# research/funding_arb.py
"""
Arbitrage de financement sur perpetuels (cash and carry).

Pourquoi cette piste est differente de tout ce qu'on a teste
------------------------------------------------------------
Les 25 000 backtests de la journee cherchaient un PATTERN dans les prix. Aucun
n'a survecu : walk-forward negatif, alpha inexistant, resultats portes par une
poignee de trades. C'est le resultat attendu, chercher un motif predictif dans
les prix est extremement difficile.

L'arbitrage de financement ne cherche aucun motif. Il exploite une MECANIQUE
contractuelle : sur un contrat perpetuel, il n'y a pas d'echeance, donc
l'exchange force la convergence vers le spot en faisant payer un taux de
financement toutes les 8 heures. Quand plus de monde est long que short, ce
qui est le cas la plupart du temps, les longs paient les shorts.

La position :
    achat au comptant de X BTC   +   vente d'un perpetuel de X BTC
    -> exposition au prix NULLE, les deux jambes s'annulent
    -> on encaisse le financement toutes les 8 heures

Ce n'est pas une prediction, c'est un loyer. C'est la raison pour laquelle
cette strategie produit un revenu regulier la ou le suivi de tendance produit
des annees blanches.

Risques reels, non modelisables ici :
  - le financement peut devenir negatif (on paie au lieu d'encaisser)
  - risque de contrepartie sur l'exchange
  - la jambe short peut etre liquidee si la marge est mal geree
  - retrait ou delisting d'une paire

ATTENTION, LA COLONNE "brut filtre" EST TROMPEUSE (corrige le 2026-09-12)
-------------------------------------------------------------------------
Elle suppose qu'on n'encaisse que les financements positifs, en ignorant le
cout de sortie et de re-entree. Mesure faite sur OKX : eviter les versements
negatifs demande 224 a 437 allers-retours par an selon la paire. A 0,30 % le
flip, les deux jambes comprises, cela coute 67 a 131 % du capital par an.

  moyenne sur 10 paires :  toujours investi  +3,21 %/an
                           filtre naif       +5,12 %/an   (faux)
                           filtre avec couts -93,09 %/an

La strategie correcte est donc de RESTER INVESTI en permanence et d'encaisser
les rares versements negatifs. Seule la colonne "brut naif" est exploitable.

Usage:
    python research/funding_arb.py
    python research/funding_arb.py --top 30 --months 24
"""

import argparse
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np   # noqa: E402

FAPI = "https://fapi.binance.com"

# Frais aller-retour pour monter ET demonter la position complete.
# Spot taker 0.10% x2 jambes + perp taker 0.05% x2 jambes, arrondi large.
ROUND_TRIP_COST_PCT = 0.35


def _get(url, retries=3):
    last = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aitb-research/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e
            time.sleep(1.2 * (k + 1))
    raise RuntimeError(f"Binance injoignable: {last}")


def perp_symbols(limit=40):
    """Perpetuels USDT les plus liquides, seuls tradables sans slippage delirant."""
    data = _get(f"{FAPI}/fapi/v1/ticker/24hr")
    usdt = [d for d in data if d["symbol"].endswith("USDT")]
    usdt.sort(key=lambda d: float(d["quoteVolume"]), reverse=True)
    return [(d["symbol"], float(d["quoteVolume"])) for d in usdt[:limit]]


def funding_history(symbol, months=24):
    """Historique complet du financement, pagine par 1000 points de 8h."""
    end = int(time.time() * 1000)
    start = end - int(months * 30.44 * 86400 * 1000)
    out = []
    cursor = start
    while cursor < end:
        url = f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}&startTime={cursor}&limit=1000"
        try:
            chunk = _get(url)
        except Exception:
            break
        if not chunk:
            break
        out.extend(chunk)
        nxt = chunk[-1]["fundingTime"] + 1
        if nxt <= cursor:
            break
        cursor = nxt
        if len(chunk) < 1000:
            break
        time.sleep(0.10)
    return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in out]


def analyse(symbol, months):
    fh = funding_history(symbol, months)
    if len(fh) < 500:
        return None
    fh.sort()
    rates = np.array([r for _, r in fh])
    times = [t for t, _ in fh]

    span_years = (times[-1] - times[0]) / 1000 / 86400 / 365.25
    periods_per_year = len(rates) / max(span_years, 0.01)

    # Strategie naive : on encaisse TOUT, y compris le financement negatif.
    gross_naive = rates.mean() * periods_per_year * 100

    # Strategie filtree : on ne tient la position que lorsque le financement
    # recent est positif. C'est ce que fait un desk en pratique.
    kept = rates[rates > 0]
    share_positive = len(kept) / len(rates)
    gross_filtered = kept.mean() * len(kept) / max(span_years, 0.01) * 100

    # Stabilite annuelle : combien d'annees auraient ete positives.
    per_year = defaultdict(list)
    for t, r in fh:
        per_year[datetime.fromtimestamp(t / 1000).year].append(r)
    yearly = {y: float(np.sum(v)) * 100 for y, v in per_year.items()}
    years_pos = sum(1 for v in yearly.values() if v > 0)

    return {
        "symbol": symbol,
        "points": len(rates),
        "years": span_years,
        "naive_pct": gross_naive,
        "filtered_pct": gross_filtered,
        "share_positive": share_positive * 100,
        "worst_8h": rates.min() * 100,
        "best_8h": rates.max() * 100,
        "years_pos": years_pos,
        "years_tot": len(yearly),
        "yearly": yearly,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--months", type=int, default=24)
    p.add_argument("--capital", type=float, default=10000.0)
    args = p.parse_args()

    print("Recuperation de la liste des perpetuels les plus liquides...")
    syms = perp_symbols(args.top)
    print(f"  {len(syms)} paires, de {syms[0][0]} a {syms[-1][0]}\n")

    print("=" * 104)
    print(f"  ARBITRAGE DE FINANCEMENT — historique {args.months} mois, frais {ROUND_TRIP_COST_PCT}% aller-retour")
    print("=" * 104)
    print(f"  {'paire':14s} {'pts':>6s} {'ans':>5s} {'brut naif':>11s} {'brut filtre':>12s} "
          f"{'% pos':>7s} {'pire 8h':>9s} {'ans+':>7s}")
    print("  " + "-" * 100)

    rows = []
    for sym, vol in syms:
        try:
            a = analyse(sym, args.months)
        except Exception:
            continue
        if not a:
            continue
        rows.append(a)
        print(f"  {sym:14s} {a['points']:>6d} {a['years']:>5.1f} {a['naive_pct']:>10.2f}% "
              f"{a['filtered_pct']:>11.2f}% {a['share_positive']:>6.1f}% "
              f"{a['worst_8h']:>8.3f}% {a['years_pos']}/{a['years_tot']:<4d}")

    if not rows:
        print("  Aucune donnee exploitable.")
        return

    print("\n" + "=" * 104)
    print("  PORTEFEUILLE : les N meilleures paires en equipondere")
    print("=" * 104)
    ranked = sorted(rows, key=lambda r: -r["naive_pct"])
    for n in (1, 3, 5, 10, 20):
        sel = ranked[:n]
        if len(sel) < n:
            continue
        naive = float(np.mean([r["naive_pct"] for r in sel]))
        filt = float(np.mean([r["filtered_pct"] for r in sel]))
        # Cout amorti : on suppose un remontage de position par mois.
        net_naive = naive - ROUND_TRIP_COST_PCT * 12
        net_filt = filt - ROUND_TRIP_COST_PCT * 12
        print(f"  {n:2d} paires : brut naif {naive:6.2f}%/an  net {net_naive:6.2f}%/an  |  "
              f"brut filtre {filt:6.2f}%/an  net {net_filt:6.2f}%/an")

    print("\n" + "=" * 104)
    print("  CAPITAL NECESSAIRE POUR 200$/SEMAINE = 10 400$/AN")
    print("=" * 104)
    best10 = float(np.mean([r["naive_pct"] for r in ranked[:10]])) - ROUND_TRIP_COST_PCT * 12
    best10f = float(np.mean([r["filtered_pct"] for r in ranked[:10]])) - ROUND_TRIP_COST_PCT * 12
    for lab, rate in (("10 paires, non filtre", best10), ("10 paires, filtre", best10f)):
        if rate <= 0:
            print(f"  {lab:24s} rendement negatif, inexploitable")
            continue
        need = 10400 / (rate / 100)
        print(f"  {lab:24s} {rate:6.2f}%/an  ->  capital necessaire {need:>12,.0f}$".replace(",", " "))


if __name__ == "__main__":
    main()
