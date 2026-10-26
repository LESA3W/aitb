# research/propfirm.py
"""
Simulation de challenge prop firm.

Le probleme
-----------
Avec 500 EUR de capital propre, aucun rendement realiste ne produit 1000 EUR
par mois. Il faudrait 200 % par mois. La seule facon connue et legale de faire
travailler un edge sur un capital qu'on ne possede pas, c'est le financement
par une societe de prop trading : on paie des frais d'inscription, on passe un
challenge sur compte simule, et si on le passe on trade leur capital en gardant
80 a 90 % des gains.

Le point cle, et c'est ce que ce script mesure : les regles de drawdown des
prop firms sont PLUS SERREES que le drawdown naturel de la strategie. Le
portefeuille actuel fait 12.4 % de drawdown maximum, alors qu'un challenge type
interdit de depasser 10 %. Tel quel, il echoue.

La question n'est donc pas "est-ce que la strategie est rentable" mais
"a quel niveau de risque la probabilite de passer le challenge est maximale".
Trop de risque : on touche la limite de perte. Trop peu : on n'atteint jamais
la cible, ou trop tard.

Methode
-------
On rejoue la vraie sequence chronologique de trades du portefeuille, en
demarrant a des points d'entree differents. Cela preserve le regroupement des
pertes par journee, ce qu'un tirage aleatoire independant detruirait et qui
ferait sous-estimer le risque de toucher la limite journaliere.

Usage:
    python research/propfirm.py
    python research/propfirm.py --target 8 --max-dd 8 --daily-dd 4
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                      # noqa: E402

from aitb import market_data as md                      # noqa: E402
from aitb import strategies as st                       # noqa: E402
from aitb.backtest import BacktestConfig, Costs, backtest_strategy   # noqa: E402
from research.portfolio import BOOK, COSTS, specs_from  # noqa: E402


def build_trade_sequence(risk_pct=1.0, tp_r=2.0):
    """
    Sequence chronologique de tous les trades du portefeuille.
    Retourne une liste de (timestamp_sortie, symbole, R).
    Le R est independant du risque, donc calculable une seule fois.
    """
    seq = []
    for symbol, tf, name, params in BOOK:
        rates, meta = md.load_history(symbol, tf)
        res = backtest_strategy(
            st.build(name, **params), rates, specs_from(meta, symbol), tf,
            BacktestConfig(initial_balance=1000.0, risk_pct=risk_pct, tp_r=tp_r), COSTS,
        )
        for t in res.trades:
            seq.append((t.time_out, symbol, t.r_multiple))
    seq.sort(key=lambda x: x[0])
    return seq


def run_challenge(seq, start_idx, risk_pct, target_pct, max_dd_pct, daily_dd_pct,
                  initial=100000.0, max_days=365):
    """
    Rejoue la sequence a partir de start_idx jusqu'a reussite ou echec.

    Regles modelisees, calees sur un challenge type :
      - cible de gain a atteindre
      - perte totale maximale, calculee depuis le solde INITIAL (drawdown statique)
      - perte journaliere maximale, calculee depuis le solde de debut de journee

    Retourne (statut, jours_ecoules, solde_final).
    """
    balance = initial
    floor_total = initial * (1 - max_dd_pct / 100.0)
    target = initial * (1 + target_pct / 100.0)

    day_start_balance = balance
    current_day = None
    t0 = seq[start_idx][0]

    for k in range(start_idx, len(seq)):
        ts, symbol, r = seq[k]
        day = datetime.fromtimestamp(ts).date()

        if current_day is None:
            current_day = day
        elif day != current_day:
            current_day = day
            day_start_balance = balance

        balance += balance * (risk_pct / 100.0) * r

        if balance <= floor_total:
            return "echec_dd_total", (ts - t0) / 86400.0, balance

        daily_floor = day_start_balance * (1 - daily_dd_pct / 100.0)
        if balance <= daily_floor:
            return "echec_dd_jour", (ts - t0) / 86400.0, balance

        if balance >= target:
            return "reussi", (ts - t0) / 86400.0, balance

        if (ts - t0) / 86400.0 > max_days:
            return "trop_lent", (ts - t0) / 86400.0, balance

    return "donnees_epuisees", (seq[-1][0] - t0) / 86400.0, balance


def sweep_risk(seq, target_pct, max_dd_pct, daily_dd_pct, risks, n_starts=300):
    """Taux de reussite et delai median par niveau de risque."""
    usable = max(len(seq) - 60, 1)
    starts = np.linspace(0, usable - 1, min(n_starts, usable)).astype(int)

    rows = []
    for risk in risks:
        outcomes = defaultdict(int)
        days_ok = []
        for s in starts:
            status, days, _ = run_challenge(seq, int(s), risk, target_pct, max_dd_pct, daily_dd_pct)
            outcomes[status] += 1
            if status == "reussi":
                days_ok.append(days)
        total = sum(outcomes.values())
        rows.append({
            "risk": risk,
            "pass_pct": outcomes["reussi"] / total * 100,
            "fail_total": outcomes["echec_dd_total"] / total * 100,
            "fail_daily": outcomes["echec_dd_jour"] / total * 100,
            "slow": (outcomes["trop_lent"] + outcomes["donnees_epuisees"]) / total * 100,
            "median_days": float(np.median(days_ok)) if days_ok else float("nan"),
        })
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=float, default=10.0, help="cible de gain en %%")
    p.add_argument("--max-dd", type=float, default=10.0, help="perte totale max en %%")
    p.add_argument("--daily-dd", type=float, default=5.0, help="perte journaliere max en %%")
    p.add_argument("--fee", type=float, default=500.0, help="cout du challenge en EUR")
    p.add_argument("--account", type=float, default=100000.0, help="taille du compte finance")
    p.add_argument("--split", type=float, default=0.80, help="part des gains conservee")
    args = p.parse_args()

    seq = build_trade_sequence()
    span = (seq[-1][0] - seq[0][0]) / 86400.0 / 365.25
    print("=" * 88)
    print(f"  SEQUENCE : {len(seq)} trades sur {span:.1f} ans, {len(seq)/span:.0f}/an")
    print(f"  REGLES   : cible +{args.target}% | perte totale max {args.max_dd}% | "
          f"perte jour max {args.daily_dd}%")
    print("=" * 88)

    risks = [0.25, 0.4, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    rows = sweep_risk(seq, args.target, args.max_dd, args.daily_dd, risks)

    print("\n  risque   reussite   echec DD total   echec DD jour   trop lent   delai median")
    print("  " + "-" * 82)
    for r in rows:
        d = f"{r['median_days']:.0f} j" if r["median_days"] == r["median_days"] else "  -"
        print(f"  {r['risk']:5.2f}%   {r['pass_pct']:7.1f}%   {r['fail_total']:13.1f}%   "
              f"{r['fail_daily']:12.1f}%   {r['slow']:8.1f}%   {d:>12s}")

    best = max(rows, key=lambda r: r["pass_pct"])
    print(f"\n  Meilleur reglage : risque {best['risk']}% -> {best['pass_pct']:.1f}% de reussite, "
          f"delai median {best['median_days']:.0f} jours")

    print("\n" + "=" * 88)
    print("  ESPERANCE ECONOMIQUE")
    print("=" * 88)
    ppass = best["pass_pct"] / 100.0
    # Un challenge en deux phases : il faut passer les deux.
    # La phase 2 a une cible plus basse, donc un taux de reussite superieur.
    rows2 = sweep_risk(seq, args.target / 2, args.max_dd, args.daily_dd, [best["risk"]])
    p2 = rows2[0]["pass_pct"] / 100.0
    combined = ppass * p2
    print(f"  reussite phase 1 (+{args.target}%)      : {ppass*100:.1f}%")
    print(f"  reussite phase 2 (+{args.target/2}%)       : {p2*100:.1f}%")
    print(f"  reussite des deux phases        : {combined*100:.1f}%")
    print(f"  cout moyen jusqu'au financement : {args.fee/max(combined,1e-9):,.0f} EUR "
          f"({1/max(combined,1e-9):.1f} tentatives en moyenne)".replace(",", " "))

    # Revenu une fois finance, au meme niveau de risque.
    ann = None
    balance = 100.0
    for _, _, r in seq:
        balance += balance * (best["risk"] / 100.0) * r
    ann = ((balance / 100.0) ** (1 / span) - 1) * 100
    monthly = args.account * (ann / 100.0) / 12 * args.split
    print(f"\n  rendement annualise a {best['risk']}%      : {ann:.1f}%")
    print(f"  sur un compte finance de {args.account:,.0f} EUR".replace(",", " "))
    print(f"  revenu mensuel brut             : {args.account*(ann/100)/12:,.0f} EUR".replace(",", " "))
    print(f"  revenu mensuel apres split {args.split*100:.0f}% : {monthly:,.0f} EUR".replace(",", " "))


if __name__ == "__main__":
    main()
