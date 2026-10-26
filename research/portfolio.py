# research/portfolio.py
"""
Simulation de portefeuille multi-strategies.

Pourquoi c'est LE levier
------------------------
Une strategie seule a un ratio rendement / drawdown a peu pres constant quel
que soit le risque : monter le risque monte le gain ET le drawdown dans les
memes proportions. On ne gagne rien a tourner le bouton.

En revanche, N strategies decorrelees additionnent leurs gains de facon
lineaire alors que leurs drawdowns s'additionnent en racine de N, parce
qu'elles ne perdent pas en meme temps. C'est la seule facon connue d'ameliorer
le ratio sans prendre plus de risque. C'est pour ca que DaviddTech fait tourner
sept bots plutot qu'un seul.

Ce script mesure la decorrelation reelle au lieu de la supposer.

Usage:
    python research/portfolio.py
    python research/portfolio.py --risk 2.0
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                              # noqa: E402

from aitb import market_data as md              # noqa: E402
from aitb import strategies as st               # noqa: E402
from aitb.backtest import (                     # noqa: E402
    BacktestConfig, Costs, backtest_strategy,
)

# Retenues apres 7258 backtests, 10 familles de strategies, 19 instruments.
# Selection gloutonne par research/build_book.py, correlation maximale 0.4.
# Deux familles differentes : suivi de tendance et expansion de volatilite.
BOOK = [
    ("EURUSD", "H1", "ema_trend", {"fast": 21, "slow": 100, "filter_len": 200, "sl_atr": 3.0}),
    ("AUDJPY", "H1", "ema_trend", {"fast": 12, "slow": 50, "filter_len": 0, "sl_atr": 3.0}),
    ("USDJPY", "H1", "ema_trend", {"fast": 12, "slow": 100, "filter_len": 200, "sl_atr": 3.0}),
    ("USDJPY", "H4", "vol_breakout", {"k_atr": 0.5, "max_hour": 20, "sl_atr": 2.5}),
    ("EURJPY", "H4", "vol_breakout", {"k_atr": 0.5, "max_hour": 20, "sl_atr": 2.5}),
]

COSTS = Costs(commission_per_lot_round_turn=7.0, slippage_points=2.0)


def specs_from(meta, symbol):
    return {"symbol": symbol,
            **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                    "volume_min", "volume_max", "volume_step")}}


def collect(book, risk_pct, tp_r=2.0):
    """Backteste chaque ligne du book et retourne les resultats + les trades en R."""
    out = []
    for symbol, tf, name, params in book:
        rates, meta = md.load_history(symbol, tf)
        res = backtest_strategy(
            st.build(name, **params), rates, specs_from(meta, symbol), tf,
            BacktestConfig(initial_balance=1000.0, risk_pct=risk_pct, tp_r=tp_r), COSTS,
        )
        out.append((symbol, tf, name, params, res))
    return out


def simulate_portfolio(entries, risk_pct, initial=1000.0):
    """
    Rejoue tous les trades de toutes les strategies sur UN compte partage,
    dans l'ordre chronologique de cloture.

    Chaque trade risque risk_pct % du solde au moment ou il se termine.
    C'est une approximation : les positions se chevauchent dans la realite,
    donc le solde au moment de l'entree differe legerement. L'ecart est petit
    devant le message que l'on cherche a mesurer, la decorrelation.
    """
    merged = []
    for symbol, tf, name, params, res in entries:
        for t in res.trades:
            merged.append((t.time_out, symbol, t.r_multiple))
    merged.sort(key=lambda x: x[0])

    balance = initial
    equity = [initial]
    times = []
    for time_out, symbol, r in merged:
        balance += balance * (risk_pct / 100.0) * r
        equity.append(balance)
        times.append(time_out)

    equity = np.array(equity)
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.where(peak > 0, peak, 1.0)
    span_days = (merged[-1][0] - merged[0][0]) / 86400.0 if len(merged) > 1 else 1.0
    years = max(span_days / 365.25, 0.01)

    return {
        "trades": len(merged),
        "end_balance": float(equity[-1]),
        "return_pct": float((equity[-1] / initial - 1) * 100),
        "annualised_pct": float(((equity[-1] / initial) ** (1 / years) - 1) * 100),
        "max_dd_pct": float(dd.max() * 100),
        "years": years,
        "equity": equity,
        "times": times,
    }


def monthly_r(res, all_months):
    """Serie des R gagnes par mois, pour mesurer la correlation entre strategies."""
    buckets = {m: 0.0 for m in all_months}
    for t in res.trades:
        key = datetime.fromtimestamp(t.time_out).strftime("%Y-%m")
        if key in buckets:
            buckets[key] += t.r_multiple
    return np.array([buckets[m] for m in all_months])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--risk", type=float, default=1.0, help="risque par trade en %% du solde")
    p.add_argument("--tp-r", type=float, default=2.0)
    args = p.parse_args()

    entries = collect(BOOK, args.risk, args.tp_r)

    print("=" * 92)
    print(f"  LIGNES DU PORTEFEUILLE  (risque {args.risk}% par trade)")
    print("=" * 92)
    for symbol, tf, name, params, res in entries:
        m = res.metrics
        print(f"  {symbol:8s} {tf:3s} {name:10s} PF={m['profit_factor']:5.2f} "
              f"ret={m['return_pct']:7.2f}% DD={m['max_drawdown_pct']:5.1f}% "
              f"n={m['trades']:4d} R/an={m['trades'] / 3 * m['expectancy_usd'] / (10 * args.risk):5.1f}")

    # Correlation des rendements mensuels
    months = set()
    for *_, res in entries:
        for t in res.trades:
            months.add(datetime.fromtimestamp(t.time_out).strftime("%Y-%m"))
    months = sorted(months)
    series = [monthly_r(res, months) for *_, res in entries]

    print("\n" + "=" * 92)
    print("  CORRELATION DES RENDEMENTS MENSUELS  (0 = independantes, 1 = elles perdent ensemble)")
    print("=" * 92)
    names = [f"{s} {tf}" for s, tf, *_ in entries]
    print("  " + " " * 14 + "".join(f"{n:>14s}" for n in names))
    for i, n in enumerate(names):
        row = "  " + f"{n:14s}"
        for j in range(len(names)):
            c = np.corrcoef(series[i], series[j])[0, 1] if series[i].std() and series[j].std() else 0.0
            row += f"{c:>14.2f}"
        print(row)

    offdiag = [np.corrcoef(series[i], series[j])[0, 1]
               for i in range(len(series)) for j in range(i + 1, len(series))
               if series[i].std() and series[j].std()]
    if offdiag:
        print(f"\n  correlation moyenne hors diagonale : {np.mean(offdiag):.2f}")
        if np.mean(offdiag) < 0.3:
            print("  -> faiblement correlees, le portefeuille apporte une vraie diversification")
        elif np.mean(offdiag) < 0.6:
            print("  -> moyennement correlees, diversification partielle")
        else:
            print("  -> fortement correlees, le portefeuille n'apporte presque rien")

    print("\n" + "=" * 92)
    print("  PORTEFEUILLE COMBINE vs MEILLEURE LIGNE SEULE")
    print("=" * 92)
    solo = max(entries, key=lambda e: e[4].metrics["return_pct"])
    sm = solo[4].metrics
    solo_ann = ((1 + sm["return_pct"] / 100) ** (1 / 3) - 1) * 100
    print(f"  meilleure seule   {solo[0]:8s} annualise {solo_ann:6.2f}%  DD {sm['max_drawdown_pct']:5.1f}%  "
          f"ratio {solo_ann / max(sm['max_drawdown_pct'], 0.01):.2f}")

    port = simulate_portfolio(entries, args.risk)
    ratio = port["annualised_pct"] / max(port["max_dd_pct"], 0.01)
    print(f"  portefeuille x{len(entries)}     annualise {port['annualised_pct']:6.2f}%  "
          f"DD {port['max_dd_pct']:5.1f}%  ratio {ratio:.2f}")
    print(f"                    {port['trades']} trades sur {port['years']:.1f} ans, "
          f"soit {port['trades'] / port['years']:.0f}/an")

    gain = ratio / max(solo_ann / max(sm["max_drawdown_pct"], 0.01), 0.01)
    print(f"\n  amelioration du ratio rendement/drawdown : x{gain:.2f}")

    print("\n" + "=" * 92)
    print("  CE QUE CA DONNE SELON LE RISQUE ET LE CAPITAL")
    print("=" * 92)
    print("  risque   annualise    max DD      1 000 EUR      10 000 EUR     50 000 EUR")
    print("  " + "-" * 82)
    for risk in (1.0, 2.0, 3.0, 5.0):
        e = collect(BOOK, risk, args.tp_r)
        pf = simulate_portfolio(e, risk)
        a = pf["annualised_pct"]
        print(f"  {risk:4.1f}%   {a:8.2f}%   {pf['max_dd_pct']:6.1f}%   "
              f"{1000 * a / 100:9.0f} EUR/an  {10000 * a / 100:9.0f} EUR/an  {50000 * a / 100:9.0f} EUR/an")


if __name__ == "__main__":
    main()
