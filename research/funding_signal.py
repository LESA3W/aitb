# research/funding_signal.py
"""
Le taux de financement predit-il quelque chose ?

Pourquoi cette question, et pourquoi elle est differente
--------------------------------------------------------
Environ 4 500 combinaisons ont ete testees sur le forex : moyennes mobiles,
canaux de Donchian, RSI, cassures de volatilite, imbalances, retours a la
moyenne. Aucun edge. Le point commun de TOUTES ces familles : elles ne
regardent que l'historique des PRIX.

Le taux de financement d'un perpetuel n'est pas calcule sur les prix. C'est une
mesure directe du desequilibre de positionnement : quand il est tres positif,
il y a trop de longs a effet de levier, et ce sont eux qui paient. C'est de la
donnee de FLUX, pas un motif de prix. C'est la seule information vraiment
nouvelle dont on dispose, et elle est publique et gratuite chez Binance avec
plus de deux ans de profondeur, toutes les 8 heures.

Ce que ce script fait, et ne fait pas
-------------------------------------
Il ne construit AUCUNE strategie. Il mesure le phenomene brut :

    sachant que le financement est dans le decile X, quel est le rendement
    moyen des 8 / 24 / 72 heures suivantes ?

Si les deciles extremes ne montrent aucun ecart de rendement futur, l'idee est
morte en dix minutes et on ne perd pas des heures a backtester du vide. C'est
l'erreur que j'ai faite sur le forex : construire des strategies avant d'avoir
verifie qu'il y avait un phenomene a exploiter.

Attention au piege de lecture : un financement positif accompagne un marche
haussier. Si le decile haut montre un rendement futur positif, ce n'est pas un
signal, c'est juste du beta. Le script neutralise ca en comparant chaque decile
au rendement MOYEN de la periode.

Usage:
    python research/funding_signal.py --top 20 --months 24
"""

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                      # noqa: E402

FAPI = "https://fapi.binance.com"
CACHE = PROJECT_ROOT / "data" / "funding"


def _get(url, retries=3):
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aitb-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def perp_symbols(limit=20):
    """Perpetuels USDT les plus echanges. Le volume est le seul filtre : une
    paire illiquide donnerait un financement bruite et intradable."""
    data = _get(f"{FAPI}/fapi/v1/ticker/24hr")
    usdt = [d for d in data if d["symbol"].endswith("USDT")]
    usdt.sort(key=lambda d: float(d["quoteVolume"]), reverse=True)
    return [d["symbol"] for d in usdt[:limit]]


def funding_history(symbol, months=24):
    """Historique complet des financements, page par page (1000 max par appel)."""
    fin = int(time.time() * 1000)
    debut = fin - int(months * 30.44 * 86400 * 1000)
    out, curseur = [], debut
    while curseur < fin:
        url = (f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}"
               f"&startTime={curseur}&limit=1000")
        lot = _get(url)
        if not lot:
            break
        out += lot
        suivant = int(lot[-1]["fundingTime"]) + 1
        if suivant <= curseur:
            break
        curseur = suivant
        time.sleep(0.12)
    return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in out]


def klines_8h(symbol, months=24):
    """Bougies 8h alignees sur les fenetres de financement."""
    fin = int(time.time() * 1000)
    debut = fin - int(months * 30.44 * 86400 * 1000)
    out, curseur = [], debut
    while curseur < fin:
        url = (f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=8h"
               f"&startTime={curseur}&limit=1500")
        lot = _get(url)
        if not lot:
            break
        out += lot
        suivant = int(lot[-1][0]) + 1
        if suivant <= curseur:
            break
        curseur = suivant
        time.sleep(0.12)
    return [(int(k[0]), float(k[4])) for k in out]   # (temps ouverture, cloture)


def mesurer(symbol, months):
    """
    Retourne, par decile de financement, le rendement futur moyen a 8h, 24h, 72h,
    exprime en ECART au rendement moyen de la periode (donc beta retire).
    """
    f = funding_history(symbol, months)
    k = klines_8h(symbol, months)
    if len(f) < 200 or len(k) < 200:
        return None

    prix = {t: c for t, c in k}
    temps = sorted(prix)
    idx = {t: i for i, t in enumerate(temps)}
    closes = np.array([prix[t] for t in temps], dtype=float)

    # Aligne chaque financement sur la bougie 8h qui le contient.
    lignes = []
    for ts, taux in f:
        # la bougie dont l'ouverture est la plus proche en dessous
        pos = np.searchsorted(temps, ts) - 1
        if pos < 0 or pos >= len(temps):
            continue
        lignes.append((pos, taux))
    if len(lignes) < 200:
        return None

    pos_arr = np.array([p for p, _ in lignes])
    taux_arr = np.array([t for _, t in lignes], dtype=float)

    res = {"symbol": symbol, "n": len(lignes),
           "taux_median_ann": float(np.median(taux_arr) * 3 * 365 * 100)}

    for horizon, nb in (("8h", 1), ("24h", 3), ("72h", 9)):
        futur = np.full(len(pos_arr), np.nan)
        for i, p in enumerate(pos_arr):
            if p + nb < len(closes):
                futur[i] = (closes[p + nb] / closes[p] - 1.0) * 100.0
        ok = ~np.isnan(futur)
        if ok.sum() < 100:
            continue
        fu, tx = futur[ok], taux_arr[ok]
        moyenne = fu.mean()          # le beta de la periode, a retirer

        deciles = np.quantile(tx, np.linspace(0, 1, 11))
        par_decile = []
        for d in range(10):
            lo, hi = deciles[d], deciles[d + 1]
            sel = (tx >= lo) & (tx <= hi) if d == 9 else (tx >= lo) & (tx < hi)
            if sel.sum() < 10:
                par_decile.append(None)
                continue
            par_decile.append(float(fu[sel].mean() - moyenne))
        res[horizon] = {"deciles": par_decile, "moyenne_periode": float(moyenne)}
        # Correlation de RANG (Spearman), calculee a la main pour ne pas
        # dependre de scipy. Elle mesure la relation sans decoupage arbitraire
        # en deciles, et resiste aux valeurs extremes qui faussent Pearson.
        rang_t = np.argsort(np.argsort(tx))
        rang_f = np.argsort(np.argsort(fu))
        res[horizon]["spearman"] = float(np.corrcoef(rang_t, rang_f)[0, 1])
    return res


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--months", type=int, default=24)
    args = p.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    syms = perp_symbols(args.top)
    print("=" * 100)
    print(f"  LE FINANCEMENT PREDIT-IL LE PRIX ?  {len(syms)} perpetuels, {args.months} mois")
    print("  rendement futur moyen par decile de financement, ECART a la moyenne "
          "de la periode (beta retire)")
    print("=" * 100, flush=True)

    tous = []
    for s in syms:
        try:
            r = mesurer(s, args.months)
        except Exception as e:
            print(f"  {s:<14} echec: {str(e)[:50]}", flush=True)
            continue
        if r is None:
            print(f"  {s:<14} pas assez de donnees", flush=True)
            continue
        tous.append(r)
        d = r.get("24h", {}).get("deciles") or []
        bas = d[0] if d and d[0] is not None else float("nan")
        haut = d[9] if len(d) > 9 and d[9] is not None else float("nan")
        print("  %-14s n=%4d  fund median %7.1f%%/an   24h: decile bas %+.3f%%  "
              "decile haut %+.3f%%  ecart %+.3f%%  spearman %+.3f"
              % (s, r["n"], r["taux_median_ann"], bas, haut, bas - haut,
                 r.get("24h", {}).get("spearman", float("nan"))), flush=True)

    (CACHE / "funding_signal.json").write_text(
        json.dumps(tous, indent=2), encoding="utf-8")

    print("\n" + "=" * 100)
    print("  AGREGAT SUR TOUS LES MARCHES")
    print("=" * 100)
    for horizon in ("8h", "24h", "72h"):
        mat = [r[horizon]["deciles"] for r in tous if horizon in r]
        if not mat:
            continue
        arr = np.array([[np.nan if v is None else v for v in row] for row in mat])
        moy = np.nanmean(arr, axis=0)
        sp = np.nanmean([r[horizon]["spearman"] for r in tous if horizon in r])
        print(f"\n  horizon {horizon}   (decile 1 = financement le plus BAS, "
              f"decile 10 = le plus HAUT)")
        print("   " + "".join("  D%-2d " % (i + 1) for i in range(10)))
        print("   " + "".join("%+6.2f" % v for v in moy))
        print(f"   spearman moyen {sp:+.4f}")
        print(f"   ecart D1 - D10 : {moy[0] - moy[9]:+.3f}%")

    print("\n  Lecture : si l'hypothese est vraie (financement eleve = longs surcharges")
    print("  = retournement a venir), on doit voir D1 POSITIF et D10 NEGATIF,")
    print("  donc un ecart D1-D10 nettement positif et un spearman negatif.")
    print(f"\n  Detail : {CACHE / 'funding_signal.json'}")


if __name__ == "__main__":
    main()
