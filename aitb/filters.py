from datetime import datetime, timedelta
import pytz

from config.settings import CONFIG

from utils.time_logs import log
from utils.colors import colors

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

_last_trade_dt = None

def _now():
    return datetime.now(pytz.timezone(CONFIG["TIME_ZONE"]))

def _current_session() -> str:
    h = _now().hour
    if 7 <= h < 12:
        return "EU"
    if 12 <= h < 21:
        return "US"
    return "ASIA"

def _spread_info(symbol: str):
    """
    Retourne (spread_points, spread_pips, bid, ask, point, digits)
    ou None si indisponible (MT5 non connecté / symbole non sélectionné).
    """
    if mt5 is None:
        return None
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not info or not tick:
        return None
    spread_points = (tick.ask - tick.bid) / info.point
    # 5 décimales: 10 points = 1 pip  → c’est correct pour EURUSD standard
    spread_pips = spread_points / 10.0
    return spread_points, spread_pips, tick.bid, tick.ask, info.point, info.digits

def _cooldown_ok() -> bool:
    global _last_trade_dt
    if _last_trade_dt is None:
        return True
    mins = CONFIG.get("COOLDOWN_MINUTES", 0)
    return _now() - _last_trade_dt >= timedelta(minutes=mins)

def remember_trade_time():
    global _last_trade_dt
    _last_trade_dt = _now()

def validate_signal(decision: str, symbol: str = None, sl_pips=None) -> tuple[bool, str]:
    sym = symbol or CONFIG["SYMBOL"]
    if decision not in ("BUY", "SELL", "NOTHING"):
        return False, "Signal invalide"

    if decision == "NOTHING":
        return True, "Aucune action demandée"

    # Sessions
    if CONFIG.get("SESSION_FILTER", True):
        sess = _current_session()
        if sess not in CONFIG.get("ALLOWED_SESSIONS", ["EU", "US"]):
            return False, f"Session non autorisée: {sess}"

    # One-position rule
    if CONFIG.get("ONE_POSITION_RULE", True) and mt5 is not None:
        pos = mt5.positions_get(symbol=sym)
        if pos and len(pos) > 0:
            return False, f"Position deja ouverte sur {sym} (one-position rule)"

    # Cooldown
    if not _cooldown_ok():
        return False, "Cooldown actif"

    # Spread (journalisé à chaque validation)
    sp_info = _spread_info(sym)
    if sp_info is None:
        log(colors("⚠️ Spread indisponible (MT5 non connecté/symbole non sélectionné) — filtre ignoré", "yellow"))
    else:
        spread_points, spread_pips, bid, ask, point, digits = sp_info
        if CONFIG.get("LOG_SPREAD_DEBUG", False):
            log(colors(f"🧪 Spread → bid:{bid:.{digits}f} ask:{ask:.{digits}f} | {spread_points:.1f} pts ≈ {spread_pips:.2f} pips", "blue"))
        # Un spread se juge par rapport au stop, pas dans l'absolu. Le plafond
        # fixe de 2 pips avait ete calibre pour EURUSD et bloquait tout le
        # reste en silence : NZDUSD tourne a 4,5 pips, SWI20 a 17. Le ratio
        # spread/stop vaut pour toutes les classes d'actifs sans table.
        part_max = CONFIG.get("MAX_SPREAD_PCT_OF_SL")
        if part_max is not None and sl_pips and sl_pips > 0:
            part = spread_pips / float(sl_pips) * 100.0
            if part > part_max:
                return False, (f"Spread trop eleve: {spread_pips:.2f} pips = "
                               f"{part:.0f}% du stop de {sl_pips:.0f} pips "
                               f"(max {part_max:.0f}%)")
        else:
            # Repli quand le stop n'est pas connu : ancien plafond absolu.
            max_pips = CONFIG.get("MAX_SPREAD_PIPS")
            if max_pips is not None:
                if spread_pips > max_pips:
                    return False, f"Spread trop élevé: {spread_pips:.2f} pips (>{max_pips} pips)"
            else:
                max_pts = CONFIG.get("MAX_SPREAD_POINTS", 99999)
                if spread_points > max_pts:
                    return False, f"Spread trop élevé: {spread_points:.1f} pts (>{max_pts} pts)"

    # ATR (optionnel)
    if mt5 is not None:
        try:
            atr_min = CONFIG.get("MIN_ATR_POINTS", 0)
            if atr_min > 0:
                rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 15)
                if rates is None or len(rates) < 15:
                    return False, "ATR indisponible"
                trs, prev_close = [], rates[0][4]
                for r in rates[1:]:
                    high, low, close = r[2], r[3], r[4]
                    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                    trs.append(tr)
                    prev_close = close
                info = mt5.symbol_info(sym)
                atr_points = (sum(trs) / len(trs)) / info.point
                if atr_points < atr_min:
                    return False, f"ATR insuffisant: {atr_points:.1f} pts"
        except Exception:
            pass

    return True, "OK"