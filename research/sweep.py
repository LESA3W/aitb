# research/sweep.py
"""
Boucle de recherche de strategies.

C'est ici que Claude est utile : generer et tester des centaines de variantes,
jeter les mauvaises, ne remonter que celles qui passent la grille d'acceptation.
Le bot live, lui, n'appelle aucune IA : il execute une regle figee et deterministe.

Usage
-----
  python research/sweep.py --download EURUSD M15 XAUUSD M15    telecharge l'historique
  python research/sweep.py --list                              datasets en cache
  python research/sweep.py --all                               teste tout ce qui est en cache
  python research/sweep.py --symbols EURUSD --timeframes M15 H1
  python research/sweep.py --strategies donchian ema_trend --risk 1.0 --tp-r 2.0
  python research/sweep.py --detail 1                          rapport complet du meilleur
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from aitb import market_data as md          # noqa: E402
from aitb import strategies as st           # noqa: E402
from aitb.backtest import (                 # noqa: E402
    BacktestConfig, Costs, backtest_strategy, format_report,
)

RESULTS_DIR = PROJECT_ROOT / "data" / "research"

# Commission aller-retour par lot, par famille d'instrument.
# IC Markets RAW : 3.50 USD par lot et par sens sur le forex.
COMMISSION_BY_SYMBOL = {
    "XAUUSD": 7.0,
    "_default": 7.0,
}


def commission_for(symbol: str) -> float:
    return COMMISSION_BY_SYMBOL.get(symbol.upper(), COMMISSION_BY_SYMBOL["_default"])


def download(pairs):
    """pairs = [(SYMBOL, TIMEFRAME), ...]"""
    if not md.connect():
        print("MT5 non joignable. Lance le terminal MetaTrader 5, connecte le compte, puis relance.")
        return False
    try:
        for symbol, timeframe in pairs:
            try:
                n = md.download_history(symbol, timeframe, years=3.0)
                print(f"  {symbol:10s} {timeframe:4s} {n:>8d} bougies")
            except Exception as e:
                print(f"  {symbol:10s} {timeframe:4s} ECHEC: {e}")
    finally:
        md.disconnect()
    return True


def sweep(symbols, timeframes, strategy_names, config, costs_builder, top=25, detail=0, save=True):
    results = []
    total = 0

    for symbol in symbols:
        for timeframe in timeframes:
            try:
                rates, meta = md.load_history(symbol, timeframe)
            except Exception as e:
                print(f"  skip {symbol} {timeframe}: {e}")
                continue

            if len(rates) < 500:
                print(f"  skip {symbol} {timeframe}: seulement {len(rates)} bougies")
                continue

            specs = {
                "symbol": symbol,
                "point": meta.get("point", 0.00001),
                "pip": meta.get("pip", 0.0001),
                "tick_size": meta.get("tick_size", 0.00001),
                "tick_value": meta.get("tick_value", 1.0),
                "volume_min": meta.get("volume_min", 0.01),
                "volume_max": meta.get("volume_max", 100.0),
                "volume_step": meta.get("volume_step", 0.01),
            }
            costs = costs_builder(symbol)

            # Marches crypto : la commission est un pourcentage du notionnel,
            # pas un montant par lot. Binance spot facture 0.20 % aller-retour.
            # Le spread n'est pas fourni dans l'historique, on force une valeur
            # realiste de 2 points de base du prix median plutot que de laisser
            # le moteur retomber sur un defaut cale sur le forex.
            if meta.get("source") == "binance_spot":
                import numpy as _np
                median_price = float(_np.median(rates["close"]))
                costs = Costs(
                    commission_per_lot_round_turn=0.0,
                    commission_pct_round_turn=float(meta.get("commission_pct_round_turn", 0.20)),
                    spread_points=max(1.0, (0.0002 * median_price) / specs["point"]),
                    slippage_points=max(1.0, (0.0001 * median_price) / specs["point"]),
                )

            span = ""
            if len(rates):
                a = datetime.fromtimestamp(int(rates[0]["time"]))
                b = datetime.fromtimestamp(int(rates[-1]["time"]))
                span = f"{a:%Y-%m-%d} -> {b:%Y-%m-%d}"
            print(f"\n{symbol} {timeframe}  {len(rates)} bougies  {span}")

            for name in strategy_names:
                variants = st.expand_grid(name)
                for strategy in variants:
                    total += 1
                    try:
                        res = backtest_strategy(strategy, rates, specs, timeframe, config, costs)
                    except Exception as e:
                        print(f"    ERREUR {strategy}: {e}")
                        continue
                    results.append(res)
                print(f"  {name:18s} {len(variants):3d} variantes testees")

    if not results:
        print("\nAucun resultat. Telecharge d'abord de l'historique avec --download.")
        return []

    accepted = [r for r in results if r.passes()]
    ranked = sorted(results, key=_rank_key, reverse=True)

    print("\n" + "=" * 74)
    print(f"  {total} backtests   |   {len(accepted)} passent la grille d'acceptation")
    print("=" * 74)
    print("  Grille: >=100 trades, drawdown <20%, profit factor >1.3, bat le buy and hold")
    print("-" * 74)

    pool = accepted if accepted else ranked
    if not accepted:
        print("  AUCUNE strategie ne passe. Voici quand meme le classement brut,")
        print("  a lire comme un diagnostic et surtout pas comme des candidates.")
        print("-" * 74)

    for r in pool[:top] if accepted else ranked[:top]:
        print("  " + r.summary())

    if save:
        path = _save_csv(results)
        print(f"\n  Resultats complets: {path}")

    for r in (accepted or ranked)[:detail]:
        print("\n" + format_report(r))

    return results


def _rank_key(r):
    """
    Classement. On privilegie l'esperance par trade ponderee par la regularite,
    et non le rendement brut, qui recompense les strategies qui ont pris un
    risque enorme une seule fois.
    """
    m = r.metrics
    if m.get("trades", 0) < 30:
        return -1e9
    pf = min(m.get("profit_factor", 0.0), 5.0)
    dd = max(m.get("max_drawdown_pct", 100.0), 1.0)
    return (pf - 1.0) * m.get("return_pct", 0.0) / dd


def _save_csv(results) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"sweep_{datetime.now():%Y%m%d_%H%M%S}.csv"
    fields = [
        "strategy", "symbol", "timeframe", "trades", "return_pct", "buy_hold_pct",
        "profit_factor", "max_drawdown_pct", "win_rate", "expectancy_usd",
        "expectancy_pips", "avg_win_usd", "avg_loss_usd", "total_commission",
        "commission_share_pct", "sharpe", "trades_per_month", "avg_bars_held",
        "skipped_undersized", "accepted",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = dict(r.metrics)
            row.update({
                "strategy": r.strategy,
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "accepted": int(r.passes()),
            })
            w.writerow(row)
    return path


def main():
    p = argparse.ArgumentParser(description="Recherche de strategies A.I.T.B")
    p.add_argument("--download", nargs="*", metavar="SYMBOL TIMEFRAME",
                   help="couples symbole timeframe a telecharger, ex: EURUSD M15 XAUUSD H1")
    p.add_argument("--list", action="store_true", help="datasets en cache")
    p.add_argument("--all", action="store_true", help="teste tous les datasets en cache")
    p.add_argument("--symbols", nargs="+", default=None)
    p.add_argument("--timeframes", nargs="+", default=None)
    p.add_argument("--strategies", nargs="+", default=list(st.REGISTRY.keys()))
    p.add_argument("--balance", type=float, default=1000.0)
    p.add_argument("--risk", type=float, default=1.0, help="risque par trade en %% du capital")
    p.add_argument("--tp-r", type=float, default=2.0, help="take profit en multiples du risque")
    p.add_argument("--no-breakeven", action="store_true")
    p.add_argument("--trailing", action="store_true")
    p.add_argument("--slippage", type=float, default=2.0, help="slippage en points, par sens")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--detail", type=int, default=0, help="rapport complet des N meilleures")
    args = p.parse_args()

    if args.list:
        found = md.cached_datasets()
        if not found:
            print("  Aucun dataset. Utilise --download SYMBOL TIMEFRAME")
        for sym, tf in found:
            try:
                rates, _ = md.load_history(sym, tf)
                a = datetime.fromtimestamp(int(rates[0]["time"]))
                b = datetime.fromtimestamp(int(rates[-1]["time"]))
                print(f"  {sym:10s} {tf:4s} {len(rates):>8d} bougies  {a:%Y-%m-%d} -> {b:%Y-%m-%d}")
            except Exception as e:
                print(f"  {sym:10s} {tf:4s} illisible ({e})")
        return

    if args.download is not None:
        toks = args.download
        if len(toks) % 2 != 0:
            print("  --download attend des paires SYMBOL TIMEFRAME")
            return
        pairs = [(toks[i].upper(), toks[i + 1].upper()) for i in range(0, len(toks), 2)]
        download(pairs)
        return

    if args.all or (not args.symbols and not args.timeframes):
        cached = md.cached_datasets()
        symbols = sorted({s for s, _ in cached})
        timeframes = sorted({t for _, t in cached})
    else:
        symbols = [s.upper() for s in (args.symbols or [])]
        timeframes = [t.upper() for t in (args.timeframes or [])]

    if not symbols or not timeframes:
        print("  Rien a tester. Commence par: python research/sweep.py --download EURUSD M15")
        return

    config = BacktestConfig(
        initial_balance=args.balance,
        risk_pct=args.risk,
        tp_r=args.tp_r,
        use_breakeven=not args.no_breakeven,
        use_trailing=args.trailing,
    )

    def costs_builder(symbol):
        return Costs(
            commission_per_lot_round_turn=commission_for(symbol),
            slippage_points=args.slippage,
        )

    sweep(symbols, timeframes, args.strategies, config, costs_builder,
          top=args.top, detail=args.detail)


if __name__ == "__main__":
    main()
