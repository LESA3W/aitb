# aitb/market_data.py
"""
Acces headless aux donnees de marche MetaTrader 5.

Remplace integralement la boucle screenshot + MiniMouseMacro + OCR :
- aucune fenetre mise au premier plan
- aucun mouvement de souris
- aucun appel reseau externe
- MT5 peut rester reduit dans la barre des taches

Le terminal MT5 doit tourner sur la meme machine (contrainte de la lib MetaTrader5).
Un cache CSV local permet de rejouer des backtests terminal ferme.
"""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

# Racine reelle du projet. On ne se sert PAS de CONFIG["ROOT"] qui pointe sur config/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "history"

# Correspondance nom lisible -> constante MT5.
# On resout les constantes a l'appel pour rester importable sans MT5.
TIMEFRAME_NAMES = (
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
)

# Minutes par bougie, utile pour dimensionner une plage d'historique.
TIMEFRAME_MINUTES = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10,
    "M12": 12, "M15": 15, "M20": 20, "M30": 30,
    "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480, "H12": 720,
    "D1": 1440, "W1": 10080, "MN1": 43200,
}

DTYPE = np.dtype([
    ("time", "i8"),
    ("open", "f8"),
    ("high", "f8"),
    ("low", "f8"),
    ("close", "f8"),
    ("tick_volume", "i8"),
    ("spread", "i4"),
])


class MarketDataError(RuntimeError):
    pass


def _require_mt5():
    if mt5 is None:
        raise MarketDataError("Le paquet MetaTrader5 n'est pas installe dans ce venv.")


def timeframe_const(name: str):
    """Convertit 'M5' en mt5.TIMEFRAME_M5."""
    _require_mt5()
    key = name.upper().strip()
    const = getattr(mt5, f"TIMEFRAME_{key}", None)
    if const is None:
        raise MarketDataError(f"Timeframe inconnu: {name}")
    return const


def connect() -> bool:
    """Initialise la connexion au terminal local. Silencieux, retourne un booleen."""
    _require_mt5()
    if not mt5.initialize():
        return False
    return True


def disconnect():
    if mt5 is not None:
        mt5.shutdown()


def terminal_status() -> dict:
    """Etat du terminal, pour diagnostic avant de lancer quoi que ce soit."""
    _require_mt5()
    ti = mt5.terminal_info()
    ai = mt5.account_info()
    return {
        "connected": bool(getattr(ti, "connected", False)),
        "trade_allowed": bool(getattr(ti, "trade_allowed", False)),
        "terminal": getattr(ti, "name", None),
        "login": getattr(ai, "login", None),
        "server": getattr(ai, "server", None),
        "balance": getattr(ai, "balance", None),
        "equity": getattr(ai, "equity", None),
        "currency": getattr(ai, "currency", None),
        "leverage": getattr(ai, "leverage", None),
    }


# ==================== SPECS SYMBOLE ====================

def symbol_specs(symbol: str) -> dict:
    """
    Caracteristiques du symbole necessaires pour valoriser un trade.

    Convention pip retenue dans tout le projet : 1 pip = 10 * point.
    C'est la meme que celle deja utilisee par monitor.py et trade_manager.py,
    donc 0.0001 sur EURUSD 5 digits et 0.10 sur XAUUSD 2 digits.
    """
    _require_mt5()
    info = mt5.symbol_info(symbol)
    if info is None:
        if not mt5.symbol_select(symbol, True):
            raise MarketDataError(f"Symbole introuvable ou non selectionnable: {symbol}")
        info = mt5.symbol_info(symbol)
    if info is None:
        raise MarketDataError(f"Symbole introuvable: {symbol}")

    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)

    point = float(info.point)
    return {
        "symbol": symbol,
        "digits": int(info.digits),
        "point": point,
        "pip": point * 10.0,
        "tick_size": float(getattr(info, "trade_tick_size", point) or point),
        "tick_value": float(getattr(info, "trade_tick_value", 1.0) or 1.0),
        "contract_size": float(getattr(info, "trade_contract_size", 100000.0) or 100000.0),
        "volume_min": float(getattr(info, "volume_min", 0.01)),
        "volume_max": float(getattr(info, "volume_max", 100.0)),
        "volume_step": float(getattr(info, "volume_step", 0.01)),
        "spread_points": int(getattr(info, "spread", 0)),
    }


# ==================== LECTURE TEMPS REEL ====================

def fetch_rates(symbol: str, timeframe: str, count: int, shift: int = 0) -> np.ndarray:
    """
    Derniers 'count' bougies cloturees, la plus recente en dernier.

    shift=1 ignore la bougie en cours de formation, ce qui est le comportement
    attendu pour prendre une decision : on ne decide jamais sur une bougie
    incomplete, sinon le backtest et le live divergent.
    """
    _require_mt5()
    tf = timeframe_const(timeframe)
    rates = mt5.copy_rates_from_pos(symbol, tf, shift, count)
    if rates is None or len(rates) == 0:
        raise MarketDataError(
            f"Aucune donnee pour {symbol} {timeframe}. "
            f"Verifie que le terminal est lance et le symbole visible dans Market Watch."
        )
    return _to_dtype(rates)


def fetch_range(symbol: str, timeframe: str, start: datetime, end: datetime) -> np.ndarray:
    """Historique entre deux dates. Le terminal doit avoir telecharge la periode."""
    _require_mt5()
    tf = timeframe_const(timeframe)
    rates = mt5.copy_rates_range(symbol, tf, start, end)
    if rates is None or len(rates) == 0:
        raise MarketDataError(
            f"Aucun historique {symbol} {timeframe} entre {start:%Y-%m-%d} et {end:%Y-%m-%d}. "
            f"Ouvre le graphique dans MT5 et fais defiler vers la gauche pour forcer le telechargement."
        )
    return _to_dtype(rates)


def current_tick(symbol: str) -> dict:
    """Bid/ask instantanes, pour l'execution live."""
    _require_mt5()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise MarketDataError(f"Pas de tick disponible pour {symbol}")
    return {
        "time": int(tick.time),
        "bid": float(tick.bid),
        "ask": float(tick.ask),
        "spread": float(tick.ask - tick.bid),
    }


def _to_dtype(rates) -> np.ndarray:
    out = np.zeros(len(rates), dtype=DTYPE)
    out["time"] = rates["time"]
    out["open"] = rates["open"]
    out["high"] = rates["high"]
    out["low"] = rates["low"]
    out["close"] = rates["close"]
    out["tick_volume"] = rates["tick_volume"]
    if "spread" in rates.dtype.names:
        out["spread"] = rates["spread"]
    return out


# ==================== CACHE CSV ====================

def _cache_paths(symbol: str, timeframe: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{symbol.upper()}_{timeframe.upper()}"
    return CACHE_DIR / f"{stem}.csv", CACHE_DIR / f"{stem}.meta.json"


def download_history(symbol: str, timeframe: str, years: float = 3.0) -> int:
    """
    Telecharge l'historique depuis MT5 et l'ecrit dans data/history/.
    Retourne le nombre de bougies ecrites.

    A lancer une fois par couple symbole/timeframe. Ensuite les backtests
    tournent terminal ferme, ce qui rend la boucle de recherche beaucoup plus rapide.
    """
    end = datetime.now()
    start = end - timedelta(days=int(years * 365))

    # copy_rates_range echoue souvent sur les timeframes courts meme quand le
    # terminal a bien l'historique. copy_rates_from_pos, lui, repond. On tente
    # la plage d'abord car elle borne proprement, puis on retombe sur from_pos.
    try:
        rates = fetch_range(symbol, timeframe, start, end)
    except MarketDataError:
        minutes = TIMEFRAME_MINUTES.get(timeframe.upper(), 60)
        wanted = int(years * 365 * 24 * 60 / minutes * 1.2)

        # Le terminal plafonne le nombre de bougies restituables et repond None
        # au-dela, sans message. On descend par paliers jusqu'a obtenir quelque chose.
        rates = None
        for count in (wanted, 100000, 50000, 20000, 10000, 5000):
            if count > 200000:
                continue
            try:
                rates = fetch_rates(symbol, timeframe, count, shift=0)
                break
            except MarketDataError:
                continue
        if rates is None:
            raise MarketDataError(
                f"{symbol} {timeframe}: le terminal ne restitue aucune bougie. "
                f"Ouvre le graphique dans MT5 et laisse-le charger."
            )

        cutoff = int(start.timestamp())
        kept = rates[rates["time"] >= cutoff]
        if len(kept) > 500:
            rates = kept

    specs = symbol_specs(symbol)

    csv_path, meta_path = _cache_paths(symbol, timeframe)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "tick_volume", "spread"])
        for r in rates:
            w.writerow([
                int(r["time"]), f"{r['open']:.8f}", f"{r['high']:.8f}",
                f"{r['low']:.8f}", f"{r['close']:.8f}",
                int(r["tick_volume"]), int(r["spread"]),
            ])

    meta = dict(specs)
    meta.update({
        "timeframe": timeframe.upper(),
        "bars": int(len(rates)),
        "first_time": int(rates[0]["time"]),
        "last_time": int(rates[-1]["time"]),
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
    })
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return len(rates)


def load_history(symbol: str, timeframe: str):
    """
    Relit le cache CSV. Retourne (rates, meta).
    Leve une erreur explicite si le cache n'existe pas encore.
    """
    csv_path, meta_path = _cache_paths(symbol, timeframe)
    if not csv_path.exists():
        raise MarketDataError(
            f"Pas de cache pour {symbol} {timeframe}. "
            f"Lance d'abord: python -m aitb.market_data --download {symbol} {timeframe}"
        )

    rows = np.genfromtxt(csv_path, delimiter=",", skip_header=1)
    if rows.ndim == 1:
        rows = rows.reshape(1, -1)

    out = np.zeros(len(rows), dtype=DTYPE)
    out["time"] = rows[:, 0].astype("i8")
    out["open"] = rows[:, 1]
    out["high"] = rows[:, 2]
    out["low"] = rows[:, 3]
    out["close"] = rows[:, 4]
    out["tick_volume"] = rows[:, 5].astype("i8")
    out["spread"] = rows[:, 6].astype("i4")

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return out, meta


def cached_datasets() -> list:
    """Liste des couples symbole/timeframe deja en cache."""
    if not CACHE_DIR.exists():
        return []
    found = []
    for p in sorted(CACHE_DIR.glob("*.csv")):
        parts = p.stem.rsplit("_", 1)
        if len(parts) == 2:
            found.append((parts[0], parts[1]))
    return found


# ==================== CLI ====================

def _main():
    import sys

    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("Usage:")
        print("  python -m aitb.market_data --status")
        print("  python -m aitb.market_data --download SYMBOL TIMEFRAME [ANNEES]")
        print("  python -m aitb.market_data --list")
        return

    if args[0] == "--list":
        for sym, tf in cached_datasets():
            try:
                rates, meta = load_history(sym, tf)
                first = datetime.fromtimestamp(int(rates[0]["time"]))
                last = datetime.fromtimestamp(int(rates[-1]["time"]))
                print(f"  {sym:10s} {tf:4s} {len(rates):>8d} bougies  {first:%Y-%m-%d} -> {last:%Y-%m-%d}")
            except Exception as e:
                print(f"  {sym:10s} {tf:4s} illisible ({e})")
        return

    if not connect():
        print("MT5 non joignable. Lance le terminal MetaTrader 5 puis relance cette commande.")
        return

    try:
        if args[0] == "--status":
            st = terminal_status()
            for k, v in st.items():
                print(f"  {k:15s} {v}")
            return

        if args[0] == "--download":
            if len(args) < 3:
                print("Usage: --download SYMBOL TIMEFRAME [ANNEES]")
                return
            symbol, timeframe = args[1].upper(), args[2].upper()
            years = float(args[3]) if len(args) > 3 else 3.0
            n = download_history(symbol, timeframe, years)
            print(f"  {symbol} {timeframe}: {n} bougies ecrites dans {CACHE_DIR}")
            return

        print(f"Argument inconnu: {args[0]}")
    finally:
        disconnect()


if __name__ == "__main__":
    _main()
