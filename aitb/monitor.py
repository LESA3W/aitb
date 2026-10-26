# aitb/monitor.py
from datetime import datetime, timedelta
from pathlib import Path
import csv
import json
import time

from config.settings import CONFIG
from utils.time_logs import log
from utils.colors import colors

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

SYMBOL = CONFIG["SYMBOL"]
USE_MULTI_TP = CONFIG.get("USE_MULTI_TP", False)
MULTI_TP_CONFIG = CONFIG.get("MULTI_TP", [])

# ---------- Helpers PIP ----------
def _pip(info):
    return 10 * info.point

def _pips_between(info, price_a, price_b):
    return (price_a - price_b) / _pip(info)

def _side_from_position(pos):
    return "BUY" if pos.type == 0 else "SELL"


# ---------- Contexte & Perf (état local) ----------
_last_open_ticket = None
_last_context = None
_last_volume_tracked = {}
_position_closed_time = {}

# Distance de risque initiale par ticket, en unites de prix.
# Sert au break-even exprime en R. Sans elle on retombe sur un seuil en pips
# fixes, qui ne veut rien dire quand le stop est calcule par ATR : le backtest
# passe a BE apres 38 pips en moyenne, un seuil fixe a 6 pips couperait tout.
_risk_dist_by_ticket = {}


def note_risk_distance(ticket, risk_price_dist):
    """Enregistre le 1R du trade au moment de son ouverture."""
    try:
        if ticket and risk_price_dist and float(risk_price_dist) > 0:
            _risk_dist_by_ticket[int(ticket)] = float(risk_price_dist)
    except Exception:
        pass


def _risk_dist_for(pos, info):
    """
    1R de la position, en unites de prix.
    Priorite a la valeur enregistree a l'ouverture. A defaut, on la deduit du
    stop courant, ce qui reste juste tant que le break-even n'a pas bouge le SL.
    """
    known = _risk_dist_by_ticket.get(int(pos.ticket))
    if known:
        return known
    if pos.sl and pos.sl > 0:
        d = abs(pos.price_open - pos.sl)
        if d > 0:
            return d
    return None


def attach_context(context: dict):
    """Stocke le contexte du prochain trade."""
    global _last_context
    _last_context = context or {}
    try:
        Path(CONFIG["REPORTS_FOLDER"]).mkdir(parents=True, exist_ok=True)
        ctx_path = Path(CONFIG.get("LOG_CONTEXT_JSONL", "reports/context.jsonl"))
        with open(ctx_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_last_context, ensure_ascii=False) + "\n")
    except Exception:
        pass

def note_open_ticket(ticket: int | None):
    """Mémorise le ticket de la position ouverte."""
    global _last_open_ticket
    _last_open_ticket = ticket
    
    if ticket and USE_MULTI_TP:
        pos = mt5.positions_get(ticket=ticket)
        if pos:
            _last_volume_tracked[ticket] = pos[0].volume


# ---------- Événements JSONL (report) ----------
def _report_path():
    Path(CONFIG["REPORTS_FOLDER"]).mkdir(parents=True, exist_ok=True)
    return str(Path(CONFIG["REPORTS_FOLDER"]) / f"report_{datetime.now().strftime('%Y%m%d')}.jsonl")

def record_event(event: dict):
    event = dict(event or {})
    event["ts"] = datetime.now().isoformat()
    try:
        with open(_report_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass

def capture_position_snapshot(tag: str = "after_send"):
    if mt5 is None:
        return
    positions = mt5.positions_get(symbol=SYMBOL) or []
    items = []
    for p in positions:
        items.append({
            "ticket": p.ticket,
            "type": int(p.type),
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "volume": p.volume,
            "profit": p.profit,
        })
    record_event({"type": "positions_snapshot", "tag": tag, "positions": items})


# ---------- Accès Position / Actions ----------
def get_open_position(symbol: str = None):
    """Position ouverte sur un symbole. Sans argument, celui de CONFIG."""
    if mt5 is None:
        return None
    positions = mt5.positions_get(symbol=symbol or SYMBOL) or []
    return positions[0] if positions else None


def get_all_open_positions():
    """Toutes les positions du compte, tous symboles. Utilise par le portefeuille."""
    if mt5 is None:
        return []
    return list(mt5.positions_get() or [])

def move_sl(ticket, new_sl, symbol: str = None):
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol or SYMBOL,
        "sl": new_sl,
        "tp": None,
        "position": ticket,
    }
    res = mt5.order_send(req)
    return res and res.retcode == mt5.TRADE_RETCODE_DONE

def close_position(ticket, volume):
    """Clôture au marché la position (volume donné)."""
    pos = get_open_position()
    if not pos:
        return False
    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    if not info or not tick:
        return False
    
    filling_mode_str = CONFIG.get("FILLING_MODE", "RETURN")
    mode_map = {
        "FOK": mt5.ORDER_FILLING_FOK,
        "IOC": mt5.ORDER_FILLING_IOC,
        "RETURN": mt5.ORDER_FILLING_RETURN,
    }
    filling_mode = mode_map.get(filling_mode_str.upper(), mt5.ORDER_FILLING_RETURN)
    
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": volume,
        "type": order_type,
        "price": round(price, info.digits),
        "deviation": 10,
        "magic": getattr(pos, "magic", 0),
        "comment": "AITB close position",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }
    res = mt5.order_send(req)
    ok = res and res.retcode == mt5.TRADE_RETCODE_DONE
    if ok:
        record_event({"type": "closed_by_monitor", "ticket": ticket, "volume": volume})
    return ok


# ---------- Monitoring temps réel (BE + Trailing) ----------
def monitor_positions_once():
    """
    A appeler regulierement : break-even + trailing sur TOUTES les positions
    ouvertes du compte, quel que soit leur symbole.

    Le mode portefeuille fait tourner plusieurs marches en parallele, donc se
    limiter a CONFIG["SYMBOL"] laisserait les autres lignes sans break-even,
    et le live divergerait du backtest sur deux tiers du book.
    """
    if mt5 is None:
        return
    for pos in get_all_open_positions():
        try:
            _manage_position(pos)
        except Exception as e:
            log(colors(f"⚠️ monitoring position {getattr(pos, 'ticket', '?')}: {e}", "yellow"))


def _manage_position(pos):
    """Break-even et trailing pour une position donnee."""
    sym = pos.symbol
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if not info or not tick:
        return

    pip = _pip(info)

    # Deux modes de break-even.
    # "R"     : seuil exprime en multiples du risque initial. C'est ce que le
    #           backtest modelise, donc le seul mode qui garde live et backtest
    #           coherents quand le stop est calcule par ATR.
    # "PIPS"  : ancien comportement, seuil fixe. Conserve pour le mode legacy.
    be_mode = str(CONFIG.get("BE_MODE", "PIPS")).upper()
    if be_mode == "R":
        risk_dist = _risk_dist_for(pos, info)
        if risk_dist is None:
            return
        be_trigger = (float(CONFIG.get("BE_TRIGGER_R", 1.0)) * risk_dist) / pip
    else:
        be_trigger = CONFIG.get("BE_TRIGGER_PIPS", 10)

    use_trailing = bool(CONFIG.get("USE_TRAILING", True))
    trail_trigger = CONFIG.get("TRAIL_TRIGGER_PIPS", 15)
    trail_dist = CONFIG.get("TRAIL_DISTANCE_PIPS", 15)

    side = _side_from_position(pos)
    price_now = tick.bid if side == "SELL" else tick.ask
    entry = pos.price_open

    raw_pips = _pips_between(info, price_now, entry)
    floating_pips = raw_pips if (side == "BUY" and price_now >= entry) or (side == "SELL" and price_now <= entry) else -abs(raw_pips)

    # 1) Break-even
    if floating_pips >= be_trigger:
        be_level = entry
        new_sl = max(pos.sl, be_level) if side == "BUY" else (min(pos.sl, be_level) if pos.sl else be_level)
        if (side == "BUY" and (new_sl > pos.sl or pos.sl == 0.0)) or (side == "SELL" and (new_sl < pos.sl or pos.sl == 0.0)):
            ok = move_sl(pos.ticket, round(new_sl, info.digits), symbol=sym)
            if ok:
                log(colors(f"🔒 {sym} Break-even → SL @ {new_sl:.{info.digits}f} (+{be_trigger:.1f} pips)", "cyan"))
                record_event({"type": "break_even_set", "ticket": pos.ticket, "sl": new_sl})

    # 2) Trailing
    if use_trailing and floating_pips >= trail_trigger:
        if side == "BUY":
            target_sl = price_now - trail_dist * pip
            if target_sl > pos.sl:
                ok = move_sl(pos.ticket, round(target_sl, info.digits), symbol=sym)
                if ok:
                    log(colors(f"📈 {sym} Trailing (BUY) → SL @ {target_sl:.{info.digits}f} (~{trail_dist} pips)", "blue"))
                    record_event({"type": "trailing_set", "ticket": pos.ticket, "sl": target_sl})
        else:  # SELL
            target_sl = price_now + trail_dist * pip
            if target_sl < pos.sl or pos.sl == 0.0:
                ok = move_sl(pos.ticket, round(target_sl, info.digits), symbol=sym)
                if ok:
                    log(colors(f"📉 {sym} Trailing (SELL) → SL @ {target_sl:.{info.digits}f} (~{trail_dist} pips)", "blue"))
                    record_event({"type": "trailing_set", "ticket": pos.ticket, "sl": target_sl})


# ---------- Perf tracker : enregistre à la clôture ----------
def _init_perf_csv():
    Path(CONFIG["REPORTS_FOLDER"]).mkdir(parents=True, exist_ok=True)
    csv_path = Path(CONFIG.get("PERF_CSV", "reports/perf.csv"))
    if not csv_path.exists():
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "time_entry", "time_exit", "side", "volume",
                "entry_price", "exit_price",
                "pnl_eur", "pnl_pips",
                "symbol",
                "cycle_ts", "screenshots", "spread_pips", "session", "notes"
            ])

def _append_perf_row(row: dict):
    _init_perf_csv()
    with open(CONFIG.get("PERF_CSV", "reports/perf.csv"), "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        ctx = row.get("context") or {}
        w.writerow([
            row.get("time_entry"), row.get("time_exit"), row.get("side"), row.get("volume"),
            row.get("entry_price"), row.get("exit_price"),
            row.get("pnl_eur"), row.get("pnl_pips"),
            row.get("symbol", SYMBOL),
            ctx.get("cycle_ts", ""),
            ";".join(ctx.get("screens", [])),
            ctx.get("spread_pips", ""),
            ctx.get("session", ""),
            ctx.get("notes", ""),
        ])

def _fetch_closed_trade_result(position_ticket: int, retry_count: int = 0):
    """
    Récupère le deal de clôture correspondant à position_ticket.
    Ajoute des tentatives avec délai si échec initial
    """
    if mt5 is None or not position_ticket:
        return None

    if retry_count == 0:
        time.sleep(0.5)

    time_to = datetime.now()
    time_from = time_to - timedelta(days=3)
    deals = mt5.history_deals_get(time_from, time_to) or []
    
    if not deals:
        if retry_count < 3:
            log(colors(f"⏳ Tentative {retry_count + 1}/3 : Attente des deals...", "yellow"))
            time.sleep(1)
            return _fetch_closed_trade_result(position_ticket, retry_count + 1)
        return None

    info = mt5.symbol_info(SYMBOL)
    if not info:
        return None

    entry_price = None
    exit_price = None
    entry_time = None
    exit_time = None
    side = None
    volume = 0.0
    pnl_eur = 0.0

    for d in deals:
        if getattr(d, "position_id", 0) != position_ticket:
            continue
        if d.entry == mt5.DEAL_ENTRY_IN:
            entry_price = d.price
            entry_time = datetime.fromtimestamp(d.time)
            if d.type == mt5.DEAL_TYPE_BUY:
                side = "BUY"
            elif d.type == mt5.DEAL_TYPE_SELL:
                side = "SELL"
            volume += d.volume
        if d.entry == mt5.DEAL_ENTRY_OUT:
            exit_price = d.price
            exit_time = datetime.fromtimestamp(d.time)
            pnl_eur += d.profit

    if entry_price is None or exit_price is None:
        if retry_count < 3:
            log(colors(f"⏳ Tentative {retry_count + 1}/3 : Données incomplètes, nouvelle tentative...", "yellow"))
            time.sleep(1)
            return _fetch_closed_trade_result(position_ticket, retry_count + 1)
        return None

    pnl_pips = _pips_between(info, exit_price, entry_price)
    if side == "SELL":
        pnl_pips = -pnl_pips

    return {
        "time_entry": entry_time.isoformat() if entry_time else "",
        "time_exit": exit_time.isoformat() if exit_time else "",
        "side": side or "",
        "volume": volume,
        "entry_price": round(entry_price, info.digits),
        "exit_price": round(exit_price, info.digits),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_pips": round(pnl_pips, 1),
        "symbol": SYMBOL,
    }


def monitor_closed_positions_once():
    """
    ✅ CORRIGÉ : Détecte les fermetures partielles ET totales avec log_tp_hit()
    Ne tente de récupérer les deals QUE si la position est vraiment complètement fermée
    """
    if mt5 is None:
        return

    global _last_open_ticket, _last_context, _last_volume_tracked, _position_closed_time
    if not _last_open_ticket:
        return

    # Vérifier si position existe encore
    pos = mt5.positions_get(ticket=_last_open_ticket)
    
    if pos and len(pos) > 0:
        # Position encore ouverte
        current_volume = pos[0].volume
        
        # Détecter fermeture partielle (multi-TP)
        if USE_MULTI_TP and _last_open_ticket in _last_volume_tracked:
            previous_volume = _last_volume_tracked[_last_open_ticket]
            
            if current_volume < previous_volume - 0.001:  # Marge de 0.001 pour erreurs d'arrondi
                # Fermeture partielle détectée !
                closed_volume = previous_volume - current_volume
                
                # Calculer le profit de cette fermeture partielle
                tick = mt5.symbol_info_tick(SYMBOL)
                if tick:
                    info = mt5.symbol_info(SYMBOL)
                    side = _side_from_position(pos[0])
                    current_price = tick.bid if side == "SELL" else tick.ask
                    pips_profit = _pips_between(info, current_price, pos[0].price_open)
                    if side == "SELL":
                        pips_profit = -pips_profit
                    
                    log(colors(f"🎯 Fermeture partielle détectée : {closed_volume:.2f} lot @ {current_price:.5f} (+{pips_profit:.1f} pips)", "green"))
                    
                    # ✅ DÉTERMINER QUEL TP A ÉTÉ HIT
                    tp_level = None
                    if USE_MULTI_TP and MULTI_TP_CONFIG:
                        # Trouver quel niveau de TP correspond à ces pips
                        for i, tp_config in enumerate(MULTI_TP_CONFIG, 1):
                            tp_pips = float(tp_config.get("pips", 0))
                            # Marge de 20% pour la détection
                            if abs(pips_profit - tp_pips) <= tp_pips * 0.2:
                                tp_level = i
                                break
                    
                    # ✅ Logger avec la BONNE fonction
                    try:
                        if tp_level:
                            # ✅ Utiliser log_tp_hit pour mettre à jour le flag TP
                            from aitb.logger import log_tp_hit
                            log_tp_hit(
                                position_ticket=_last_open_ticket,
                                tp_level=tp_level,
                                lot_closed=closed_volume,
                                price=current_price,
                                pips=pips_profit,
                                lot_remaining=current_volume
                            )
                        else:
                            # Fallback si TP level non déterminé
                            from aitb.logger import log_modify_trade
                            log_modify_trade(
                                position_ticket=_last_open_ticket,
                                current_price=current_price,
                                notes=f"Partial close: {closed_volume:.2f} lot @ +{pips_profit:.1f} pips",
                                context={"decision": "PARTIAL_TP"},
                                silent=False
                            )
                    except Exception as e:
                        log(colors(f"⚠️ Log HTML échoué: {e}", "yellow"))
                
                # Mettre à jour le volume tracké
                _last_volume_tracked[_last_open_ticket] = current_volume
        
        else:
            # Première fois qu'on track cette position
            if USE_MULTI_TP:
                _last_volume_tracked[_last_open_ticket] = current_volume
        
        # ✅ NE PAS CHERCHER LES DEALS tant que la position existe
        return
    
    # ✅ Position n'existe plus dans MT5
    # MAIS on doit vérifier si c'était vraiment une position complète ou juste un volume résiduel
    
    # Si on avait tracké le volume et qu'il était > 0.01, alors c'est une vraie fermeture
    if _last_open_ticket in _last_volume_tracked:
        last_known_volume = _last_volume_tracked[_last_open_ticket]
        
        # Si le dernier volume connu était < 0.01, c'était un résidu, on skip la récupération
        if last_known_volume < 0.01:
            log(colors(f"⚠️ Position #{_last_open_ticket} : Volume résiduel ({last_known_volume:.4f}), skip récupération deals", "yellow"))
            
            # Reset le suivi sans chercher les deals
            _last_open_ticket = None
            _last_context = None
            if _last_open_ticket in _last_volume_tracked:
                del _last_volume_tracked[_last_open_ticket]
            if _last_open_ticket in _position_closed_time:
                del _position_closed_time[_last_open_ticket]
            return
    
    # ✅ OK, c'est une vraie fermeture, on cherche les deals
    
    # Enregistrer le moment de la fermeture si pas déjà fait
    if _last_open_ticket not in _position_closed_time:
        _position_closed_time[_last_open_ticket] = time.time()
        log(colors(f"📊 Position #{_last_open_ticket} complètement fermée, récupération des données...", "cyan"))
    
    # Vérifier si on doit attendre plus longtemps
    elapsed = time.time() - _position_closed_time[_last_open_ticket]
    if elapsed < 2.0:
        return
    
    result = _fetch_closed_trade_result(_last_open_ticket)
    if result:
        result["context"] = _last_context or {}
        
        # 1) Enregistrer dans perf.csv
        _append_perf_row(result)
        
        # 2) Mettre à jour trade_logs.json
        try:
            from aitb.logger import log_close_trade
            
            pnl_usd = result["pnl_eur"] * 1.08
            
            # Déterminer quel TP a été touché (si multi-TP)
            tp1_hit = False
            tp2_hit = False
            tp3_hit = False
            
            if USE_MULTI_TP and MULTI_TP_CONFIG:
                pnl_pips = result["pnl_pips"]
                
                # Vérifier chaque niveau TP
                for i, tp_config in enumerate(MULTI_TP_CONFIG, 1):
                    tp_pips = float(tp_config.get("pips", 0))
                    
                    # Détection avec marge de 20%
                    if abs(pnl_pips) >= tp_pips * 0.8:
                        if i == 1:
                            tp1_hit = True
                        elif i == 2:
                            tp2_hit = True
                        elif i == 3:
                            tp3_hit = True
            
            log_close_trade(
                position_ticket=_last_open_ticket,
                exit_price=result["exit_price"],
                pnl_usd=pnl_usd,
                pnl_eur=result["pnl_eur"],
                pnl_pips=result["pnl_pips"],
                time_exit=result["time_exit"],
                symbol=result["symbol"],
                tp1_hit=tp1_hit,
                tp2_hit=tp2_hit,
                tp3_hit=tp3_hit
            )
            
            if tp1_hit or tp2_hit or tp3_hit:
                tp_levels = []
                if tp1_hit:
                    tp_levels.append("TP1")
                if tp2_hit:
                    tp_levels.append("TP2")
                if tp3_hit:
                    tp_levels.append("TP3")
                log(colors(f"✅ {'+'.join(tp_levels)} final atteint", "green"))
        
        except Exception as e:
            log(colors(f"⚠️ Erreur mise à jour trade_logs.json: {e}", "yellow"))
        
        log(colors(f"✅ Perf enregistrée: {result['pnl_pips']:.1f} pips / {result['pnl_eur']:.2f} €", "green"))
        record_event({"type": "perf_logged", "ticket": _last_open_ticket, "pnl_pips": result["pnl_pips"], "pnl_eur": result["pnl_eur"]})
    else:
        log(colors(f"⚠️ Position #{_last_open_ticket} : Impossible de retrouver les deals (deals peut-être pas encore dans l'historique)", "yellow"))
        record_event({"type": "perf_missing", "ticket": _last_open_ticket})

    # Reset le suivi
    _last_open_ticket = None
    _last_context = None
    if _last_open_ticket in _last_volume_tracked:
        del _last_volume_tracked[_last_open_ticket]
    if _last_open_ticket in _position_closed_time:
        del _position_closed_time[_last_open_ticket]