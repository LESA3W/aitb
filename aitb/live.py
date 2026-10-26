# aitb/live.py
"""
Runner live headless.

Remplace la chaine screenshot -> MiniMouseMacro -> ChatGPT -> OCR par une
lecture directe des bougies via l'API MT5. Consequences :

  - aucun mouvement de souris, aucune fenetre mise au premier plan
  - MT5 peut rester reduit, tu utilises ton PC normalement pendant que ca tourne
  - transportable tel quel sur un VPS Windows, sans aucune modification
  - la decision est deterministe, donc reproductible et backtestable

Le bot REFUSE de demarrer tant qu'aucune strategie validee n'est declaree dans
CONFIG["LIVE_STRATEGY"]. C'est volontaire : une strategie qui n'a pas passe la
grille d'acceptation puis un forward test n'a rien a faire sur un compte reel.

Lancement:
    python -m aitb.live            trading selon CONFIG
    python -m aitb.live --dry-run  calcule les signaux sans envoyer d'ordre
    python -m aitb.live --status   diagnostic de connexion puis sortie
"""

import json
import sys
import time
from datetime import datetime

import numpy as np

from config.settings import CONFIG
from utils.time_logs import log
from utils.colors import colors

from aitb import control
from aitb.propfirm_guard import (
    PropFirmGuard, FERMER_TOUT, STOP_ENTREES)
from aitb import market_data as md
from aitb import strategies as st
from aitb.trade_manager import initialize_mt5, open_trade

try:
    from aitb.filters import validate_signal, remember_trade_time
except Exception:
    validate_signal = None
    remember_trade_time = None

try:
    from aitb.monitor import (
        monitor_positions_once, monitor_closed_positions_once,
        attach_context, note_open_ticket, get_open_position, record_event,
        note_risk_distance,
    )
except Exception:
    monitor_positions_once = None
    monitor_closed_positions_once = None
    attach_context = None
    note_open_ticket = None
    get_open_position = None
    record_event = None
    note_risk_distance = None

try:
    from aitb.logger import refresh_open_rows_from_mt5, init_logs_txt
except Exception:
    refresh_open_rows_from_mt5 = None
    init_logs_txt = None


SYMBOL = CONFIG["SYMBOL"]
TIMEFRAME = CONFIG.get("LIVE_TIMEFRAME", "M15")
BARS = int(CONFIG.get("LIVE_BARS", 500))
TP_R = float(CONFIG.get("LIVE_TP_R", 2.0))
MIN_SL_PIPS = float(CONFIG.get("LIVE_MIN_SL_PIPS", 3.0))
MAX_SL_PIPS = float(CONFIG.get("LIVE_MAX_SL_PIPS", 60.0))
POLL_SECONDS = int(CONFIG.get("LIVE_POLL_SECONDS", 5))
MONITOR_EVERY = int(CONFIG.get("MONITOR_INTERVAL_SECONDS", 2))


_GARDE = None


def _garde_propfirm():
    """Instance unique du garde-fou, ou None si le mode prop firm est coupe."""
    global _GARDE
    if _GARDE is None:
        g = PropFirmGuard(CONFIG)
        _GARDE = g if g.actif else False
    return _GARDE or None


def _controle_coherence(balance: float, book=None):
    """
    Verifie que la config est coherente avec la TAILLE DU COMPTE.

    `book` est le portefeuille REELLEMENT retenu apres le tri de portabilite.
    Sans lui, le controle rejouait toutes les lignes declarees, y compris
    celles deja ecartees, et signalait une incoherence sur une ligne qui ne
    tradera jamais. Une alerte qui crie pour rien fait ignorer les vraies.

    Origine : le 2026-09-11, MAX_LOT etait fige a 0.10, valeur calibree pour la
    demo de 1000 USD. Sur un compte de 160 000 EUR, toutes les entrees auraient
    ete ecrasees et le bot aurait risque un tiers de ce qu'il devait, sans une
    seule ligne d'alerte. Ce controle rend ce type d'erreur impossible a rater :
    il compare le lot theorique au lot reellement envoye, pour chaque ligne.
    """
    from aitb.risk_manager import calc_lot, max_lot_for
    import MetaTrader5 as mt5

    risque = float(CONFIG.get("RISK_PCT", 1.0))
    plafond = max_lot_for(balance)
    soucis = []

    log(colors(f"   controle  capital {balance:,.0f} | risque {risque}% "
               f"| plafond lot {plafond:.2f}", "white"))

    lignes = book if book is not None else CONFIG.get("LIVE_BOOK", [])
    for ligne in lignes:
        sym = ligne["symbol"]
        info = mt5.symbol_info(sym)
        if info is None:
            soucis.append(f"{sym}: symbole introuvable chez ce broker")
            continue
        # Stop median observe sur cette ligne, suffisant pour detecter un ecrasement.
        pips = 160.0 if ligne["timeframe"] == "H4" else 40.0
        vpip = (info.point * 10 / info.trade_tick_size) * info.trade_tick_value
        theorique = (balance * risque / 100.0) / (vpip * pips)
        envoye = calc_lot(balance, risque, pips, sym)
        # Un ecart d'un pas de volume est de l'arrondi, inevitable sur un petit
        # compte et sans consequence. Au-dela, c'est un plafond qui ecrase, et
        # la c'est un vrai probleme : on ne trade plus le risque qu'on croit.
        pas = float(getattr(info, "volume_step", 0.01) or 0.01)
        if theorique > 0 and (theorique - envoye) > pas * 1.5:
            soucis.append(
                "%s %s: lot theorique %.2f, envoye %.2f -> risque reel %.2f%% au lieu de %.1f%%"
                % (sym, ligne["timeframe"], theorique, envoye,
                   risque * envoye / theorique, risque))
        if envoye < info.volume_min:
            soucis.append(f"{sym}: lot {envoye:.3f} sous le minimum broker "
                          f"{info.volume_min}")

    if soucis:
        log(colors("⚠️  INCOHERENCE CONFIG / TAILLE DE COMPTE", "red"))
        for x in soucis:
            log(colors(f"      {x}", "yellow"))
    else:
        log(colors("   controle  sizing coherent sur les %d lignes retenues"
                   % len(lignes), "green"))
        if balance < 3000:
            log(colors("   controle  petit compte : l'arrondi au pas de volume "
                       "fait varier le risque reel de +/- 0.2 point", "white"))
    return not soucis


_DERNIER_STATUT_PF = None


def _surveiller_propfirm():
    """
    Verifie les limites de la firme a chaque cycle. Ne journalise qu'au
    CHANGEMENT de statut : sans ca, on ecrirait une ligne toutes les 5 secondes.
    """
    global _DERNIER_STATUT_PF
    garde = _garde_propfirm()
    if garde is None:
        return
    try:
        import MetaTrader5 as mt5
        info = mt5.account_info()
        if info is None:
            return
        v = garde.evaluer(info.equity, info.balance)
    except Exception as e:
        log(colors(f"⚠️ garde-fou prop firm: {e}", "yellow"))
        return

    if v["statut"] != _DERNIER_STATUT_PF:
        _DERNIER_STATUT_PF = v["statut"]
        if v["statut"] == STOP_ENTREES:
            log(colors("🛡️  Prop firm: %s. Plus aucune entree, les positions "
                       "ouvertes gardent leur stop." % v["raison"], "yellow"))
        elif v["statut"] == FERMER_TOUT:
            log(colors("🚨 Prop firm: %s. Fermeture de toutes les positions."
                       % v["raison"], "red"))
            _fermer_tout_propfirm()
            control.ecrire(control.PAUSE, par="propfirm_guard")
        else:
            log(colors("🛡️  Prop firm: marge revenue a la normale (%s)"
                       % v["raison"], "green"))


def _fermer_tout_propfirm():
    """
    Ferme toutes les positions au marche.

    C'est la seule situation ou le bot clot une position de lui-meme. On
    accepte de cristalliser la perte : le compte vaut plus que le flottant.
    """
    try:
        import MetaTrader5 as mt5
        for pos in (mt5.positions_get() or []):
            sens = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            prix = tick.bid if pos.type == 0 else tick.ask
            r = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL, "position": pos.ticket,
                "symbol": pos.symbol, "volume": pos.volume, "type": sens,
                "price": prix, "deviation": 30,
                "comment": "propfirm guard", "type_time": mt5.ORDER_TIME_GTC,
            })
            ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
            log(colors("   %s %s %.2f lot -> %s"
                       % ("fermee" if ok else "ECHEC", pos.symbol, pos.volume,
                          getattr(r, "retcode", "?")), "green" if ok else "red"))
    except Exception as e:
        log(colors(f"⚠️ Fermeture d'urgence impossible: {e}", "red"))


def _stop_estime(symbol, timeframe):
    """
    Stop typique de cette ligne, en pips, calcule sur l'ATR reel du marche.

    None si le calcul est impossible : l'appelant laisse alors passer la ligne
    plutot que de la refuser sur une valeur inventee.
    """
    try:
        import numpy as _np
        sl_atr = 2.0
        for l in CONFIG.get("LIVE_BOOK", []):
            if l["symbol"].upper() == symbol and l.get("timeframe", "").upper() == timeframe:
                sl_atr = float((l.get("params") or {}).get("sl_atr", 2.0))
                break
        rates, meta = md.load_history(symbol, timeframe)
        if rates is None or len(rates) < 100:
            return None
        h = rates["high"].astype(float)[-200:]
        b = rates["low"].astype(float)[-200:]
        c = rates["close"].astype(float)[-200:]
        tr = _np.maximum.reduce([h[1:] - b[1:],
                                 _np.abs(h[1:] - c[:-1]),
                                 _np.abs(b[1:] - c[:-1])])
        atr = float(_np.mean(tr[-14:]))
        pip = float(meta["pip"])
        return (atr * sl_atr) / pip if pip > 0 else None
    except Exception:
        return None


def _ligne_portable(symbol, timeframe):
    """
    (portable, raison). Une ligne est portable si le lot MINIMUM du broker
    risque moins que le budget de risque du compte.

    SWI20 a un lot minimum de 1.0 : sur un stop de 700 points cela fait 85,73 $
    de risque, soit 8,57% d'un compte de 1000 $ pour un reglage a 1%. La ligne
    n'est pas tradable sur ce compte, et l'ecraser vers le lot minimum ferait
    tourner la strategie a huit fois le risque prevu.
    """
    try:
        import MetaTrader5 as _m5
        info = _m5.account_info()
        if info is None:
            return True, ""
        _m5.symbol_select(symbol, True)
        si = _m5.symbol_info(symbol)
        if si is None:
            return False, "symbole absent chez ce broker"
        # Un symbole peut exister et avoir le trading DESACTIVE chez ce broker
        # (retcode 10017). USTECH100M est dans ce cas chez MetaQuotes. La ligne
        # doit etre ecartee ici plutot que de produire des ordres refuses en
        # boucle. Elle sera reprise automatiquement chez un broker qui l'active.
        mode = getattr(si, "trade_mode", None)
        if mode is not None and mode == getattr(_m5, "SYMBOL_TRADE_MODE_DISABLED", 0):
            return False, "trading desactive pour ce symbole chez ce broker"
        capital = float(info.balance)
        budget = capital * float(CONFIG.get("RISK_PCT", 1.0)) / 100.0
        pips = _stop_estime(symbol, timeframe)
        if pips is None:
            return True, ""
        vpip = (si.point * 10 / si.trade_tick_size) * si.trade_tick_value
        risque_mini = float(si.volume_min) * vpip * pips
        if risque_mini > budget:
            return False, ("lot minimum %.2f risque %.2f, budget %.2f (%.1f%% du capital)"
                           % (si.volume_min, risque_mini, budget,
                              risque_mini / max(capital, 1) * 100))
        return True, ""
    except Exception as e:
        # En cas de doute on laisse passer : le controle de coherence au
        # demarrage signalera le probleme sans bloquer le bot.
        return True, "verification impossible (%s)" % str(e)[:40]


def build_book():
    """
    Construit le portefeuille declare dans CONFIG["LIVE_BOOK"].

    Chaque ligne est un couple marche + strategie independant. Elles partagent
    le meme compte mais n'interagissent pas : une position par symbole maximum.
    Retourne une liste vide si rien n'est configure, ce qui bloque le demarrage.
    """
    book = []
    for line in CONFIG.get("LIVE_BOOK", []) or []:
        try:
            sym = line["symbol"].upper()
            tf = line.get("timeframe", "H1").upper()
            portable, pourquoi = _ligne_portable(sym, tf)
            if not portable:
                log(colors(f"⏭️  {sym} {tf} ecarte : {pourquoi}", "yellow"))
                continue
            book.append({
                "symbol": line["symbol"].upper(),
                "timeframe": line.get("timeframe", "H1").upper(),
                "strategy": st.build(line["strategy"], **(line.get("params") or {})),
                "last_bar": None,
                "pip": None,
            })
        except Exception as e:
            log(colors(f"⚠️ Ligne de portefeuille ignoree ({line}): {e}", "yellow"))

    # Repli mono-ligne pour rester compatible avec l'ancienne configuration.
    if not book and CONFIG.get("LIVE_STRATEGY"):
        book.append({
            "symbol": SYMBOL,
            "timeframe": CONFIG.get("LIVE_TIMEFRAME", "H1").upper(),
            "strategy": st.build(CONFIG["LIVE_STRATEGY"], **(CONFIG.get("LIVE_STRATEGY_PARAMS") or {})),
            "last_bar": None,
            "pip": None,
        })
    return book


def _last_closed_bar_time(rates) -> int:
    return int(rates[-1]["time"])


# ==================== ETAT PERSISTANT ====================
# Sans ca, un redemarrage remet last_bar a None, chaque ligne reevalue la
# derniere bougie cloturee et peut rouvrir un trade qu'elle a DEJA pris.
# ONE_POSITION_RULE ne protege que si la position precedente est encore ouverte.
# Si elle a ete cloturee entre-temps, on entre une seconde fois sur un signal
# perime. Le fichier d'etat evite ce doublon.

STATE_PATH = md.PROJECT_ROOT / "data" / "live_state.json"


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(book):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {f"{l['symbol']}_{l['timeframe']}": l["last_bar"]
                for l in book if l.get("last_bar")}
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _sl_pips_from_distance(distance_price: float, pip: float) -> float:
    """Convertit une distance de stop en prix vers des pips, borne par securite."""
    pips = distance_price / pip
    return float(min(max(pips, MIN_SL_PIPS), MAX_SL_PIPS))


def run_once(line, dry_run: bool = False):
    """
    Un passage sur une ligne du portefeuille : lit les bougies cloturees,
    calcule le signal de la derniere, et execute si besoin.
    """
    symbol = line["symbol"]
    timeframe = line["timeframe"]
    strategy = line["strategy"]
    pip = line["pip"]

    # shift=1 : on ignore la bougie en cours de formation. C'est la meme
    # convention que le backtest, sinon live et backtest divergent.
    rates = md.fetch_rates(symbol, timeframe, BARS, shift=1)
    bar_time = _last_closed_bar_time(rates)

    signals, sl_dist = strategy.signals(rates)
    idx = len(rates) - 1
    signal = int(signals[idx])

    if signal == 0:
        return bar_time, None

    decision = "BUY" if signal > 0 else "SELL"
    distance = sl_dist[idx]
    if not np.isfinite(distance) or distance <= 0:
        log(colors(f"⚠️ Signal {decision} ignore: distance de stop invalide", "yellow"))
        return bar_time, None

    sl_pips = _sl_pips_from_distance(float(distance), pip)
    tp_pips = sl_pips * TP_R

    bar_dt = datetime.fromtimestamp(bar_time)
    log(colors(f"📊 {symbol} {timeframe} {bar_dt:%Y-%m-%d %H:%M} → ", "white")
        + colors(decision, "green" if signal > 0 else "red")
        + colors(f" | SL {sl_pips:.1f} pips | TP {tp_pips:.1f} pips", "cyan"))

    if dry_run:
        log(colors("🧪 dry-run: aucun ordre envoye", "yellow"))
        return bar_time, decision

    # Interrupteur du tableau de bord. En PAUSE ou OFF on ne prend aucun
    # nouveau trade, mais les positions deja ouvertes restent gerees et
    # gardent leur stop et leur objectif chez le broker.
    if not control.peut_ouvrir():
        log(colors(f"⏸️  Bot en {control.mode()} : signal {decision} sur {symbol} ignore", "yellow"))
        if record_event:
            record_event({"type": "ignore_controle", "symbol": symbol,
                          "decision": decision, "mode": control.mode()})
        return bar_time, None

    # Regles de la prop firm, evaluees sur l'EQUITY donc flottant inclus.
    # Passe AVANT les filtres de strategie : perdre le compte coute plus cher
    # que rater un signal, et aucun filtre ne rattrape un compte disqualifie.
    garde = _garde_propfirm()
    if garde is not None:
        import MetaTrader5 as mt5
        info = mt5.account_info()
        if info is not None:
            autorise, verdict = garde.peut_ouvrir(info.equity, info.balance)
            if not autorise:
                log(colors("🛡️  Prop firm: %s, signal %s sur %s ignore"
                           % (verdict["raison"], decision, symbol), "yellow"))
                if verdict["statut"] == FERMER_TOUT:
                    log(colors("🚨 Limite critique atteinte, fermeture de toutes "
                               "les positions et passage en PAUSE", "red"))
                    _fermer_tout_propfirm()
                    control.ecrire(control.PAUSE, par="propfirm_guard")
                if record_event:
                    record_event({"type": "ignore_propfirm", "symbol": symbol,
                                  "decision": decision, **{
                                      k: verdict[k] for k in
                                      ("statut", "consomme_pct", "limite_qui_mord")}})
                return bar_time, None

    if validate_signal:
        # Le stop est passe au filtre pour qu'il juge le spread par rapport au
        # risque reel, et non contre un plafond absolu calibre pour EURUSD.
        ok, reason = validate_signal(decision, symbol=symbol, sl_pips=sl_pips)
        if not ok:
            log(colors(f"⛔ Signal rejete par les filtres: {reason}", "red"))
            if record_event:
                record_event({"type": "rejected", "symbol": symbol,
                              "reason": reason, "decision": decision})
            return bar_time, None

    if attach_context:
        try:
            attach_context({
                "cycle_ts": datetime.now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "strategy": repr(strategy),
                "timeframe": timeframe,
                "sl_pips": round(sl_pips, 2),
                "tp_r": TP_R,
                "bar_time": bar_time,
            })
        except Exception:
            pass

    result = open_trade(decision, symbol=symbol, sl_pips=sl_pips, tp_pips=tp_pips)

    if result is not None:
        if remember_trade_time:
            remember_trade_time()
        if note_open_ticket and get_open_position:
            try:
                pos = get_open_position(symbol)
                if pos:
                    note_open_ticket(pos.ticket)
                    # Enregistre le 1R pour que le break-even se declenche au bon
                    # endroit. Sans ca, le monitor retomberait sur un seuil en pips
                    # fixes et le live divergerait du backtest.
                    if note_risk_distance:
                        note_risk_distance(pos.ticket, sl_pips * pip)
            except Exception:
                pass
        if record_event:
            record_event({"type": "executed", "symbol": symbol,
                          "decision": decision, "strategy": repr(strategy)})

    return bar_time, decision


def start(dry_run: bool = False):
    book = build_book()
    if not book:
        log(colors("⛔ Aucune strategie live configuree.", "red"))
        log(colors("   Renseigne CONFIG['LIVE_BOOK'] (ou LIVE_STRATEGY)", "yellow"))
        log(colors("   avec une strategie qui a passe la grille d'acceptation:", "yellow"))
        log(colors("   python research/sweep.py --all --detail 3", "cyan"))
        return

    if init_logs_txt:
        try:
            init_logs_txt()
        except Exception:
            pass

    if not initialize_mt5():
        log(colors("❌ MT5 injoignable. Lance le terminal MetaTrader 5 et connecte le compte.", "red"))
        return

    # Resolution des specs de chaque marche du portefeuille.
    #
    # Le tri de portabilite se fait ICI et pas dans build_book() : celui-ci
    # tourne avant initialize_mt5(), donc sans acces au solde du compte, et
    # laissait passer des lignes intradables. SWI20 a un lot minimum de 1.0 qui
    # risque 85 $ : sur un compte de 1000 $ a 1% de risque, la ligne tradait a
    # huit fois le risque prevu au lieu d'etre ecartee.
    usable = []
    for line in book:
        try:
            line["pip"] = md.symbol_specs(line["symbol"])["pip"]
        except Exception as e:
            log(colors(f"⚠️ {line['symbol']} indisponible, ligne ignoree: {e}", "yellow"))
            continue
        portable, pourquoi = _ligne_portable(line["symbol"], line["timeframe"])
        if not portable:
            log(colors(f"⏭️  {line['symbol']} {line['timeframe']} ecarte : {pourquoi}",
                       "yellow"))
            continue
        usable.append(line)
    book = usable
    if not book:
        log(colors("❌ Aucun marche du portefeuille n'est accessible.", "red"))
        return

    # Reprise de l'etat : evite de rejouer un signal deja pris apres un redemarrage.
    state = load_state()
    resumed = 0
    for line in book:
        key = f"{line['symbol']}_{line['timeframe']}"
        if key in state:
            line["last_bar"] = state[key]
            resumed += 1

    status = md.terminal_status()

    # Garde-fou argent reel. La strategie n'a pas encore de forward test, donc
    # elle n'a rien a faire sur un compte reel. Pour lever ce blocage il faut
    # mettre CONFIG["ALLOW_LIVE_REAL_MONEY"] a True, explicitement et sciemment.
    account_kind = {0: "DEMO", 1: "CONCOURS", 2: "REEL"}
    try:
        import MetaTrader5 as _mt5
        _acc = _mt5.account_info()
        kind = account_kind.get(getattr(_acc, "trade_mode", None), "INCONNU")
    except Exception:
        kind = "INCONNU"

    if kind != "DEMO" and not dry_run and not CONFIG.get("ALLOW_LIVE_REAL_MONEY", False):
        log(colors(f"⛔ Compte de type {kind}, pas DEMO. Demarrage refuse.", "red"))
        log(colors("   Cette strategie n'a pas encore passe de forward test.", "yellow"))
        log(colors("   Pour forcer: CONFIG['ALLOW_LIVE_REAL_MONEY'] = True", "yellow"))
        return

    print("")
    log(colors("🐐 A.I.T.B ", "cyan") + colors("--> ", "white") + colors("🟢 HEADLESS", "green"))
    log(colors(f"   compte    {status.get('login')} @ {status.get('server')} "
               f"({status.get('balance')} {status.get('currency')})", "white"))
    for line in book:
        log(colors(f"   ligne     {line['symbol']:8s} {line['timeframe']:3s} {line['strategy']}", "white"))
    log(colors(f"   type      {kind}", "green" if kind == "DEMO" else "red"))
    if resumed:
        log(colors(f"   reprise   {resumed}/{len(book)} lignes reprises depuis data/live_state.json", "cyan"))
    log(colors(f"   controle  {control.mode()}", "green" if control.mode()=="ON" else "yellow"))
    log(colors(f"   reglages  TP {TP_R}R | risque {CONFIG.get('RISK_PCT')}% par trade "
               f"| max {CONFIG.get('MAX_OPEN_POSITIONS')} positions", "white"))
    try:
        import MetaTrader5 as _m5
        _info = _m5.account_info()
        _controle_coherence(float(_info.balance) if _info else 0.0, book)
    except Exception as _e:
        log(colors(f"⚠️ Controle de coherence impossible: {_e}", "yellow"))
    log(colors(f"   mode      {'DRY-RUN, aucun ordre' if dry_run else 'LIVE, ordres reels'}",
               "yellow" if dry_run else "green"))
    print("")

    last_monitor = 0.0

    try:
        while True:
            now = time.time()

            # Surveillance prop firm. Doit tourner en CONTINU, pas seulement a
            # l'arrivee d'un signal : la limite journaliere se calcule sur
            # l'equity, donc une position ouverte peut la faire sauter alors
            # que le bot n'a rien a decider.
            _surveiller_propfirm()

            # Suivi des positions ouvertes, independamment des nouvelles bougies.
            if now - last_monitor >= MONITOR_EVERY:
                last_monitor = now
                for fn in (monitor_positions_once, monitor_closed_positions_once, refresh_open_rows_from_mt5):
                    if fn is None:
                        continue
                    try:
                        fn()
                    except Exception as e:
                        log(colors(f"⚠️ monitoring: {e}", "yellow"))

            # Chaque ligne decide independamment, sur la cloture de SA bougie.
            for line in book:
                try:
                    rates = md.fetch_rates(line["symbol"], line["timeframe"], 2, shift=1)
                    bar_time = _last_closed_bar_time(rates)
                except Exception as e:
                    log(colors(f"⚠️ lecture {line['symbol']}: {e}", "yellow"))
                    continue

                if bar_time == line["last_bar"]:
                    continue
                line["last_bar"] = bar_time
                if not dry_run:
                    save_state(book)
                try:
                    run_once(line, dry_run=dry_run)
                except Exception as e:
                    log(colors(f"⚠️ cycle {line['symbol']}: {e}", "yellow"))

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("")
        log(colors("⛔ Interruption clavier, arret propre.", "yellow"))
    finally:
        try:
            from aitb.logger import finalize_before_shutdown
            finalize_before_shutdown()
        except Exception:
            pass
        log(colors("🐐 A.I.T.B", "cyan") + colors(" --> ", "white") + colors("🔴 OFF", "red"))


def main():
    args = sys.argv[1:]

    if "--status" in args:
        if not md.connect():
            print("MT5 injoignable. Lance le terminal MetaTrader 5.")
            return
        try:
            for k, v in md.terminal_status().items():
                print(f"  {k:15s} {v}")
            book = build_book()
            if not book:
                print("\n  portefeuille: AUCUNE ligne, le bot refusera de demarrer")
            else:
                print(f"\n  portefeuille: {len(book)} ligne(s)")
                for line in book:
                    try:
                        sp = md.symbol_specs(line["symbol"])
                        print(f"    {line['symbol']:8s} {line['timeframe']:3s} "
                              f"spread={sp['spread_points']:>3d} pts  "
                              f"lot {sp['volume_min']}/{sp['volume_max']}/{sp['volume_step']}  "
                              f"{line['strategy']}")
                    except Exception as e:
                        print(f"    {line['symbol']:8s} INDISPONIBLE: {e}")
        finally:
            md.disconnect()
        return

    start(dry_run="--dry-run" in args)


if __name__ == "__main__":
    main()
