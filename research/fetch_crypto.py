# research/fetch_crypto.py
"""
Telechargement d'historique crypto depuis l'API publique Binance.

Pourquoi la crypto
------------------
Le levier identifie pour augmenter les gains n'est ni le risque ni la vitesse,
c'est le NOMBRE de lignes decorrelees. Sur 7258 backtests limites au forex et
aux indices, seules 5 combinaisons passent la grille. La crypto est une classe
d'actifs au comportement different, donc une source plausible de lignes
supplementaires non correlees aux paires JPY.

Trois avantages concrets pour un petit compte :
  - marche ouvert 24/7, donc pas de trou du week-end et plus de trades par an
  - volatilite plus forte, donc les frais pesent relativement moins
  - taille de position quasi continue. Sur le forex, le lot minimum de 0.01
    forcait le risque reel entre 78 % et 107 % de la consigne selon la paire.

Pourquoi pas TradingView
------------------------
TradingView donnerait acces aux memes marches, mais coute un abonnement, son
testeur n'impose ni frais ni out of sample, et il faudrait reecrire toutes les
strategies en Pine Script. L'API publique de Binance donne la meme donnee,
gratuitement, sans cle, et alimente directement le backtester qu'on a deja
valide. On garde notre moteur comme source de verite.

Usage:
    python research/fetch_crypto.py --list
    python research/fetch_crypto.py BTCUSDT ETHUSDT SOLUSDT --timeframes 1h 4h
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from aitb import market_data as md   # noqa: E402

BASE = "https://api.binance.com"

# Correspondance avec la nomenclature interne du projet.
TF_MAP = {"M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h", "D1": "1d"}
TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

# Frais spot Binance : 0.10 % par sens, soit 0.20 % aller-retour.
# Valeur utilisee par le sweep crypto, volontairement non optimiste.
COMMISSION_PCT_ROUND_TURN = 0.20


def _get(url: str, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aitb-research/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Binance injoignable apres {retries} tentatives: {last}")


def symbol_filters(symbol: str) -> dict:
    """
    Pas de prix et pas de quantite reels du marche.

    Ces valeurs alimentent le backtester. Les inventer fausserait le sizing :
    sur BTCUSDT le pas de quantite est 0.00001, sur d'autres paires 0.1.
    """
    data = _get(f"{BASE}/api/v3/exchangeInfo?symbol={symbol.upper()}")
    info = data["symbols"][0]
    tick_size = step_size = min_qty = None
    max_qty = 1e9
    for f in info["filters"]:
        if f["filterType"] == "PRICE_FILTER":
            tick_size = float(f["tickSize"])
        elif f["filterType"] == "LOT_SIZE":
            step_size = float(f["stepSize"])
            min_qty = float(f["minQty"])
            max_qty = float(f["maxQty"])
    if not tick_size or not step_size:
        raise RuntimeError(f"Filtres introuvables pour {symbol}")

    return {
        "symbol": symbol.upper(),
        "point": tick_size,
        # Convention interne : 1 pip = 10 points. Sur la crypto c'est une unite
        # d'affichage arbitraire, le calcul monetaire passe par tick_size.
        "pip": tick_size * 10.0,
        "tick_size": tick_size,
        # Une unite de base (1 BTC) qui bouge de tick_size rapporte tick_size
        # en quote (USDT). money = (diff / tick_size) * tick_value * volume
        # redonne bien diff * volume.
        "tick_value": tick_size,
        "volume_min": min_qty,
        "volume_max": max_qty,
        "volume_step": step_size,
        "spread_points": 0,
    }


def fetch_klines(symbol: str, timeframe: str, years: float = 3.0):
    """Bougies paginees par 1000, de la plus ancienne a la plus recente."""
    interval = TF_MAP[timeframe.upper()]
    minutes = TF_MINUTES[timeframe.upper()]
    end = datetime.now()
    start = end - timedelta(days=int(years * 365))
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    out = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{BASE}/api/v3/klines?symbol={symbol.upper()}&interval={interval}"
               f"&startTime={cursor}&limit=1000")
        chunk = _get(url)
        if not chunk:
            break
        out.extend(chunk)
        last_open = chunk[-1][0]
        nxt = last_open + minutes * 60 * 1000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(chunk) < 1000:
            break
        time.sleep(0.12)   # respecte la limite de requetes
    return out


def download(symbol: str, timeframe: str, years: float = 3.0) -> int:
    """Ecrit dans le meme cache CSV que les donnees MT5, pour que le sweep les lise pareil."""
    symbol = symbol.upper()
    timeframe = timeframe.upper()
    klines = fetch_klines(symbol, timeframe, years)
    if not klines:
        raise RuntimeError(f"Aucune bougie pour {symbol} {timeframe}")

    specs = symbol_filters(symbol)
    csv_path, meta_path = md._cache_paths(symbol, timeframe)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "tick_volume", "spread"])
        for k in klines:
            # Binance : [openTime, open, high, low, close, volume, closeTime, ...]
            w.writerow([
                int(k[0]) // 1000,
                f"{float(k[1]):.8f}", f"{float(k[2]):.8f}",
                f"{float(k[3]):.8f}", f"{float(k[4]):.8f}",
                int(float(k[5])),
                0,   # pas de spread historique fourni, le sweep force une valeur
            ])

    meta = dict(specs)
    meta.update({
        "timeframe": timeframe,
        "source": "binance_spot",
        "commission_pct_round_turn": COMMISSION_PCT_ROUND_TURN,
        "bars": len(klines),
        "first_time": int(klines[0][0]) // 1000,
        "last_time": int(klines[-1][0]) // 1000,
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
    })
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return len(klines)


def list_liquid(limit: int = 25):
    """Paires USDT les plus echangees sur 24h, pour choisir des marches liquides."""
    data = _get(f"{BASE}/api/v3/ticker/24hr")
    usdt = [d for d in data if d["symbol"].endswith("USDT")
            and not any(x in d["symbol"] for x in ("UP", "DOWN", "BULL", "BEAR"))]
    usdt.sort(key=lambda d: float(d["quoteVolume"]), reverse=True)
    return [(d["symbol"], float(d["quoteVolume"])) for d in usdt[:limit]]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*", help="ex: BTCUSDT ETHUSDT")
    p.add_argument("--timeframes", nargs="+", default=["H1", "H4"])
    p.add_argument("--years", type=float, default=3.0)
    p.add_argument("--list", action="store_true", help="paires USDT les plus liquides")
    args = p.parse_args()

    if args.list:
        print("  Paires USDT les plus liquides sur 24h :")
        for sym, vol in list_liquid(25):
            print(f"    {sym:14s} {vol/1e6:10.0f} M USDT")
        return

    if not args.symbols:
        print("  Rien a telecharger. Exemple :")
        print("    python research/fetch_crypto.py BTCUSDT ETHUSDT --timeframes H1 H4")
        return

    for sym in args.symbols:
        for tf in args.timeframes:
            try:
                n = download(sym, tf, args.years)
                rates, meta = md.load_history(sym, tf)
                a = datetime.fromtimestamp(int(rates[0]["time"]))
                b = datetime.fromtimestamp(int(rates[-1]["time"]))
                print(f"  {sym:12s} {tf:3s} {n:>7d} bougies  {a:%Y-%m-%d} -> {b:%Y-%m-%d}")
            except Exception as e:
                print(f"  {sym:12s} {tf:3s} ECHEC: {e}")


if __name__ == "__main__":
    main()
