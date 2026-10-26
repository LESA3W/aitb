# research/wf_confirm.py
"""
Confirmation des survivantes sur PLUSIEURS decoupages temporels.

Pourquoi ce script est indispensable
------------------------------------
wf_search.py a tourne avec 5 fenetres. Or EURUSD H1 ema_trend passe a 80% de
fenetres positives avec 5 fenetres, alors qu'il echouait a 1 sur 8 avec le
decoupage a 8 fenetres du script d'origine. Meme donnee, meme strategie,
conclusion inverse. Cela veut dire qu'une partie du resultat depend du
decoupage, donc du hasard.

Une ligne qui merite d'etre tradee doit gagner out-of-sample QUEL QUE SOIT
l'endroit ou l'on coupe l'historique. Ce script rejoue chaque candidate sur
4, 6 et 8 fenetres et ne garde que celles qui passent PARTOUT.

Le test du bruit (wf_null.py) a mesure un ratio signal/bruit de 2.22x : sur 22
survivantes, une dizaine sont du hasard. Ce script est la pour les separer.

Usage:
    python research/wf_confirm.py --jobs 12
"""

import argparse
import json
import sys
import time
from functools import lru_cache
from multiprocessing import Pool, cpu_count
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                          # noqa: E402

from aitb import market_data as md                          # noqa: E402
from aitb.backtest import BacktestConfig                    # noqa: E402
from research.wf_search import walkforward, SORTIE          # noqa: E402

DECOUPAGES = (4, 6, 8)
CONFIRME = PROJECT_ROOT / "research" / "wf_confirmes.json"


@lru_cache(maxsize=None)
def _charger(sym, tf):
    rates, meta = md.load_history(sym, tf)
    return rates, {"symbol": sym,
                   **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                           "volume_min", "volume_max", "volume_step")}}


def _tache(args):
    sym, tf, fam, n_fen, risque = args
    try:
        rates, specs = _charger(sym, tf)
        cfg = BacktestConfig(initial_balance=100000.0, risk_pct=risque, tp_r=2.0)
        b = walkforward(sym, tf, fam, rates, specs, n_fen, cfg)
        if b is not None:
            b["n_fenetres_test"] = n_fen
        return b
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--jobs", type=int, default=0)
    args = p.parse_args()

    src = json.loads(SORTIE.read_text(encoding="utf-8"))
    candidates = src["survivants"]
    if not candidates:
        print("  Aucune candidate a confirmer.")
        return

    taches = [(c["symbol"], c["timeframe"], c["famille"], n, args.risk)
              for c in candidates for n in DECOUPAGES]
    workers = max(1, min(args.jobs or (cpu_count() - 4), len(taches)))

    print("=" * 96)
    print(f"  CONFIRMATION : {len(candidates)} candidates x {len(DECOUPAGES)} decoupages "
          f"{DECOUPAGES} = {len(taches)} tests")
    print("  une ligne n'est gardee que si elle gagne out-of-sample sur TOUS les decoupages")
    print("=" * 96, flush=True)

    par_ligne = {}
    fait = 0
    t0 = time.time()
    with Pool(processes=workers) as pool:
        for b in pool.imap_unordered(_tache, taches, chunksize=1):
            fait += 1
            if b is not None:
                cle = (b["symbol"], b["timeframe"], b["famille"])
                par_ligne.setdefault(cle, []).append(b)
            if fait % 12 == 0 or fait == len(taches):
                reste = (time.time() - t0) / fait * (len(taches) - fait)
                print("  ... %d/%d (%.0f s restantes)" % (fait, len(taches), reste),
                      flush=True)

    print("\n" + "=" * 96)
    print("  %-9s %-3s %-17s %s %10s %10s %9s"
          % ("marche", "tf", "famille", " ".join("%d fen" % n for n in DECOUPAGES),
             "PF min", "fen+ min", "verdict"))
    print("  " + "-" * 92)

    confirmes = []
    for c in sorted(candidates, key=lambda x: -x["r_total"]):
        cle = (c["symbol"], c["timeframe"], c["famille"])
        runs = par_ligne.get(cle, [])
        par_n = {r["n_fenetres_test"]: r for r in runs}
        cells = []
        pfs, pos = [], []
        complet = True
        for n in DECOUPAGES:
            r = par_n.get(n)
            if r is None:
                cells.append("  --  ")
                complet = False
                continue
            cells.append("%5.2f " % r["pf_out"])
            pfs.append(r["pf_out"])
            pos.append(r["pct_positives"])

        if complet and pfs:
            pf_min = min(pfs)
            pos_min = min(pos)
            # Exigence : rentable sur TOUS les decoupages, et jamais sous la
            # moitie des fenetres. Une ligne qui ne tient que sur un decoupage
            # precis n'est pas une strategie, c'est un artefact.
            ok = pf_min >= 1.05 and pos_min >= 50.0
        else:
            pf_min, pos_min, ok = 0.0, 0.0, False

        if ok:
            confirmes.append({**c, "pf_min": pf_min, "pos_min": pos_min})
        print("  %-9s %-3s %-17s %s %9.2f %9.0f%% %9s"
              % (c["symbol"], c["timeframe"], c["famille"], " ".join(cells),
                 pf_min, pos_min, "GARDE" if ok else "rejete"))

    CONFIRME.write_text(json.dumps(confirmes, indent=2), encoding="utf-8")
    print("\n" + "=" * 96)
    print(f"  {len(confirmes)} lignes confirmees sur {len(candidates)} candidates")
    print("=" * 96)
    for c in sorted(confirmes, key=lambda x: -x["pf_min"]):
        print("  %-9s %-3s %-17s PF le pire des decoupages %.2f, fenetres+ mini %.0f%%"
              % (c["symbol"], c["timeframe"], c["famille"], c["pf_min"], c["pos_min"]))
    if not confirmes:
        print("  AUCUNE ligne ne tient sur les trois decoupages.")
        print("  Conclusion : les 22 survivantes etaient des artefacts de decoupage.")
    print(f"\n  Fichier : {CONFIRME}")


if __name__ == "__main__":
    main()
