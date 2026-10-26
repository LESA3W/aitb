# research/wf_frequence.py
"""
A quelle FREQUENCE faut-il reoptimiser la strategie ?

L'hypothese de Paul (2026-09-12)
--------------------------------
« Tu ne peux pas faire une regle qui marche sur toutes les annees. La regle doit
s'adapter au marche presque tous les mois. Si elle marche sur 1 mois et qu'elle
est rentable, tu la laisses, et au fur et a mesure elle s'adapte. »

C'est une hypothese testable, et elle merite d'etre mesuree plutot que discutee.

Ce que le walk-forward faisait deja
-----------------------------------
Il reoptimise, mais lentement : avec 6 fenetres sur 6 ans, les parametres sont
rechoisis tous les ~10 mois. L'hypothese de Paul est qu'une reoptimisation
BEAUCOUP plus rapide ferait mieux.

Le contre-argument, lui aussi testable : sur 1 mois, une ligne H4 ne produit que
2 a 5 trades. Choisir des parametres sur 3 trades, c'est choisir sur du bruit.
Il existe donc probablement une frequence optimale, ni trop lente ni trop rapide.

Methode
-------
Fenetre glissante : on apprend sur les N derniers mois, on trade le mois (ou les
3 mois, ou les 10 mois) qui suivent, on avance, on recommence. Toujours sur des
donnees REELLES, jamais synthetiques.

On mesure, pour chaque frequence :
  - le profit factor out-of-sample cumule
  - la part de periodes gagnantes
  - le nombre moyen de trades par periode de test (pour voir si on optimise
    sur du bruit)

Usage:
    python research/wf_frequence.py --jobs 4
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
from aitb.backtest import BacktestConfig, Costs, backtest_strategy   # noqa: E402
from research.wf_search import meilleur_sur                 # noqa: E402

COSTS = Costs(commission_per_lot_round_turn=7.0, slippage_points=2.0)
SORTIE = PROJECT_ROOT / "research" / "wf_frequence.json"

# Bougies par mois environ. H4 : 6 bougies/jour x 22 jours. H1 : 24 x 22.
PAR_MOIS = {"H1": 480, "H4": 120}

# Frequences de reoptimisation testees, en mois.
FREQUENCES = (1, 2, 3, 6, 12)

# Duree d'apprentissage glissante, en mois. On garde la meme pour toutes les
# frequences afin de ne faire varier QU'UNE seule chose : la frequence.
APPRENTISSAGE_MOIS = 24


def _tache(args):
    sym, tf, fam, freq, risque = args
    try:
        rates, meta = md.load_history(sym, tf)
        specs = {"symbol": sym,
                 **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                         "volume_min", "volume_max", "volume_step")}}
        cfg = BacktestConfig(initial_balance=100000.0, risk_pct=risque, tp_r=2.0)

        par_mois = PAR_MOIS[tf]
        apprentissage = APPRENTISSAGE_MOIS * par_mois
        test = freq * par_mois
        n = len(rates)
        if n < apprentissage + test * 3:
            return None

        gains = pertes = 0.0
        periodes = positives = 0
        trades_par_periode = []

        debut = apprentissage
        while debut + test <= n:
            train = rates[debut - apprentissage:debut]
            bloc = rates[debut:debut + test]
            # mini_trades bas : sur 1 mois il y a peu de trades, on ne veut pas
            # que le filtre elimine mecaniquement les frequences rapides.
            choix = meilleur_sur(train, specs, tf, fam, cfg, mini_trades=8)
            if choix is None:
                debut += test
                continue
            _, variante, _ = choix
            try:
                r = backtest_strategy(variante, bloc, specs, tf, cfg, COSTS)
            except Exception:
                debut += test
                continue

            periodes += 1
            trades_par_periode.append(r.metrics["trades"])
            for t in r.trades:
                if t.r_multiple > 0:
                    gains += t.r_multiple
                else:
                    pertes -= t.r_multiple
            if r.metrics["return_pct"] > 0:
                positives += 1
            debut += test

        if periodes < 4:
            return None
        return {
            "symbol": sym, "timeframe": tf, "famille": fam, "freq_mois": freq,
            "periodes": periodes,
            "pct_positives": positives / periodes * 100,
            "pf_out": gains / pertes if pertes > 0 else 0.0,
            "trades_moyen": float(np.mean(trades_par_periode)),
            "trades_total": int(np.sum(trades_par_periode)),
        }
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--jobs", type=int, default=0)
    args = p.parse_args()

    # Echantillon representatif : les marches et familles qui etaient sortis en
    # tete de la derniere recherche, plus le book actuellement en live.
    ECHANTILLON = [
        ("HK50", "H4", "bpr"), ("GBPUSD", "H4", "rsi_reversion"),
        ("IT40", "H4", "momentum_roc"), ("USTEC", "H4", "trend_pullback"),
        ("ESP35", "H4", "bpr"), ("XAUUSD", "H4", "ema_trend"),
        ("USDCAD", "H1", "bb_reversion"), ("SWI20", "H4", "bpr"),
        ("EURUSD", "H1", "ema_trend"), ("USDJPY", "H1", "ema_trend"),
        ("AUDJPY", "H1", "ema_trend"), ("USDJPY", "H4", "vol_breakout"),
    ]

    taches = [(s, tf, f, freq, args.risk)
              for s, tf, f in ECHANTILLON for freq in FREQUENCES]
    workers = max(1, min(args.jobs or 4, len(taches)))

    print("=" * 92)
    print(f"  FREQUENCE DE REOPTIMISATION : {len(ECHANTILLON)} lignes x "
          f"{len(FREQUENCES)} frequences = {len(taches)} tests")
    print(f"  apprentissage glissant {APPRENTISSAGE_MOIS} mois, donnees REELLES uniquement")
    print("=" * 92, flush=True)

    res = []
    fait = 0
    t0 = time.time()
    with Pool(processes=workers) as pool:
        for b in pool.imap_unordered(_tache, taches, chunksize=1):
            fait += 1
            if b is not None:
                res.append(b)
            if fait % 10 == 0 or fait == len(taches):
                reste = (time.time() - t0) / fait * (len(taches) - fait)
                print("  ... %d/%d (%.0f min restantes)" % (fait, len(taches), reste / 60),
                      flush=True)

    SORTIE.write_text(json.dumps(res, indent=2), encoding="utf-8")

    print("\n" + "=" * 92)
    print("  MOYENNE SUR TOUTES LES LIGNES, PAR FREQUENCE")
    print("=" * 92)
    print("  %-22s %10s %12s %14s %12s"
          % ("reoptimisation", "PF out", "periodes+", "trades/periode", "lignes"))
    print("  " + "-" * 78)
    for freq in FREQUENCES:
        lot = [r for r in res if r["freq_mois"] == freq]
        if not lot:
            continue
        print("  tous les %-2d mois%-7s %9.3f %11.1f%% %13.1f %11d"
              % (freq, "", float(np.mean([r["pf_out"] for r in lot])),
                 float(np.mean([r["pct_positives"] for r in lot])),
                 float(np.mean([r["trades_moyen"] for r in lot])), len(lot)))

    print("\n  DETAIL PAR LIGNE (PF out selon la frequence)")
    print("  %-9s %-3s %-16s %s" % ("marche", "tf", "famille",
                                    " ".join("%5dm" % f for f in FREQUENCES)))
    print("  " + "-" * 78)
    for s, tf, f in ECHANTILLON:
        cells = []
        for freq in FREQUENCES:
            m = [r for r in res if (r["symbol"], r["timeframe"], r["famille"],
                                    r["freq_mois"]) == (s, tf, f, freq)]
            cells.append("%6.2f" % m[0]["pf_out"] if m else "   -- ")
        print("  %-9s %-3s %-16s %s" % (s, tf, f, " ".join(cells)))
    print(f"\n  Fichier : {SORTIE}")


if __name__ == "__main__":
    main()
