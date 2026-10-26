# research/wf_null.py
"""
Test de l'hypothese nulle sur la recherche walk-forward.

Le piege que ce script ferme
----------------------------
wf_search.py a retenu 22 combinaisons sur 418. C'est encourageant, mais ca ne
veut rien dire tant qu'on ne connait pas le taux de survie du BRUIT. Si l'on
soumet 418 series aleatoires aux memes criteres et qu'il en ressort 20, alors
les 22 "survivantes" ne sont qu'un artefact de la selection, et on refera
exactement l'erreur du premier book.

Methode
-------
On fabrique des marches synthetiques en tirant au sort, AVEC REMISE, les
rendements bougie a bougie du marche reel. Cela conserve la volatilite, la
distribution des rendements et les queues epaisses, mais detruit
l'autocorrelation, donc la tendance et tout motif exploitable.

Un marche synthetique n'a par construction AUCUN edge. Tout ce qui "passe" le
walk-forward dessus est du faux positif.

Lecture
-------
  survie bruit << survie reelle   -> il y a bien quelque chose a exploiter
  survie bruit ~= survie reelle   -> la recherche ne fait que trier du hasard

Usage:
    python research/wf_null.py --jobs 12
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
from aitb.backtest import BacktestConfig                    # noqa: E402
from research.wf_search import (                            # noqa: E402
    instruments, walkforward, retenu, SORTIE,
)

GRAINE = 20260911


def synthetique(rates, graine):
    """
    Marche synthetique conservant la volatilite mais sans autocorrelation.

    On travaille en log-rendements de cloture, qu'on retire au sort avec remise.
    Les OHLC sont ensuite reconstruits en gardant les ecarts relatifs haut/bas
    de la bougie d'origine, pour que les stops se comportent normalement.
    """
    rng = np.random.default_rng(graine)
    n = len(rates)
    close = rates["close"].astype(float)
    rend = np.diff(np.log(np.maximum(close, 1e-12)))
    if len(rend) < 100:
        return None

    tires = rng.choice(rend, size=n - 1, replace=True)
    nouveau_close = np.empty(n, dtype=float)
    nouveau_close[0] = close[0]
    nouveau_close[1:] = close[0] * np.exp(np.cumsum(tires))

    # Amplitudes relatives de la bougie d'origine, melangees elles aussi.
    with np.errstate(divide="ignore", invalid="ignore"):
        h_rel = np.where(close > 0, rates["high"].astype(float) / close - 1.0, 0.0)
        l_rel = np.where(close > 0, rates["low"].astype(float) / close - 1.0, 0.0)
        o_rel = np.where(close > 0, rates["open"].astype(float) / close - 1.0, 0.0)
    perm = rng.permutation(n)

    out = rates.copy()
    out["close"] = nouveau_close
    out["open"] = nouveau_close * (1.0 + o_rel[perm])
    out["high"] = np.maximum.reduce([
        nouveau_close * (1.0 + np.abs(h_rel[perm])), out["open"], out["close"]])
    out["low"] = np.minimum.reduce([
        nouveau_close * (1.0 - np.abs(l_rel[perm])), out["open"], out["close"]])
    return out


# maxsize borne : sans ca chaque worker finit par garder en memoire les 108
# historiques synthetiques de 6 ans, soit plusieurs Go au total. Le test a
# ete tue par l'OS le 2026-09-12 pour cette raison.
@lru_cache(maxsize=2)
def _charger_faux(sym, tf, graine):
    rates, meta = md.load_history(sym, tf)
    faux = synthetique(rates, graine)
    specs = {"symbol": sym,
             **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                     "volume_min", "volume_max", "volume_step")}}
    return faux, specs


def _tache(args):
    sym, tf, fam, n_fen, risque, graine = args
    try:
        rates, specs = _charger_faux(sym, tf, graine)
        if rates is None:
            return None
        cfg = BacktestConfig(initial_balance=100000.0, risk_pct=risque, tp_r=2.0)
        return walkforward(sym, tf, fam, rates, specs, n_fen, cfg)
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--windows", type=int, default=5)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--jobs", type=int, default=0)
    p.add_argument("--repeats", type=int, default=1,
                   help="nombre de tirages synthetiques par marche")
    args = p.parse_args()

    paires = instruments()
    familles = list(st.REGISTRY)
    taches = [(sym, tf, fam, args.windows, args.risk, GRAINE + k)
              for k in range(args.repeats)
              for sym, tf in paires for fam in familles]
    workers = max(1, min(args.jobs or (cpu_count() - 4), len(taches)))

    print("=" * 92)
    print(f"  HYPOTHESE NULLE : {len(taches)} combinaisons sur marches SYNTHETIQUES")
    print("  memes marches, meme volatilite, autocorrelation detruite -> aucun edge possible")
    print("=" * 92, flush=True)

    evaluables = 0
    faux_positifs = []
    fait = 0
    t0 = time.time()
    with Pool(processes=workers) as pool:
        for b in pool.imap_unordered(_tache, taches, chunksize=1):
            fait += 1
            if b is not None:
                evaluables += 1
                if retenu(b):
                    faux_positifs.append(b)
            if fait % 60 == 0 or fait == len(taches):
                reste = (time.time() - t0) / fait * (len(taches) - fait)
                print("  ... %d/%d (%.0f min restantes, %d faux positifs)"
                      % (fait, len(taches), reste / 60, len(faux_positifs)), flush=True)

    taux_bruit = len(faux_positifs) / evaluables * 100 if evaluables else 0.0

    reel = json.loads(SORTIE.read_text(encoding="utf-8"))
    n_reel_ev = len(reel["tous"])
    n_reel_ok = len(reel["survivants"])
    taux_reel = n_reel_ok / n_reel_ev * 100 if n_reel_ev else 0.0

    print("\n" + "=" * 92)
    print("  RESULTAT")
    print("=" * 92)
    print("  %-34s %10s %10s %10s" % ("", "evaluables", "retenues", "taux"))
    print("  %-34s %10d %10d %9.1f%%" % ("marches REELS", n_reel_ev, n_reel_ok, taux_reel))
    print("  %-34s %10d %10d %9.1f%%" % ("marches SYNTHETIQUES (bruit)",
                                         evaluables, len(faux_positifs), taux_bruit))
    print()
    if taux_bruit <= 0:
        print("  Le bruit ne produit AUCUN faux positif. Les survivantes reelles")
        print("  sont donc tres probablement porteuses d'un vrai signal.")
    else:
        ratio = taux_reel / taux_bruit
        print("  ratio reel / bruit : %.2fx" % ratio)
        if ratio < 1.3:
            print("  VERDICT : la recherche trie du hasard. Les survivantes reelles")
            print("  ne se distinguent pas de ce que produit du bruit pur.")
        elif ratio < 2.0:
            print("  VERDICT : signal faible. Une partie des survivantes est du bruit,")
            print("  il faut resserrer les criteres avant d'en tirer un book.")
        else:
            print("  VERDICT : le taux reel domine nettement le bruit. Il y a du signal.")
    print(f"\n  attendu par hasard sur {n_reel_ev} essais reels : "
          f"~{n_reel_ev * taux_bruit / 100:.0f} faux positifs sur {n_reel_ok} retenues")


if __name__ == "__main__":
    main()
