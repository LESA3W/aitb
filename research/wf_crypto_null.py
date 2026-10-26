# research/wf_crypto_null.py
"""
Combien des 7 lignes crypto survivraient sur du BRUIT PUR ?

Pourquoi ce test decide de tout
-------------------------------
Le protocole a 9 criteres a retenu 7 combinaisons sur 498 evaluables, soit
1,4 %. Le portefeuille sort a PF 1,57, drawdown 4,9 %, 1,35 trade par semaine,
correlation moyenne 0,02, et 214 EUR/semaine sur un compte finance de 100 000.

C'est le meilleur resultat du projet. C'est exactement le moment ou il faut
arreter de se rejouir et mesurer le taux de faux positifs, parce que c'est aussi
exactement le moment ou je me suis trompe les trois fois precedentes.

Methode
-------
On fabrique des marches synthetiques a partir des vrais : les rendements bougie
a bougie sont retires au sort AVEC REMISE. La volatilite, la distribution et les
queues epaisses sont conservees, l'autocorrelation est detruite. Il n'y a donc
plus rien a exploiter, par construction. Tout ce qui « passe » le filtre est un
faux positif.

  taux bruit tres inferieur a 1,4 %  ->  les 7 lignes portent du vrai signal
  taux bruit proche de 1,4 %         ->  c'est du hasard, on arrete

Usage:
    python research/wf_crypto_null.py --jobs 5
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
from aitb import strategies as st                           # noqa: E402
from aitb.backtest import BacktestConfig, Costs             # noqa: E402
from research import wf_search                              # noqa: E402
from research.wf_crypto import CRYPTOS, TIMEFRAMES, SORTIE  # noqa: E402
from research.wf_null import synthetique                    # noqa: E402

GRAINE = 20260912


@lru_cache(maxsize=2)
def _charger_faux(sym, tf, graine):
    rates, meta = md.load_history(sym, tf)
    faux = synthetique(rates, graine)
    if faux is None:
        return None, None, None
    specs = {"symbol": sym,
             **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                     "volume_min", "volume_max", "volume_step")}}
    prix_median = float(np.median(rates["close"]))
    costs = Costs(commission_per_lot_round_turn=0.0,
                  commission_pct_round_turn=0.10,
                  spread_points=max(1.0, (0.0002 * prix_median) / specs["point"]),
                  slippage_points=max(1.0, (0.0001 * prix_median) / specs["point"]))
    return faux, specs, costs


def _tache(args):
    sym, tf, fam, n_fen, risque, graine = args
    try:
        rates, specs, costs = _charger_faux(sym, tf, graine)
        if rates is None:
            return None
        cfg = BacktestConfig(initial_balance=100000.0, risk_pct=risque, tp_r=2.0)
        anc = wf_search.COSTS
        wf_search.COSTS = costs
        try:
            return wf_search.walkforward(sym, tf, fam, rates, specs, n_fen, cfg)
        finally:
            wf_search.COSTS = anc
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--windows", type=int, default=6)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--jobs", type=int, default=5)
    args = p.parse_args()

    familles = list(st.REGISTRY)
    taches = [(s, tf, f, args.windows, args.risk, GRAINE)
              for s in CRYPTOS for tf in TIMEFRAMES for f in familles]
    workers = max(1, min(args.jobs, len(taches)))

    print("=" * 92)
    print(f"  HYPOTHESE NULLE CRYPTO : {len(taches)} combinaisons sur marches SYNTHETIQUES")
    print("  meme volatilite, autocorrelation detruite -> aucun edge possible")
    print("=" * 92, flush=True)

    evaluables, faux = 0, []
    fait, t0 = 0, time.time()
    with Pool(processes=workers) as pool:
        for b in pool.imap_unordered(_tache, taches, chunksize=1):
            fait += 1
            if b is not None:
                evaluables += 1
                if wf_search.retenu(b):
                    faux.append(b)
            if fait % 40 == 0 or fait == len(taches):
                reste = (time.time() - t0) / fait * (len(taches) - fait)
                print("  ... %d/%d (%.0f min restantes, %d evaluables, %d faux positifs)"
                      % (fait, len(taches), reste / 60, evaluables, len(faux)), flush=True)

    reel = json.loads(SORTIE.read_text(encoding="utf-8"))
    n_ev, n_ok = len(reel["tous"]), len(reel["survivants"])
    taux_reel = n_ok / n_ev * 100 if n_ev else 0.0
    taux_bruit = len(faux) / evaluables * 100 if evaluables else 0.0

    print("\n" + "=" * 92)
    print("  RESULTAT")
    print("=" * 92)
    print("  %-32s %11s %10s %9s" % ("", "evaluables", "retenues", "taux"))
    print("  %-32s %11d %10d %8.2f%%" % ("marches REELS", n_ev, n_ok, taux_reel))
    print("  %-32s %11d %10d %8.2f%%" % ("marches SYNTHETIQUES", evaluables,
                                         len(faux), taux_bruit))
    print()
    if taux_bruit <= 0.001:
        print("  Le bruit ne produit AUCUN faux positif.")
        print("  Les 7 lignes portent donc tres probablement un vrai signal.")
    else:
        ratio = taux_reel / taux_bruit
        print("  ratio reel / bruit : %.2fx" % ratio)
        attendus = n_ev * taux_bruit / 100
        print("  sur %d essais reels, %.1f faux positifs attendus pour %d retenues"
              % (n_ev, attendus, n_ok))
        if ratio < 1.3:
            print("\n  VERDICT : du hasard. On arrete.")
        elif ratio < 2.5:
            print("\n  VERDICT : signal faible, une partie des 7 est du bruit.")
        else:
            print("\n  VERDICT : le reel domine nettement le bruit. Il y a du signal.")


if __name__ == "__main__":
    main()
