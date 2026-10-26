# research/crypto_reopt.py
"""
Le test qui decide : les 7 lignes crypto tiennent-elles avec une reoptimisation
REALISTE, celle qu'on peut reellement coder dans un bot ?

Pourquoi c'est LE point bloquant
--------------------------------
Tous les chiffres du walk-forward (PF 1,57, drawdown 4,9 %, 214 EUR/semaine sur
100 000) viennent d'une procedure qui rechoisit les parametres a chaque fenetre.
Le walk-forward decoupe l'historique en 6 morceaux, donc il reoptimise environ
tous les 13 mois sur 7,5 ans de donnees.

En live, il faut choisir une frequence et s'y tenir. Et on a deja vu ce piege :
le book forex donnait PF 1,80 en walk-forward et **1,01** avec des parametres
figes. Si le meme effondrement se produit ici, tout le resultat crypto tombe.

Ce que ce script mesure
-----------------------
Le portefeuille des 7 lignes, rejoue avec une reoptimisation tous les 1, 2, 3, 6
et 12 mois, sur un historique EXPANSIF (on garde tout le passe, ce qu'un bot
ferait naturellement). On compare a la version parametres FIGES, qui est le pire
cas, et au walk-forward de reference.

Lecture :
  - si le PF reste au-dessus de ~1,4 a une frequence codable, le resultat tient
  - s'il s'effondre vers 1,0 comme en forex, on arrete la piste crypto

Usage:
    python research/crypto_reopt.py --jobs 5
"""

import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                          # noqa: E402

from aitb.backtest import BacktestConfig, backtest_strategy  # noqa: E402
from research import wf_search                              # noqa: E402
from research.wf_crypto import _charger, SORTIE             # noqa: E402

CFG = BacktestConfig(initial_balance=100000.0, risk_pct=1.0, tp_r=2.0)

# Bougies par mois. H4 : 6 par jour en crypto (24/7). D1 : 1 par jour.
PAR_MOIS = {"H4": 183, "D1": 30}
FREQUENCES = (1, 2, 3, 6, 12)       # mois entre deux reoptimisations
APPRENTISSAGE_MINI_MOIS = 18        # on ne trade pas avant d'avoir vu ca


def _rejouer(args):
    """Rejoue une ligne avec reoptimisation tous les `freq` mois."""
    sym, tf, fam, freq = args
    try:
        rates, specs, costs = _charger(sym, tf)
        pm = PAR_MOIS[tf]
        debut = APPRENTISSAGE_MINI_MOIS * pm
        pas = freq * pm
        n = len(rates)
        if n < debut + pas * 3:
            return (sym, tf, fam, freq, [])

        anc = wf_search.COSTS
        wf_search.COSTS = costs
        trades = []
        try:
            curseur = debut
            while curseur + pas <= n:
                # Historique EXPANSIF : un bot garde tout son passe.
                ch = wf_search.meilleur_sur(rates[:curseur], specs, tf, fam,
                                            CFG, mini_trades=8)
                if ch is not None:
                    _, var, _ = ch
                    try:
                        r = backtest_strategy(var, rates[curseur:curseur + pas],
                                              specs, tf, CFG, costs)
                        trades += [(t.time_out, t.r_multiple) for t in r.trades]
                    except Exception:
                        pass
                curseur += pas
        finally:
            wf_search.COSTS = anc
        return (sym, tf, fam, freq, trades)
    except Exception:
        return (sym, tf, fam, freq, [])


def _stats(trades, risque=0.5):
    if not trades:
        return None
    trades = sorted(trades)
    g = sum(r for _, r in trades if r > 0)
    p = -sum(r for _, r in trades if r < 0)
    bal, pic, dd = 1.0, 1.0, 0.0
    for _, r in trades:
        bal *= (1 + risque / 100.0 * r)
        pic = max(pic, bal)
        dd = max(dd, (pic - bal) / pic)
    span = (trades[-1][0] - trades[0][0]) / 86400.0
    sem = max(span / 7.0, 1)
    return {"n": len(trades), "pf": g / p if p else 0.0,
            "dd": dd * 100, "R": sum(r for _, r in trades),
            "semaines": sem, "par_sem": (bal - 1.0) * 100000.0 / sem,
            "trades_sem": len(trades) / sem}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=5)
    args = ap.parse_args()

    lignes = json.loads(SORTIE.read_text(encoding="utf-8"))["survivants"]
    lignes = [(b["symbol"], b["timeframe"], b["famille"]) for b in lignes]

    taches = [(s, tf, f, freq) for s, tf, f in lignes for freq in FREQUENCES]
    print("=" * 88)
    print(f"  REOPTIMISATION REALISTE : {len(lignes)} lignes x "
          f"{len(FREQUENCES)} frequences = {len(taches)} rejeux")
    print("  historique expansif, comme le ferait un bot en production")
    print("=" * 88, flush=True)

    par_freq = {f: [] for f in FREQUENCES}
    fait, t0 = 0, time.time()
    with Pool(processes=args.jobs) as pool:
        for sym, tf, fam, freq, tr in pool.imap_unordered(_rejouer, taches, chunksize=1):
            fait += 1
            par_freq[freq] += tr
            if fait % 5 == 0 or fait == len(taches):
                reste = (time.time() - t0) / fait * (len(taches) - fait)
                print("  ... %d/%d (%.0f min restantes)"
                      % (fait, len(taches), reste / 60), flush=True)

    print("\n" + "=" * 88)
    print("  PORTEFEUILLE DES 7 LIGNES, PAR FREQUENCE DE REOPTIMISATION")
    print("  (risque 0,5 %, compte 100 000 EUR)")
    print("=" * 88)
    print("  %-22s %7s %8s %9s %10s %13s %14s"
          % ("reoptimisation", "trades", "PF", "DD max", "R total",
             "trades/sem", "EUR/semaine"))
    print("  " + "-" * 84)
    for f in FREQUENCES:
        s = _stats(par_freq[f])
        if not s:
            print("  tous les %-2d mois%-8s pas assez de donnees" % (f, ""))
            continue
        print("  tous les %-2d mois%-7s %7d %8.2f %8.1f%% %10.1f %12.2f %12.0f E"
              % (f, "", s["n"], s["pf"], s["dd"], s["R"],
                 s["trades_sem"], s["par_sem"]))

    print("\n  Reference walk-forward (reoptimisation ~tous les 13 mois) :")
    print("    PF 1,57  |  DD 4,9 %  |  1,35 trade/semaine  |  214 EUR/semaine")
    print("\n  Rappel du precedent forex : PF 1,80 en walk-forward, 1,01 a parametres")
    print("  figes. Si le meme effondrement apparait ici, la piste crypto tombe.")


if __name__ == "__main__":
    main()
