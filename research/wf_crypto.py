# research/wf_crypto.py
"""
Le protocole a 9 criteres, applique a la CRYPTO.

Pourquoi ce test manquait
-------------------------
Le verdict « pas d'edge » a ete rendu sur ~4 500 combinaisons FOREX. La crypto
avait bien ete testee, mais avec un protocole plus ancien et plus faible, celui
qui avait conclu a la loterie DOGE (un trade a +1052 R portant 90 % du resultat).
Elle n'a jamais subi le filtre durci : drawdown sous 8 %, PF gagnant a la hausse
ET a la baisse, au moins 12 ventes, et survie au retrait des 5 meilleurs trades.

C'est la seule voie restante ou le capital peut etre EMPRUNTE : les prop firms
crypto financent le directionnel, alors qu'elles interdisent toutes l'arbitrage.
Avec 500 EUR, c'est la difference entre 0,45 EUR et quelque chose d'exploitable.

Le piege des couts, corrige ici
-------------------------------
wf_search.py facture 7 USD de commission PAR LOT, plus 2 points de slippage.
C'est du forex. En crypto la commission est un POURCENTAGE du notionnel, il n'y
a pas de notion de lot, et le spread se mesure en points de base du prix. Un
BTC a 77 000 et un DOGE a 0,20 n'ont rien de comparable en points.

Facturation retenue, volontairement pessimiste :
  - commission aller-retour 0,10 % du notionnel (taker 0,05 % par jambe)
  - spread 2 points de base du prix median
  - slippage 1 point de base

A cela s'ajoute, en reel, le financement paye tant qu'on tient la position. La
plupart des prop firms crypto le repercutent dans le drawdown. Il n'est pas
modelise ici, donc les resultats sont un PLAFOND, pas une prevision.

Usage:
    python research/wf_crypto.py --jobs 6
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
import aitb.backtest as bt                                  # noqa: E402
from research import wf_search                              # noqa: E402

SORTIE = PROJECT_ROOT / "research" / "wf_crypto_survivors.json"

def _cryptos_en_cache():
    """
    Toutes les cryptos du cache, pas une liste figee.

    La version initiale testait 7 marches en dur et en retenait 2, soit 28 %.
    J'en avais conclu que la cadence de 15 trades/an rendait la strategie
    infinancable. Mais 15 trades/an, c'etait la cadence de DEUX lignes, pas de
    la strategie. Si le taux de 28 % tient, 44 marches donnent une douzaine de
    lignes, donc six fois la cadence. Le blocage venait peut-etre de la taille
    de mon echantillon, pas de la strategie.
    """
    hist = PROJECT_ROOT / "data" / "history"
    vus = set()
    for f in hist.glob("*USDT_D1.csv"):
        vus.add(f.stem.rsplit("_", 1)[0])
    return sorted(vus)


CRYPTOS = _cryptos_en_cache()
TIMEFRAMES = ["H4", "D1"]


@lru_cache(maxsize=3)
def _charger(sym, tf):
    rates, meta = md.load_history(sym, tf)
    specs = {"symbol": sym,
             **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                     "volume_min", "volume_max", "volume_step")}}
    # Couts en POURCENTAGE, pas par lot : c'est la difference structurelle
    # entre un marche crypto et le forex.
    prix_median = float(np.median(rates["close"]))
    costs = Costs(
        commission_per_lot_round_turn=0.0,
        commission_pct_round_turn=0.10,
        spread_points=max(1.0, (0.0002 * prix_median) / specs["point"]),
        slippage_points=max(1.0, (0.0001 * prix_median) / specs["point"]),
    )
    return rates, specs, costs


def _tache(args):
    sym, tf, fam, n_fen, risque = args
    try:
        rates, specs, costs = _charger(sym, tf)
        cfg = BacktestConfig(initial_balance=100000.0, risk_pct=risque, tp_r=2.0)
        # wf_search.walkforward lit COSTS au niveau module. On le remplace le
        # temps du calcul pour ne pas dupliquer toute la logique de fenetres.
        ancien = wf_search.COSTS
        wf_search.COSTS = costs
        try:
            b = wf_search.walkforward(sym, tf, fam, rates, specs, n_fen, cfg)
        finally:
            wf_search.COSTS = ancien
        return b
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--windows", type=int, default=6)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--jobs", type=int, default=6)
    args = p.parse_args()

    familles = list(st.REGISTRY)
    taches = []
    for sym in CRYPTOS:
        for tf in TIMEFRAMES:
            try:
                md.load_history(sym, tf)
            except Exception:
                continue
            for fam in familles:
                taches.append((sym, tf, fam, args.windows, args.risk))

    workers = max(1, min(args.jobs, len(taches)))
    print("=" * 96)
    print(f"  CRYPTO AU PROTOCOLE 9 CRITERES : {len(taches)} combinaisons")
    print("  couts en POURCENTAGE du notionnel, pas par lot")
    print(f"  {workers} processus")
    print("=" * 96, flush=True)

    resultats, survivants = [], []
    fait, t0 = 0, time.time()
    with Pool(processes=workers) as pool:
        for b in pool.imap_unordered(_tache, taches, chunksize=1):
            fait += 1
            if b is not None:
                resultats.append(b)
                if wf_search.retenu(b):
                    survivants.append(b)
                    print("  RETENU  %-9s %-3s %-17s fen+ %3.0f%%  PF %.2f  "
                          "long %.2f short %.2f  sansTop5 %.2f  DD %.1f%%  n=%d"
                          % (b["symbol"], b["timeframe"], b["famille"],
                             b["pct_positives"], b["pf_out"], b["pf_long"],
                             b["pf_short"], b["pf_sans_top5"], b["dd_out_pct"],
                             b["trades_out"]), flush=True)
            if fait % 20 == 0 or fait == len(taches):
                reste = (time.time() - t0) / fait * (len(taches) - fait)
                print("  ... %d/%d (%.0f min restantes, %d evaluables, %d retenus)"
                      % (fait, len(taches), reste / 60, len(resultats),
                         len(survivants)), flush=True)

    SORTIE.write_text(json.dumps(
        {"survivants": survivants, "tous": resultats}, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print(f"  {len(resultats)} evaluables, {len(survivants)} retenues")
    print("=" * 96)
    if not survivants:
        print("  AUCUNE. La crypto ne fait pas mieux que le forex sous le meme filtre.")
        print("  La voie prop firm crypto est donc fermee elle aussi.")
    else:
        print("  %-9s %-3s %-17s %6s %6s %6s %6s %8s %6s %5s"
              % ("marche", "tf", "famille", "fen+", "PF", "long", "short",
                 "sansTop5", "DD%", "n"))
        for b in sorted(survivants, key=lambda x: -x["pf_out"]):
            print("  %-9s %-3s %-17s %5.0f%% %6.2f %6.2f %6.2f %8.2f %5.1f%% %5d"
                  % (b["symbol"], b["timeframe"], b["famille"], b["pct_positives"],
                     b["pf_out"], b["pf_long"], b["pf_short"], b["pf_sans_top5"],
                     b["dd_out_pct"], b["trades_out"]))
    print(f"\n  Detail : {SORTIE}")


if __name__ == "__main__":
    main()
