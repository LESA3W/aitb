# research/build_book.py
"""
Selection automatique du portefeuille a partir des resultats de sweep.

Le probleme
-----------
Un sweep sort une liste de strategies qui passent la grille d'acceptation, mais
prendre les N meilleures est une erreur : les meilleures se ressemblent souvent
(meme famille, meme marche, parametres voisins) et perdent donc en meme temps.
Un portefeuille de clones ne diversifie rien.

Ce qui compte n'est pas la performance individuelle mais l'apport MARGINAL de
chaque ligne au portefeuille. Une strategie moyenne mais decorrelee vaut mieux
qu'une excellente qui duplique une ligne existante.

Methode
-------
Selection gloutonne. On part de la meilleure ligne, puis a chaque tour on ajoute
la candidate qui maximise le ratio rendement sur drawdown du portefeuille
COMPLET, en refusant toute candidate trop correlee a une ligne deja retenue.

Garde-fous :
  - une seule variante par couple marche + famille, sinon on empile des clones
  - correlation maximale autorisee configurable
  - la ligne doit ameliorer le ratio, sinon on l'ecarte

Usage:
    python research/build_book.py
    python research/build_book.py --max-corr 0.3 --max-lines 12
"""

import argparse
import ast
import csv
import glob
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

COSTS = Costs(commission_per_lot_round_turn=7.0, slippage_points=2.0)


def parse_strategy(label: str):
    """Transforme 'ema_trend(fast=21,slow=100)' en ('ema_trend', {...})."""
    if "(" not in label:
        return label, {}
    name, rest = label.split("(", 1)
    rest = rest.rstrip(")")
    params = {}
    for chunk in rest.split(","):
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        try:
            params[k.strip()] = ast.literal_eval(v.strip())
        except Exception:
            params[k.strip()] = v.strip()
    return name.strip(), params


def load_candidates(csv_path=None, min_trades=100, max_dd=20.0, min_pf=1.3):
    """Lit le dernier sweep et retourne les lignes qui passent la grille."""
    if csv_path is None:
        files = sorted(glob.glob(str(PROJECT_ROOT / "data" / "research" / "sweep_*.csv")))
        if not files:
            raise SystemExit("Aucun sweep trouve. Lance d'abord research/sweep.py")
        csv_path = files[-1]

    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    out = []
    for r in rows:
        try:
            if int(r["trades"]) < min_trades:
                continue
            if float(r["max_drawdown_pct"]) > max_dd:
                continue
            if float(r["profit_factor"]) < min_pf:
                continue
            if float(r["return_pct"]) <= float(r["buy_hold_pct"]):
                continue
        except Exception:
            continue
        name, params = parse_strategy(r["strategy"])
        out.append({
            "symbol": r["symbol"], "timeframe": r["timeframe"],
            "name": name, "params": params,
            "pf": float(r["profit_factor"]),
            "ret": float(r["return_pct"]),
            "dd": float(r["max_drawdown_pct"]),
            "trades": int(r["trades"]),
        })
    return csv_path, out


def dedupe(cands):
    """Garde la meilleure variante par couple marche + timeframe + famille."""
    best = {}
    for c in cands:
        key = (c["symbol"], c["timeframe"], c["name"])
        score = (c["pf"] - 1.0) * c["ret"] / max(c["dd"], 1.0)
        if key not in best or score > best[key][0]:
            best[key] = (score, c)
    return [c for _, c in sorted(best.values(), key=lambda x: -x[0])]


def trades_of(c, risk_pct=1.0, tp_r=2.0):
    """Sequence (timestamp, R) d'une candidate."""
    rates, meta = md.load_history(c["symbol"], c["timeframe"])
    specs = {"symbol": c["symbol"],
             **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                     "volume_min", "volume_max", "volume_step")}}
    res = backtest_strategy(
        st.build(c["name"], **c["params"]), rates, specs, c["timeframe"],
        BacktestConfig(1000.0, risk_pct, tp_r), COSTS,
    )
    return [(t.time_out, t.r_multiple) for t in res.trades]


def monthly_series(trades, months):
    b = {m: 0.0 for m in months}
    for ts, r in trades:
        k = datetime.fromtimestamp(ts).strftime("%Y-%m")
        if k in b:
            b[k] += r
    return np.array([b[m] for m in months])


def portfolio_stats(selected_trades, risk_pct=1.0, initial=1000.0):
    merged = []
    for tr in selected_trades:
        merged.extend(tr)
    if len(merged) < 10:
        return None
    merged.sort(key=lambda x: x[0])

    bal = initial
    eq = [initial]
    for _, r in merged:
        bal += bal * (risk_pct / 100.0) * r
        eq.append(bal)
    eq = np.array(eq)
    peak = np.maximum.accumulate(eq)
    dd = float(((peak - eq) / np.where(peak > 0, peak, 1.0)).max() * 100)
    years = max((merged[-1][0] - merged[0][0]) / 86400.0 / 365.25, 0.01)
    ann = float(((eq[-1] / initial) ** (1 / years) - 1) * 100)
    return {"annualised": ann, "max_dd": dd, "ratio": ann / max(dd, 0.01),
            "trades": len(merged), "years": years}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=None)
    p.add_argument("--max-corr", type=float, default=0.4)
    p.add_argument("--max-lines", type=int, default=12)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--min-pf", type=float, default=1.3)
    args = p.parse_args()

    csv_path, cands = load_candidates(args.csv, min_pf=args.min_pf)
    print(f"  Source : {csv_path}")
    print(f"  {len(cands)} lignes passent la grille, ", end="")
    cands = dedupe(cands)
    print(f"{len(cands)} apres deduplication par marche + famille\n")
    if not cands:
        raise SystemExit("  Aucune candidate. Elargis la recherche ou baisse --min-pf.")

    print("  Candidates disponibles :")
    for c in cands[:30]:
        print(f"    {c['symbol']:8s} {c['timeframe']:3s} {c['name']:16s} "
              f"PF={c['pf']:5.2f} ret={c['ret']:7.1f}% DD={c['dd']:5.1f}% n={c['trades']:4d}")

    print("\n  Calcul des sequences de trades...")
    for c in cands:
        c["trades_seq"] = trades_of(c, args.risk)

    months = sorted({datetime.fromtimestamp(ts).strftime("%Y-%m")
                     for c in cands for ts, _ in c["trades_seq"]})
    for c in cands:
        c["series"] = monthly_series(c["trades_seq"], months)

    # ---- selection gloutonne ----
    selected = [cands[0]]
    print(f"\n  1. {cands[0]['symbol']} {cands[0]['timeframe']} {cands[0]['name']}  (meilleure ligne)")

    while len(selected) < args.max_lines:
        best = None
        for c in cands:
            if c in selected:
                continue
            corrs = []
            for s in selected:
                if c["series"].std() and s["series"].std():
                    corrs.append(abs(np.corrcoef(c["series"], s["series"])[0, 1]))
            if corrs and max(corrs) > args.max_corr:
                continue
            stats = portfolio_stats([x["trades_seq"] for x in selected] + [c["trades_seq"]], args.risk)
            if stats is None:
                continue
            if best is None or stats["ratio"] > best[0]["ratio"]:
                best = (stats, c, max(corrs) if corrs else 0.0)

        if best is None:
            print(f"\n  Plus aucune candidate sous une correlation de {args.max_corr}.")
            break

        stats, c, corr = best
        current = portfolio_stats([x["trades_seq"] for x in selected], args.risk)
        if current and stats["ratio"] <= current["ratio"]:
            print(f"\n  Aucune ligne restante n'ameliore le ratio ({current['ratio']:.2f}).")
            break

        selected.append(c)
        print(f"  {len(selected)}. {c['symbol']:8s} {c['timeframe']:3s} {c['name']:16s} "
              f"corr max {corr:.2f}  ->  ratio portefeuille {stats['ratio']:.2f} "
              f"({stats['annualised']:.1f}% / DD {stats['max_dd']:.1f}%)")

    # ---- resultat ----
    final = portfolio_stats([x["trades_seq"] for x in selected], args.risk)
    solo = portfolio_stats([selected[0]["trades_seq"]], args.risk)

    print("\n" + "=" * 88)
    print(f"  PORTEFEUILLE FINAL : {len(selected)} lignes, risque {args.risk}% par trade")
    print("=" * 88)
    print(f"  meilleure ligne seule : {solo['annualised']:6.1f}% / DD {solo['max_dd']:5.1f}%  "
          f"ratio {solo['ratio']:.2f}")
    print(f"  portefeuille          : {final['annualised']:6.1f}% / DD {final['max_dd']:5.1f}%  "
          f"ratio {final['ratio']:.2f}")
    print(f"  {final['trades']} trades sur {final['years']:.1f} ans, "
          f"soit {final['trades']/final['years']:.0f} par an")
    if solo["ratio"] > 0:
        print(f"  amelioration du ratio : x{final['ratio']/solo['ratio']:.2f}")

    print("\n  Revenus mensuels selon le capital, au risque retenu :")
    for cap in (1000, 10000, 50000, 100000):
        print(f"    {cap:>7,d} EUR -> {cap*final['annualised']/100/12:>8,.0f} EUR/mois".replace(",", " "))

    print("\n" + "=" * 88)
    print("  A COLLER DANS config/settings.py")
    print("=" * 88)
    print('    "LIVE_BOOK": [')
    for c in selected:
        params = ", ".join(f'"{k}": {v!r}' for k, v in sorted(c["params"].items()))
        print(f'        {{"symbol": "{c["symbol"]}", "timeframe": "{c["timeframe"]}", '
              f'"strategy": "{c["name"]}",')
        print(f'         "params": {{{params}}}}},')
    print("    ],")
    print(f'    "MAX_OPEN_POSITIONS": {len(selected)},')


if __name__ == "__main__":
    main()
