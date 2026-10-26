# aitb/trade_manager.py
import MetaTrader5 as mt5
from datetime import datetime
import threading

from config.settings import CONFIG
from utils.time_logs import log
from utils.colors import colors
from aitb.risk_manager import calc_lot

# ==================== CONFIGS DE BASE ====================
SYMBOL = CONFIG["SYMBOL"]
RISK_PCT = float(CONFIG["RISK_PCT"])
SL_PIPS = float(CONFIG["SL_PIPS"])
TP_PIPS = float(CONFIG["TP_PIPS"])
USE_TP = CONFIG["USE_TP"]
USE_SL = CONFIG["USE_SL"]
MAGIC_NUMBER = int(CONFIG["MAGIC"])
DEVIATION = int(CONFIG["DEVIATION_POINTS"])
FILLING_MODE = CONFIG["FILLING_MODE"]
TRADE_COMMENT = CONFIG.get("TRADE_COMMENT", "AITB")

# Multi-TP (lit directement depuis CONFIG - valeur par défaut False si absent)
# ✅ LA VRAIE CONFIG EST DANS settings.py : "USE_MULTI_TP": True
USE_MULTI_TP = CONFIG.get("USE_MULTI_TP", False)  # False = fallback si clé manquante
MULTI_TP_CONFIG = CONFIG.get("MULTI_TP", [])  # Liste de dict depuis settings.py

# Protections
MAX_OPEN_POSITIONS = int(CONFIG["MAX_OPEN_POSITIONS"])
DAILY_DRAWDOWN_LIMIT_PCT = float(CONFIG["DAILY_DRAWDOWN_LIMIT_PCT"])
ONE_POSITION_RULE = CONFIG["ONE_POSITION_RULE"]
COOLDOWN_MINUTES = float(CONFIG["COOLDOWN_MINUTES"])

# Tracking état
_last_trade_time = None
_daily_start_balance = None

# ✅ Stockage des managers multi-TP actifs
_active_tp_managers = {}  # {ticket: MultiTPManager}


# ==================== INIT MT5 ====================
def _charger_env():
    """
    Lit un fichier .env a la racine du projet, sans dependance externe.

    Les identifiants d'un compte prop firm ne doivent jamais vivre dans
    settings.py : ce fichier part dans les sauvegardes et les copies de projet.
    """
    import os
    from pathlib import Path as _P
    f = _P(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return {}
    out = {}
    for ligne in f.read_text(encoding="utf-8", errors="replace").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        k, v = ligne.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in out.items():
        os.environ.setdefault(k, v)
    return out


def _identifiants():
    """
    (login, password, server, path) ou None si rien n'est configure.

    Sans identifiants, on garde le comportement historique : on se branche sur
    le compte deja ouvert dans le terminal. C'est ce qui fait tourner la demo
    aujourd'hui, il ne faut pas le casser.
    """
    import os
    env = _charger_env()
    src = {**env, **{k: v for k, v in os.environ.items() if k.startswith(("MT5_", "FTMO_"))}}
    login = src.get("FTMO_LOGIN") or src.get("MT5_LOGIN")
    mdp = src.get("FTMO_PASSWORD") or src.get("MT5_PASSWORD")
    serveur = src.get("FTMO_SERVER") or src.get("MT5_SERVER")
    chemin = src.get("FTMO_PATH") or src.get("MT5_PATH") or CONFIG.get("MT5_TERMINAL_PATH")
    if not (login and mdp and serveur):
        return None
    try:
        return int(login), mdp, serveur, chemin
    except ValueError:
        log(colors(f"⚠️ FTMO_LOGIN doit etre un nombre, recu: {login!r}", "yellow"))
        return None


def initialize_mt5():
    """Init MT5 + sélection du symbole + démarrage monitoring multi-TP."""
    ids = _identifiants()
    if ids:
        login, mdp, serveur, chemin = ids
        kwargs = {"login": login, "password": mdp, "server": serveur}
        if chemin:
            kwargs["path"] = chemin
        ok = mt5.initialize(**kwargs)
        if not ok:
            log(colors(f"❌ Connexion au compte {login} sur {serveur} refusee: "
                       f"{mt5.last_error()}", "red"))
            return False
        log(colors(f"🔑 Connecte au compte {login} @ {serveur}", "cyan"))
    elif not mt5.initialize():
        log(colors("❌ Erreur d'initialisation de MetaTrader 5", "red"))
        return False
    if not mt5.symbol_select(SYMBOL, True):
        log(colors(f"❌ Symbole {SYMBOL} non sélectionné", "red"))
        return False
    
    # Initialiser balance du jour
    global _daily_start_balance
    if _daily_start_balance is None:
        acc = mt5.account_info()
        if acc:
            _daily_start_balance = acc.balance
    
    # ✅ Démarrer le monitoring multi-TP en thread si activé
    if USE_MULTI_TP:
        monitor_thread = threading.Thread(
            target=_run_multi_tp_monitor,
            daemon=True,
            name="MultiTPMonitor"
        )
        monitor_thread.start()
        log(colors("✅ Monitoring Multi-TP démarré en arrière-plan", "green"))
    
    return True


# ==================== PROTECTIONS ESSENTIELLES ====================
def check_daily_drawdown():
    """Kill-switch: arrête trading si drawdown > limite."""
    acc = mt5.account_info()
    if not acc:
        return False
    
    global _daily_start_balance
    if _daily_start_balance is None:
        _daily_start_balance = acc.balance
    
    balance_start = _daily_start_balance
    equity_now = acc.equity
    dd_pct = ((balance_start - equity_now) / balance_start) * 100
    
    if dd_pct >= DAILY_DRAWDOWN_LIMIT_PCT:
        loss_usd = balance_start - equity_now
        log(colors(f"🛑 KILL-SWITCH: Drawdown {dd_pct:.2f}% ({loss_usd:.2f} USD) > {DAILY_DRAWDOWN_LIMIT_PCT}%", "red"))
        return True
    
    return False


def check_position_limits(sym: str = None):
    """
    Verifie ONE_POSITION_RULE et MAX_OPEN_POSITIONS.

    ONE_POSITION_RULE s'applique PAR SYMBOLE : en mode portefeuille on veut une
    position sur EURUSD et une sur USDJPY en meme temps, mais jamais deux sur
    le meme symbole. MAX_OPEN_POSITIONS, lui, plafonne le total du compte.
    """
    sym = sym or SYMBOL
    positions_symbol = mt5.positions_get(symbol=sym)
    if positions_symbol is None:
        return False

    nb_symbol = len(positions_symbol)
    nb_positions = len(mt5.positions_get() or [])

    if ONE_POSITION_RULE and nb_symbol > 0:
        log(colors(f"⚠️ ONE_POSITION_RULE: {nb_symbol} position(s) sur {sym} — trade bloqué", "yellow"))
        return True

    if nb_positions >= MAX_OPEN_POSITIONS:
        log(colors(f"⚠️ {nb_positions} positions ≥ limite {MAX_OPEN_POSITIONS} — trade bloqué", "yellow"))
        return True
    
    return False


def check_cooldown():
    """Cooldown: empêche trade si < COOLDOWN_MINUTES depuis dernier ordre."""
    global _last_trade_time
    if _last_trade_time is None:
        return False
    
    now = datetime.now()
    elapsed_minutes = (now - _last_trade_time).total_seconds() / 60
    
    if elapsed_minutes < COOLDOWN_MINUTES:
        remaining = COOLDOWN_MINUTES - elapsed_minutes
        log(colors(f"⏳ Cooldown: {remaining:.1f} min restantes", "yellow"))
        return True
    
    return False


# ==================== UTILS PRIX/FILLING ====================
def _symbol_info(sym=SYMBOL):
    si = mt5.symbol_info(sym)
    if not si:
        raise RuntimeError(f"symbol_info({sym}) is None")
    if not si.visible:
        mt5.symbol_select(sym, True)
    return si


def _get_price(is_buy: bool, sym=SYMBOL):
    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        raise RuntimeError("symbol_info_tick returned None")
    return tick.ask if is_buy else tick.bid


def _get_filling_mode(sym: str = None):
    """
    Mode de remplissage reellement accepte par le broker pour ce symbole.

    On demande d'abord celui de CONFIG. S'il n'est pas dans le masque de bits
    renvoye par le symbole, on retombe sur un mode supporte au lieu de se faire
    rejeter par un retcode 10030 opaque.

    BUG CORRIGE LE 2026-09-10 : CONFIG impose IOC, ce qui est correct chez
    IC Markets RAW, mais MetaQuotes-Demo n'accepte que FOK. Tous les ordres
    etaient rejetes. Detecte des le premier signal du forward test.
    """
    mode_map = {
        "FOK": mt5.ORDER_FILLING_FOK,
        "IOC": mt5.ORDER_FILLING_IOC,
        "RETURN": mt5.ORDER_FILLING_RETURN,
    }
    wanted = mode_map.get(FILLING_MODE.upper())
    if wanted is None:
        raise ValueError(f"FILLING_MODE invalide: {FILLING_MODE}")

    info = mt5.symbol_info(sym or SYMBOL)
    mask = getattr(info, "filling_mode", 0) if info else 0
    if not mask:
        return wanted

    # Bits du masque : 1 = FOK, 2 = IOC, 4 = RETURN.
    supported = {"FOK": bool(mask & 1), "IOC": bool(mask & 2), "RETURN": bool(mask & 4)}
    if supported.get(FILLING_MODE.upper()):
        return wanted

    for name in ("IOC", "FOK", "RETURN"):
        if supported.get(name):
            return mode_map[name]
    return wanted


def _price_tp_sl(is_buy: bool, sym: str = None, sl_pips: float = None, tp_pips: float = None):
    """
    Calcule price/TP/SL en respectant stops_level.

    sl_pips et tp_pips permettent au runner headless de passer une distance
    calculee dynamiquement (ATR du moment) au lieu des constantes de CONFIG.
    Laisses a None, le comportement est strictement identique a avant.
    """
    sym = sym or SYMBOL
    info = _symbol_info(sym)
    price = _get_price(is_buy, sym)
    pip = 10 * info.point

    # SL_PIPS / TP_PIPS valent 3 et 5, heritage du scalping de l'ancienne archi.
    # Le book actuel utilise des stops de 38 a 165 pips calcules sur l'ATR. Si un
    # appel arrivait sans stop, on enverrait un ordre a 3 pips : il sauterait
    # immediatement. Mieux vaut refuser l'ordre que d'en envoyer un absurde.
    if sl_pips is None or tp_pips is None:
        log(colors("❌ Ordre refuse: SL ou TP absent. Le repli historique "
                   f"({SL_PIPS}/{TP_PIPS} pips) ne convient pas a ce book.", "red"))
        return None
    sl_eff = float(sl_pips)
    tp_eff = float(tp_pips)

    # ✅ Si multi-TP activé, on ne met PAS de TP sur l'ordre initial
    # Le système multi-TP gère les fermetures partielles automatiquement
    tp = None
    if USE_TP and not USE_MULTI_TP:
        tp_delta = tp_eff * pip
        tp = price + tp_delta if is_buy else price - tp_delta

    # SL normal
    sl = None
    if USE_SL:
        sl_delta = sl_eff * pip
        sl = price - sl_delta if is_buy else price + sl_delta

    # Respect stops_level
    stops_level_pts = getattr(info, "trade_stops_level", 0) or getattr(info, "stops_level", 0) or 0
    min_dist = stops_level_pts * info.point

    if min_dist > 0:
        if tp is not None:
            if is_buy and (tp - price) < min_dist:
                tp = price + min_dist
            elif not is_buy and (price - tp) < min_dist:
                tp = price - min_dist
        
        if sl is not None:
            if is_buy and (price - sl) < min_dist:
                sl = price - min_dist
            elif not is_buy and (sl - price) < min_dist:
                sl = price + min_dist

    d = info.digits
    price_rounded = round(price, d)
    tp_rounded = round(tp, d) if tp is not None else None
    sl_rounded = round(sl, d) if sl is not None else None
    
    return price_rounded, tp_rounded, sl_rounded


def _send_order(request):
    """Envoie l'ordre avec le FILLING_MODE configuré."""
    check = mt5.order_check(request)
    if check is None:
        return None, "order_check returned None"
    
    if check.retcode != mt5.TRADE_RETCODE_DONE and check.retcode != 0:
        error_msg = f"order_check failed: retcode={check.retcode}"
        if hasattr(check, 'comment'):
            error_msg += f", comment={check.comment}"
        return None, error_msg
    
    result = mt5.order_send(request)
    if result is None:
        return None, "order_send returned None"
    
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        return result, None
    
    return result, f"retcode={result.retcode}"


# ==================== MULTI-TP MONITORING ====================
def _run_multi_tp_monitor():
    """
    Thread de monitoring qui vérifie toutes les 2 secondes si les TP doivent être déclenchés.
    Tourne en permanence en arrière-plan.
    """
    import time
    
    log(colors("🎯 Multi-TP Monitor thread démarré", "cyan"))
    
    while True:
        try:
            # Scan des positions ouvertes
            positions = mt5.positions_get(symbol=SYMBOL, magic=MAGIC_NUMBER)
            if not positions:
                time.sleep(CONFIG.get("MONITOR_INTERVAL_SECONDS", 2))
                continue
            
            for position in positions:
                ticket = position.ticket
                
                # Créer le manager si pas encore créé
                if ticket not in _active_tp_managers:
                    _active_tp_managers[ticket] = _create_tp_manager(position)
                
                # Vérifier et exécuter les TP
                manager = _active_tp_managers[ticket]
                if manager:
                    result = manager.check_and_execute()
                    
                    # Si TP déclenché, logger
                    if result.get("triggered"):
                        _log_tp_hit(ticket, result)
            
            # Nettoyer les managers des positions fermées
            current_tickets = {p.ticket for p in positions}
            closed_tickets = set(_active_tp_managers.keys()) - current_tickets
            for ticket in closed_tickets:
                del _active_tp_managers[ticket]
            
            time.sleep(CONFIG.get("MONITOR_INTERVAL_SECONDS", 2))
        
        except Exception as e:
            log(colors(f"⚠️ Erreur monitoring multi-TP: {e}", "yellow"))
            time.sleep(5)


def _create_tp_manager(position):
    """Crée un gestionnaire multi-TP pour une position."""
    try:
        # Créer une classe TPManager simplifiée inline
        class TPManager:
            def __init__(self, pos):
                self.ticket = pos.ticket
                self.initial_volume = pos.volume
                self.entry_price = pos.price_open
                self.is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
                
                info = mt5.symbol_info(SYMBOL)
                pip = 10 * info.point
                
                # ✅ Lire les niveaux TP depuis MULTI_TP_CONFIG
                self.tp_levels = []
                for tp_config in MULTI_TP_CONFIG:
                    pips = float(tp_config.get("pips", 0))
                    close_pct = float(tp_config.get("close_pct", 0))
                    
                    if pips <= 0 or close_pct <= 0:
                        continue
                    
                    price_target = pos.price_open + (pips * pip) if self.is_buy else pos.price_open - (pips * pip)
                    
                    self.tp_levels.append({
                        "pips": pips,
                        "close_pct": close_pct,
                        "price_target": price_target,
                        "triggered": False
                    })
                
                # Trier par distance croissante
                self.tp_levels.sort(key=lambda x: x["pips"])
            
            def check_and_execute(self):
                # Récupérer position actuelle
                positions = mt5.positions_get(ticket=self.ticket)
                if not positions:
                    return {"triggered": False}
                
                pos = positions[0]
                tick = mt5.symbol_info_tick(SYMBOL)
                if not tick:
                    return {"triggered": False}
                
                current_price = tick.bid if self.is_buy else tick.ask
                
                # Vérifier chaque TP
                for i, tp in enumerate(self.tp_levels, 1):
                    if tp["triggered"]:
                        continue
                    
                    # Prix atteint ?
                    tp_reached = False
                    if self.is_buy:
                        tp_reached = (current_price >= tp["price_target"])
                    else:
                        tp_reached = (current_price <= tp["price_target"])
                    
                    if tp_reached:
                        return self._close_partial(tp, i, current_price, pos)
                
                return {"triggered": False}
            
            def _close_partial(self, tp_level, tp_number, price, position):
                # Calculer volume à fermer
                volume_to_close = (self.initial_volume * tp_level["close_pct"]) / 100.0
                
                info = mt5.symbol_info(SYMBOL)
                lot_step = info.volume_step
                volume_to_close = round(volume_to_close / lot_step) * lot_step
                
                # Vérifier volume minimum
                if volume_to_close < info.volume_min:
                    tp_level["triggered"] = True
                    return {"triggered": False, "error": "volume_too_small"}
                
                # Ne pas fermer plus que le volume restant
                if volume_to_close > position.volume:
                    volume_to_close = position.volume
                
                # Construire requête de fermeture partielle
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": SYMBOL,
                    "volume": volume_to_close,
                    "type": mt5.ORDER_TYPE_SELL if self.is_buy else mt5.ORDER_TYPE_BUY,
                    "position": self.ticket,
                    "price": price,
                    "deviation": DEVIATION,
                    "magic": MAGIC_NUMBER,
                    "comment": f"TP{tp_number} ({tp_level['pips']}p)",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": _get_filling_mode(),
                }
                
                result = mt5.order_send(request)
                
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    tp_level["triggered"] = True
                    remaining = position.volume - volume_to_close
                    
                    return {
                        "triggered": True,
                        "tp_level": tp_number,
                        "closed_volume": volume_to_close,
                        "remaining_volume": remaining,
                        "price": price,
                        "pips": tp_level["pips"],
                        "pct": tp_level["close_pct"]
                    }
                else:
                    return {"triggered": False, "error": f"retcode={result.retcode if result else 'None'}"}
        
        return TPManager(position)
    
    except Exception as e:
        log(colors(f"⚠️ Erreur création TPManager: {e}", "yellow"))
        return None


def _log_tp_hit(ticket, result):
    """Log quand un TP est touché."""
    tp_level = result.get("tp_level")
    closed_vol = result.get("closed_volume")
    remaining_vol = result.get("remaining_volume")
    pips = result.get("pips")
    price = result.get("price")
    
    log(colors(f"✅ TP{tp_level} HIT: #{ticket} | Fermé {closed_vol} lot @ {price:.5f} (+{pips} pips) | Reste {remaining_vol} lot", "green"))
    
    # Logger dans HTML
    try:
        from aitb.logger import log_modify_trade
        log_modify_trade(
            position_ticket=ticket,
            current_price=price,
            notes=f"TP{tp_level} hit: {closed_vol} lot @ +{pips} pips",
            context={"decision": f"TP{tp_level}"}
        )
    except Exception as e:
        log(colors(f"⚠️ Log HTML échoué: {e}", "yellow"))


# ==================== API PRINCIPALE ====================
def open_trade(action: str, context: dict = None,
               symbol: str = None, sl_pips: float = None, tp_pips: float = None,
               risk_pct: float = None):
    """
    Ouvre un trade BUY/SELL avec protections et multi-TP.

    Args:
        action: "BUY" ou "SELL"
        context: Contexte du trade (cycle, screenshots, etc.)
        symbol: force un autre symbole que CONFIG["SYMBOL"]
        sl_pips: distance de stop calculee dynamiquement, ex: 2 x ATR converti en pips
        tp_pips: distance de take profit, ignoree si USE_MULTI_TP
        risk_pct: risque par trade, sinon CONFIG["RISK_PCT"]

    Tous les arguments optionnels laisses a None reproduisent exactement
    le comportement historique.
    """
    action = action.upper()
    if action not in ("BUY", "SELL"):
        log(colors("❌ Action invalide", "red"))
        return None

    sym = symbol or SYMBOL
    sl_eff = SL_PIPS if sl_pips is None else float(sl_pips)
    risk_eff = RISK_PCT if risk_pct is None else float(risk_pct)

    # Protections
    if check_daily_drawdown():
        return None
    if check_position_limits(sym):
        return None
    if check_cooldown():
        return None

    # Calcul lot dynamique
    acc = mt5.account_info()
    if not acc:
        log(colors("❌ Impossible de récupérer account_info", "red"))
        return None

    lot = calc_lot(acc.balance, risk_eff, sl_eff, sym)
    if not lot or lot <= 0:
        log(colors(f"⛔ Lot nul pour {sym}, trade refusé", "red"))
        return None

    # Vérif symbole
    info = _symbol_info(sym)
    if info.trade_mode in (mt5.SYMBOL_TRADE_MODE_DISABLED, mt5.SYMBOL_TRADE_MODE_CLOSEONLY):
        log(colors(f"❌ {sym} trading désactivé", "red"))
        return None

    # Prix/TP/SL
    is_buy = (action == "BUY")
    try:
        price, tp, sl = _price_tp_sl(is_buy, sym=sym, sl_pips=sl_pips, tp_pips=tp_pips)
    except Exception as e:
        log(colors(f"❌ Prix indisponibles: {e}", "red"))
        return None

    filling_mode = _get_filling_mode(sym)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl if sl is not None else 0.0,
        "tp": tp if tp is not None else 0.0,
        "deviation": DEVIATION,
        "magic": MAGIC_NUMBER,
        "comment": TRADE_COMMENT,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    # Log adapté multi-TP
    if USE_MULTI_TP and MULTI_TP_CONFIG:
        tp_levels = [f"{tp['pips']}p({tp['close_pct']}%)" for tp in MULTI_TP_CONFIG]
        log(colors(f"💰 Calcul lot: Capital={acc.balance:.2f} | Risque={risk_eff}% ({acc.balance * risk_eff / 100:.2f} USD) | SL={sl_eff} pips", "cyan"))
        log(colors(f"📊 Pip value=10.00 USD/lot | Lot brut={lot:.4f} → Lot final={lot:.2f}", "cyan"))
        log(colors(f"🎯 {action} @ {price:.5f} | Multi-TP: {' → '.join(tp_levels)} | SL: {sl:.5f} | Lot: {lot}", "cyan"))
    else:
        tp_text = f"TP: {tp:.5f}" if tp is not None else "TP: OFF"
        sl_text = f"SL: {sl:.5f}" if sl is not None else "SL: OFF"
        log(colors(f"🎯 {action} @ {price:.5f} | {tp_text} | {sl_text} | Lot: {lot}", "cyan"))

    # Envoi
    result, err = _send_order(request)

    if err is None and result:
        log(colors(f"✅ {action} EXECUTED : #{result.order}", "green"))
        
        global _last_trade_time
        _last_trade_time = datetime.now()
        
        # ✅ CORRECTION : Log HTML avec tp1/tp2/tp3
        try:
            from aitb.logger import log_open_trade
            ctx = context or {}
            
            # ✅ Calculer les 3 TP pour le logger si multi-TP
            if USE_MULTI_TP and MULTI_TP_CONFIG:
                tp1_val = None
                tp2_val = None
                tp3_val = None
                
                pip = 10 * info.point
                
                for i, tp_config in enumerate(MULTI_TP_CONFIG, 1):
                    pips = float(tp_config.get("pips", 0))
                    if pips <= 0:
                        continue
                    
                    price_target = price + (pips * pip) if is_buy else price - (pips * pip)
                    
                    if i == 1:
                        tp1_val = price_target
                    elif i == 2:
                        tp2_val = price_target
                    elif i == 3:
                        tp3_val = price_target
                
                log_open_trade(
                    position_ticket=result.order,
                    order_id=getattr(result, "order", None),
                    side=action,
                    volume=lot,
                    entry_price=price,
                    sl=sl,
                    tp1=tp1_val,
                    tp2=tp2_val,
                    tp3=tp3_val,
                    context={
                        "symbol": sym,
                        "cycle_number": ctx.get("cycle_number", 1),
                        "decision": action,
                        "screens": ctx.get("screens", []),
                    }
                )
            else:
                log_open_trade(
                    position_ticket=result.order,
                    order_id=getattr(result, "order", None),
                    side=action,
                    volume=lot,
                    entry_price=price,
                    sl=sl,
                    tp1=tp,  # ✅ Utiliser tp1 au lieu de tp
                    tp2=None,
                    tp3=None,
                    context={
                        "symbol": sym,
                        "cycle_number": ctx.get("cycle_number", 1),
                        "decision": action,
                        "screens": ctx.get("screens", []),
                    }
                )
        except Exception as e:
            log(colors(f"⚠️ Log HTML échoué: {e}", "yellow"))

        return result

    # Gestion erreurs
    if result:
        rc = result.retcode
        if rc == mt5.TRADE_RETCODE_INVALID_FILL:
            log(colors(f"⛔ Filling mode '{FILLING_MODE}' non supporté (10030)", "red"))
        elif rc in (mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_OFF, mt5.TRADE_RETCODE_PRICE_CHANGED):
            log(colors(f"⚠️ Prix a bougé (retcode {rc})", "yellow"))
        elif rc == mt5.TRADE_RETCODE_INVALID_STOPS:
            log(colors("⛔ Stops invalides", "red"))
        elif rc == mt5.TRADE_RETCODE_MARKET_CLOSED:
            log(colors("⛔ Marché fermé (10018)", "red"))
        elif rc == mt5.TRADE_RETCODE_NO_MONEY:
            log(colors("⛔ Fonds insuffisants (10019)", "red"))
        else:
            log(colors(f"⛔ MT5 error {rc}", "red"))
        
        if hasattr(result, "comment") and result.comment:
            log(colors(f"ℹ️ {result.comment}", "yellow"))
    else:
        log(colors(f"❌ Envoi échoué: {err}", "red"))

    return None


# ==================== UTILS ====================
def reset_daily_balance():
    """Réinitialise la balance de référence (à appeler en début de journée)"""
    global _daily_start_balance
    acc = mt5.account_info()
    if acc:
        _daily_start_balance = acc.balance


def get_account_status():
    """Retourne l'état complet du compte"""
    acc = mt5.account_info()
    if not acc:
        return None
    
    positions = mt5.positions_get(symbol=SYMBOL)
    nb_positions = len(positions) if positions else 0
    
    global _daily_start_balance
    if _daily_start_balance is None:
        _daily_start_balance = acc.balance
    
    dd_pct = (((_daily_start_balance - acc.equity) / _daily_start_balance) * 100) if _daily_start_balance > 0 else 0
    
    return {
        "balance": acc.balance,
        "equity": acc.equity,
        "margin": acc.margin,
        "free_margin": acc.margin_free,
        "leverage": acc.leverage,
        "profit": acc.profit,
        "daily_drawdown_pct": dd_pct,
        "nb_positions": nb_positions,
    }