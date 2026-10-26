# research/walkforward.py
"""
Validation walk-forward : le test le plus dur qu'on puisse faire sans attendre.

Le probleme des tests precedents
--------------------------------
Meme le decoupage 60/40 out of sample garde un biais : les parametres ont ete
choisis en regardant l'ensemble des donnees, donc la partie "jamais vue" a
quand meme influence la selection. C'est le biais de recherche, et il est
responsable d'une grande partie des 1600 backtests sur 2000 qui meurent en reel.

Le walk-forward supprime ce biais
---------------------------------
On decoupe l'historique en fenetres successives. Pour chacune :
  1. on choisit les meilleurs parametres en ne regardant QUE le passe
  2. on les applique a la periode suivante, jamais vue
  3. on avance et on recommence

On obtient une dizaine de periodes out of sample enchainees, exactement comme
si on avait fait tourner le bot en reel pendant trois ans en le re-optimisant
periodiquement. C'est ce qui se rapproche le plus d'un vrai historique live.

Lecture du resultat
-------------------
Ce qui compte n'est pas le rendement total mais la PROPORTION de fenetres
positives et l'ecart entre performance in-sample et out-of-sample. Un ecart
enorme signifie qu'on optimise du bruit.

Usage:
    python research/walkforward.py
    python research/walkforward.py --windows 8 --risk 0.40
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                     # noqa: E402

from aitb import market_data as md                     # noqa: E402
from aitb import strategies as st                      # noqa: E402
from aitb.backtest import BacktestConfig, Costs, backtest_strategy   # noqa: E402

COSTS = Costs(commission_per_lot_round_turn=7.0, slippage_points=2.0)

# Marches du book forex actuel. On re-selectionne les parametres a chaque
# fenetre, donc on ne fige que le couple marche + famille de strategie.
MARKETS = [
    ("EURUSD", "H1", "ema_trend"),
    ("USDJPY", "H1", "ema_trend"),
    ("AUDJPY", "H1", "ema_trend"),
    ("USDJPY", "H4", "vol_breakout"),
    ("EURJPY", "H4", "vol_breakout"),
]


def specs_from(meta, symbol):
    return {"symbol": symbol,
            **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                    "volume_min", "volume_max", "volume_step")}}


def best_params_on(rates, specs, tf, family, config):
    """
    Meilleure variante sur CETTE tranche de donnees uniquement.
    Score = esperance ponderee par la regularite, pas le rendement brut,
    pour eviter de retenir une variante qui a eu un coup de chance unique.
    """
    best = None
    for v in st.expand_grid(family):
        try:
            r = backtest_strategy(v, rates, specs, tf, config, COSTS)
        except Exception:
            continue
        m = r.metrics
        if m["trades"] < 15:
            continue
        pf = min(m["profit_factor"], 5.0)
        dd = max(m["max_drawdown_pct"], 1.0)
        score = (pf - 1.0) * m["return_pct"] / dd
        if best is None or score > best[0]:
            best = (score, v, m)
    return best


def run(n_windows=8, risk=0.40, train_ratio=0.6):
    config = BacktestConfig(initial_balance=100000.0, risk_pct=risk, tp_r=2.0)

    data = {}
    for sym, tf, fam in MARKETS:
        rates, meta = md.load_history(sym, tf)
        data[(sym, tf)] = (rates, specs_from(meta, sym))

    # Fenetres temporelles communes, basees sur le marche le plus dense.
    ref = max(data.values(), key=lambda x: len(x[0]))[0]
    t_start, t_end = int(ref[0]["time"]), int(ref[-1]["time"])
    span = t_end - t_start
    step = span // (n_windows + 1)

    print("=" * 92)
    print(f"  WALK-FORWARD : {n_windows} fenetres, risque {risk}%, compte 100 000$")
    print("  parametres re-choisis sur le passe uniquement, testes sur la periode suivante")
    print("=" * 92)
    print(f"  {'fenetre':>8s} {'periode testee':>25s} {'trades':>7s} {'IN PF':>7s} "
          f"{'OUT PF':>8s} {'OUT ret':>9s} {'OUT $':>9s}")
    print("  " + "-" * 84)

    all_oos_r = []
    window_results = []

    for w in range(n_windows):
        train_end = t_start + step * (w + 1)
        test_end = min(train_end + step, t_end)
        if test_end <= train_end:
            break

        in_pfs, out_pfs, oos_r, n_out = [], [], [], 0

        for sym, tf, fam in MARKETS:
            rates, specs = data[(sym, tf)]
            train = rates[(rates["time"] >= t_start) & (rates["time"] < train_end)]
            test = rates[(rates["time"] >= train_end) & (rates["time"] < test_end)]
            if len(train) < 400 or len(test) < 120:
                continue

            picked = best_params_on(train, specs, tf, fam, config)
            if picked is None:
                continue
            _, variant, m_in = picked

            try:
                r_out = backtest_strategy(variant, test, specs, tf, config, COSTS)
            except Exception:
                continue

            in_pfs.append(m_in["profit_factor"])
            out_pfs.append(r_out.metrics["profit_factor"])
            n_out += r_out.metrics["trades"]
            for t in r_out.trades:
                oos_r.append((t.time_out, t.r_multiple))

        if not oos_r:
            continue

        oos_r.sort()
        bal = 100000.0
        for _, rr in oos_r:
            bal += bal * (risk / 100.0) * rr
        ret = (bal / 100000.0 - 1) * 100
        a = datetime.fromtimestamp(train_end)
        b = datetime.fromtimestamp(test_end)
        print(f"  {w+1:>8d} {a:%Y-%m-%d} -> {b:%Y-%m-%d} {n_out:>7d} "
              f"{np.mean(in_pfs):>7.2f} {np.mean(out_pfs):>8.2f} {ret:>8.2f}% {bal-100000:>8.0f}$")

        all_oos_r.extend(oos_r)
        window_results.append({"ret": ret, "in_pf": np.mean(in_pfs), "out_pf": np.mean(out_pfs)})

    if not window_results:
        print("  Pas assez de donnees pour un walk-forward.")
        return

    rets = np.array([w["ret"] for w in window_results])
    in_pf = np.mean([w["in_pf"] for w in window_results])
    out_pf = np.mean([w["out_pf"] for w in window_results])

    print("\n" + "=" * 92)
    print("  RESULTAT AGREGE, uniquement des donnees jamais vues au moment du choix")
    print("=" * 92)
    print(f"  fenetres positives        : {(rets > 0).sum()}/{len(rets)}  ({(rets > 0).mean()*100:.0f}%)")
    print(f"  rendement median/fenetre  : {np.median(rets):+.2f}%")
    print(f"  profit factor IN SAMPLE   : {in_pf:.2f}")
    print(f"  profit factor OUT SAMPLE  : {out_pf:.2f}")
    degradation = (1 - (out_pf - 1) / max(in_pf - 1, 0.01)) * 100 if in_pf > 1 else 100
    print(f"  degradation de l'edge     : {degradation:.0f}%")

    all_oos_r.sort()
    bal = 100000.0
    eq = [bal]
    for _, rr in all_oos_r:
        bal += bal * (risk / 100.0) * rr
        eq.append(bal)
    eq = np.array(eq)
    peak = np.maximum.accumulate(eq)
    dd = float(((peak - eq) / peak).max() * 100)
    yrs = max((all_oos_r[-1][0] - all_oos_r[0][0]) / 86400 / 365.25, 0.1)
    ann = ((eq[-1] / 100000.0) ** (1 / yrs) - 1) * 100

    print()
    print(f"  COURBE COMPLETE EN OUT OF SAMPLE ENCHAINE ({len(all_oos_r)} trades, {yrs:.1f} ans)")
    print(f"    annualise        : {ann:+.2f}%")
    print(f"    max drawdown     : {dd:.2f}%")
    print(f"    par semaine      : {100000*ann/100/52:+.0f}$")
    print(f"    net 90% par mois : {100000*ann/100/12*0.9:+.0f}$")

    print()
    if degradation < 40 and (rets > 0).mean() >= 0.6:
        print("  VERDICT : l'edge survit au walk-forward. C'est le meilleur signal possible")
        print("            sans attendre. La strategie merite un challenge prop firm.")
    elif degradation < 70:
        print("  VERDICT : l'edge se degrade mais reste positif. Fragile, a surveiller.")
    else:
        print("  VERDICT : l'edge disparait hors echantillon. C'etait du sur-apprentissage.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--windows", type=int, default=8)
    p.add_argument("--risk", type=float, default=0.40)
    args = p.parse_args()
    run(args.windows, args.risk)


if __name__ == "__main__":
    main()
