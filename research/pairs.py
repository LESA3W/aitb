# research/pairs.py
"""
Arbitrage statistique : trading de paires cointegrees.

Pourquoi cette famille peut reussir la ou tout le reste a echoue
----------------------------------------------------------------
Tous les tests de la journee ont bute sur le meme mur : les strategies
directionnelles ne gagnent que quand le marche monte. Decomposition alpha/beta :
profit factor des shorts systematiquement sous 1. Autrement dit on mesurait de
l'exposition, pas un edge.

Le pairs trading supprime ce probleme PAR CONSTRUCTION. On achete un actif et
on vend simultanement un actif correle, pour un montant equivalent. Si les deux
montent, les deux jambes s'annulent. On ne parie ni sur la hausse ni sur la
baisse, uniquement sur le fait que l'ECART entre les deux revienne a sa moyenne.

Le phenomene exploite est reel et documente : deux actifs pousses par les memes
facteurs s'ecartent temporairement pour des raisons de flux, puis se
resynchronisent. C'est le fondement du statistical arbitrage.

Methode
-------
1. Sur une fenetre de calibration, on mesure le ratio de couverture par
   regression, puis le spread residuel.
2. On teste la stationnarite du spread. Un spread non stationnaire signifie que
   les deux actifs divergent durablement : la paire est rejetee.
3. On entre quand le spread s'ecarte de plus de N ecarts-types, on sort au
   retour vers zero, on coupe si l'ecart depasse un seuil de securite.
4. Tout est evalue en glissant : la calibration ne regarde que le passe.

Aucun parametre n'est optimise par paire. Les seuils sont les valeurs
classiques de la litterature : entree a 2 ecarts-types, sortie a 0.5.
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                    # noqa: E402

from aitb import market_data as md    # noqa: E402

# Frais aller-retour pour les DEUX jambes. Crypto spot 0.1% par jambe et par
# sens, donc 0.4% pour ouvrir et fermer une paire complete. Volontairement large.
COST_PCT_ROUND_TRIP = 0.40


def aligned_closes(sym_a, sym_b, timeframe="D1"):
    """Series de cloture alignees sur les timestamps communs."""
    ra, _ = md.load_history(sym_a, timeframe)
    rb, _ = md.load_history(sym_b, timeframe)
    ta = {int(t): float(c) for t, c in zip(ra["time"], ra["close"])}
    tb = {int(t): float(c) for t, c in zip(rb["time"], rb["close"])}
    common = sorted(set(ta) & set(tb))
    if len(common) < 400:
        return None, None, None
    return (np.array(common),
            np.array([ta[t] for t in common]),
            np.array([tb[t] for t in common]))


def adf_like_stat(x):
    """
    Statistique de retour a la moyenne, version legere du test de Dickey-Fuller.
    On regresse la variation sur le niveau : un coefficient franchement negatif
    indique que la serie est rappelee vers sa moyenne.
    Retourne le coefficient normalise, plus il est negatif mieux c'est.
    """
    x = np.asarray(x, dtype="f8")
    if len(x) < 30:
        return 0.0
    dx = np.diff(x)
    lag = x[:-1]
    lag_c = lag - lag.mean()
    denom = (lag_c ** 2).sum()
    if denom <= 0:
        return 0.0
    beta = (lag_c * (dx - dx.mean())).sum() / denom
    resid = dx - dx.mean() - beta * lag_c
    se = np.sqrt((resid ** 2).sum() / max(len(dx) - 2, 1) / denom)
    return float(beta / se) if se > 0 else 0.0


def backtest_pair(sym_a, sym_b, timeframe="D1", calib=180, entry_z=2.0,
                  exit_z=0.5, stop_z=4.0, adf_max=-2.5):
    """
    Rejoue la paire en glissant. La calibration a l'instant i n'utilise que les
    'calib' bougies precedentes, donc aucun regard vers l'avant.
    Retourne la liste des trades en rendement fractionnaire du capital engage.
    """
    times, a, b = aligned_closes(sym_a, sym_b, timeframe)
    if times is None:
        return None

    la, lb = np.log(a), np.log(b)
    n = len(times)
    trades = []
    pos = None   # (sens, index entree, spread entree, hedge)

    for i in range(calib, n):
        wa, wb = la[i - calib:i], lb[i - calib:i]
        va = wa.var()
        if va <= 0:
            continue
        hedge = float(np.cov(wa, wb)[0, 1] / va)
        if not np.isfinite(hedge) or abs(hedge) < 0.2 or abs(hedge) > 5:
            continue

        spread_hist = wb - hedge * wa
        mu, sd = spread_hist.mean(), spread_hist.std()
        if sd <= 0:
            continue
        if adf_like_stat(spread_hist) > adf_max:
            continue          # spread non stationnaire, on ecarte la paire

        z = (lb[i] - hedge * la[i] - mu) / sd

        if pos is None:
            if z > entry_z:
                pos = (-1, i, z, hedge)      # spread trop haut : on le vend
            elif z < -entry_z:
                pos = (1, i, z, hedge)       # spread trop bas : on l'achete
        else:
            side, i0, z0, h0 = pos
            hit_exit = abs(z) < exit_z
            hit_stop = (side < 0 and z > stop_z) or (side > 0 and z < -stop_z)
            if hit_exit or hit_stop:
                # Rendement : variation du spread capturee, en log, ramenee au
                # capital engage sur les deux jambes.
                move = (lb[i] - h0 * la[i]) - (lb[i0] - h0 * la[i0])
                gross = side * (-move)
                net = gross - COST_PCT_ROUND_TRIP / 100.0
                trades.append({
                    "t_out": int(times[i]), "ret": float(net),
                    "bars": i - i0, "reason": "stop" if hit_stop else "exit",
                })
                pos = None

    return trades


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--capital", type=float, default=100000.0)
    p.add_argument("--alloc", type=float, default=10.0,
                   help="pourcentage du capital engage par paire ouverte")
    p.add_argument("--top", type=int, default=20)
    args = p.parse_args()

    crypto = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
              "DOGEUSDT", "LTCUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]
    forex = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
             "NZDUSD", "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURAUD"]
    metaux = ["XAUUSD", "XAGUSD"]
    indices = ["DE40", "US500", "USTEC", "US30", "JPN225"]

    groups = {"CRYPTO": crypto, "FOREX": forex,
              "METAUX+INDICES": metaux + indices}

    print("=" * 96)
    print(f"  ARBITRAGE STATISTIQUE — {args.timeframe}, entree a 2 sigma, sortie a 0.5, stop a 4")
    print(f"  frais {COST_PCT_ROUND_TRIP}% par aller-retour sur les deux jambes")
    print("=" * 96)

    all_trades = []
    per_pair = []

    for gname, syms in groups.items():
        pairs = list(combinations(syms, 2))
        print(f"\n  {gname} : {len(pairs)} paires a tester")
        found = 0
        for sa, sb in pairs:
            try:
                tr = backtest_pair(sa, sb, args.timeframe)
            except Exception:
                continue
            if not tr or len(tr) < 12:
                continue
            rets = np.array([t["ret"] for t in tr])
            wins = rets[rets > 0].sum()
            loss = -rets[rets < 0].sum()
            pf = wins / loss if loss > 0 else 999.0
            per_pair.append({
                "pair": f"{sa}/{sb}", "group": gname, "n": len(tr),
                "pf": pf, "mean": float(rets.mean()) * 100,
                "total": float(rets.sum()) * 100,
            })
            all_trades.extend((t["t_out"], t["ret"]) for t in tr)
            found += 1
        print(f"    {found} paires exploitables")

    if not per_pair:
        print("\n  Aucune paire cointegree exploitable.")
        return

    print("\n" + "=" * 96)
    print("  MEILLEURES PAIRES")
    print("=" * 96)
    print(f"  {'paire':26s} {'groupe':16s} {'n':>5s} {'PF':>6s} {'moy/trade':>11s} {'total':>9s}")
    print("  " + "-" * 92)
    for r in sorted(per_pair, key=lambda x: -x["pf"])[:args.top]:
        print(f"  {r['pair']:26s} {r['group']:16s} {r['n']:>5d} {r['pf']:>6.2f} "
              f"{r['mean']:>10.3f}% {r['total']:>8.1f}%")

    # Portefeuille : toutes les paires exploitables, allocation fixe par trade.
    all_trades.sort()
    bal = args.capital
    eq = [bal]
    for _, r in all_trades:
        bal += bal * (args.alloc / 100.0) * r
        eq.append(bal)
    eq = np.array(eq)
    peak = np.maximum.accumulate(eq)
    dd = float(((peak - eq) / peak).max() * 100)
    yrs = max((all_trades[-1][0] - all_trades[0][0]) / 86400 / 365.25, 0.1)
    ann = ((eq[-1] / args.capital) ** (1 / yrs) - 1) * 100

    rets = np.array([r for _, r in all_trades])
    top = sorted(rets, reverse=True)
    per_year = defaultdict(float)
    for t, r in all_trades:
        per_year[datetime.fromtimestamp(t).year] += r
    years_pos = sum(1 for v in per_year.values() if v > 0)

    print("\n" + "=" * 96)
    print(f"  PORTEFEUILLE COMPLET — {len(per_pair)} paires, {len(all_trades)} trades, {yrs:.1f} ans")
    print("=" * 96)
    print(f"    annualise            : {ann:+.2f}%")
    print(f"    max drawdown         : {dd:.2f}%")
    print(f"    ratio                : {ann/max(dd,0.01):.2f}")
    print(f"    par semaine          : {args.capital*ann/100/52:+.0f}$")
    print(f"    annees positives     : {years_pos}/{len(per_year)}")
    print(f"    sans les 5 meilleurs : {(rets.sum()-sum(top[:5]))*100:+.1f}%  (total {rets.sum()*100:+.1f}%)")
    print("    " + "  ".join(f"{y}:{v*args.alloc/100*args.capital:+.0f}$"
                             for y, v in sorted(per_year.items())))

    if ann > 0:
        need = 10400 / (ann / 100)
        print(f"\n    capital necessaire pour 200$/semaine : {need:,.0f}$".replace(",", " "))


if __name__ == "__main__":
    main()
