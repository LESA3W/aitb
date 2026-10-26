# research/funding_clean.py
"""
Reprise propre du test du financement. Deux defauts corriges.

Defaut 1 : intervalle de financement suppose a 8h
--------------------------------------------------
Toutes les paires ne paient pas toutes les 8 heures. LSKUSDT, ENAUSDT et
RAYSOLUSDT avaient 2 a 2,4 fois plus de points que BTCUSDT sur la meme periode :
elles paient toutes les 4h ou toutes les heures. En les alignant de force sur des
bougies 8h, plusieurs financements tombaient sur la meme bougie. Ici l'intervalle
reel est DEDUIT des horodatages, et les prix viennent de bougies 1h indexees par
temps, donc l'alignement est exact quelle que soit la frequence.

Defaut 2, le plus grave : fenetres qui se CHEVAUCHENT
-----------------------------------------------------
Mesurer un rendement a 72h tous les 8h, c'est produire 9 observations qui
partagent presque toutes leurs donnees. Leurs erreurs sont correlees, la taille
d'echantillon effective est proche de n/9, et les t-stats sont gonflees d'un
facteur proche de racine(9) = 3.

Autrement dit le t de 6,68 sur XRPUSDT vaut en realite plutot 2,2, et la moyenne
de 1,95 tombe vers 0,65. Ce defaut affecte TOUS les marches, pas seulement ceux
a frequence rapide, et il suffit a expliquer l'essentiel du resultat precedent.

Correction : on n'utilise que des fenetres DISJOINTES. Pour un horizon de 72h et
un financement toutes les 8h, on ne garde qu'une observation sur neuf. On perd
beaucoup de points, et c'est le prix d'un test honnete.

Usage:
    python research/funding_clean.py --top 30 --months 24
"""

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                              # noqa: E402

from research.funding_signal import _get, FAPI, funding_history, perp_symbols  # noqa: E402

HORIZONS_H = (8, 24, 72)
MIN_OBS = 120          # apres degroupage, il faut garder de quoi conclure


def klines_1h(symbol, months=24):
    """Bougies 1h, la granularite la plus fine qui couvre 24 mois sans exces."""
    fin = int(time.time() * 1000)
    debut = fin - int(months * 30.44 * 86400 * 1000)
    out, curseur = [], debut
    while curseur < fin:
        url = (f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h"
               f"&startTime={curseur}&limit=1500")
        lot = _get(url)
        if not lot:
            break
        out += lot
        suivant = int(lot[-1][0]) + 1
        if suivant <= curseur:
            break
        curseur = suivant
        time.sleep(0.1)
    return {int(k[0]) // 3600000: float(k[4]) for k in out}     # heure -> cloture


def intervalle_reel(f):
    """Intervalle de financement median, en heures, deduit des horodatages."""
    if len(f) < 10:
        return None
    d = np.diff([t for t, _ in f]) / 3600000.0
    return float(np.median(d))


def regression(y, X):
    n = len(y)
    A = np.column_stack([np.ones(n)] + list(X))
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    ddl = n - A.shape[1]
    if ddl <= 0:
        return beta, np.zeros_like(beta)
    s2 = float(resid @ resid) / ddl
    try:
        se = np.sqrt(np.clip(np.diag(s2 * np.linalg.inv(A.T @ A)), 1e-30, None))
        return beta, beta / se
    except np.linalg.LinAlgError:
        return beta, np.zeros_like(beta)


def analyser(symbol, months):
    f = funding_history(symbol, months)
    if len(f) < 300:
        return None
    pas_h = intervalle_reel(f)
    if not pas_h or pas_h <= 0:
        return None
    prix = klines_1h(symbol, months)
    if len(prix) < 3000:
        return None

    res = {"symbol": symbol, "intervalle_h": round(pas_h, 2), "n_funding": len(f)}

    for H in HORIZONS_H:
        # Une observation sur k pour que les fenetres ne se chevauchent jamais.
        k = max(1, int(round(H / pas_h)))
        y, xp, xf = [], [], []
        for i in range(0, len(f), k):
            ts, taux = f[i]
            h0 = ts // 3600000
            if h0 not in prix or (h0 + H) not in prix or (h0 - 24) not in prix:
                continue
            y.append((prix[h0 + H] / prix[h0] - 1.0) * 100.0)
            xp.append((prix[h0] / prix[h0 - 24] - 1.0) * 100.0)
            xf.append(taux * 100.0)
        if len(y) < MIN_OBS:
            continue
        y = np.array(y); xp_ = np.array(xp); xf_ = np.array(xf)
        _, t1 = regression(y, [xf_])
        _, t2 = regression(y, [xp_, xf_])
        res["h%d" % H] = {
            "n_disjoint": len(y),
            "un_sur": k,
            "t_fund_seul": float(t1[1]),
            "t_fund_avec_prix": float(t2[2]),
            "t_prix": float(t2[1]),
        }
    return res


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--months", type=int, default=24)
    args = p.parse_args()

    syms = perp_symbols(args.top)
    print("=" * 100)
    print("  TEST DU FINANCEMENT, VERSION PROPRE")
    print("  intervalle deduit des donnees + fenetres DISJOINTES (fin du chevauchement)")
    print("=" * 100, flush=True)

    tous = []
    for s in syms:
        try:
            r = analyser(s, args.months)
        except Exception as e:
            print("  %-14s echec %s" % (s, str(e)[:40]), flush=True)
            continue
        if r is None:
            continue
        tous.append(r)
        d = r.get("h72", {})
        print("  %-14s pas %4.1fh  72h: 1 obs sur %d, n=%4d  t seul %6.2f  "
              "t avec prix %6.2f"
              % (s, r["intervalle_h"], d.get("un_sur", 0), d.get("n_disjoint", 0),
                 d.get("t_fund_seul", float("nan")),
                 d.get("t_fund_avec_prix", float("nan"))), flush=True)

    (PROJECT_ROOT / "data" / "funding" / "clean.json").write_text(
        json.dumps(tous, indent=2), encoding="utf-8")

    print("\n" + "=" * 100)
    print("  AGREGAT  (|t| > 2 = significatif)")
    print("=" * 100)
    print("  %-10s %9s %14s %18s %14s %16s"
          % ("horizon", "marches", "t fund seul", "t fund + prix", "t prix",
             "marches |t|>2"))
    print("  " + "-" * 88)
    for H in HORIZONS_H:
        lot = [r["h%d" % H] for r in tous if "h%d" % H in r]
        if not lot:
            continue
        forts = sum(1 for x in lot if abs(x["t_fund_avec_prix"]) > 2)
        print("  %-10s %9d %14.2f %18.2f %14.2f %12d / %d"
              % ("%dh" % H, len(lot),
                 float(np.mean([x["t_fund_seul"] for x in lot])),
                 float(np.mean([x["t_fund_avec_prix"] for x in lot])),
                 float(np.mean([x["t_prix"] for x in lot])),
                 forts, len(lot)))

    print("\n  Attendu par pur hasard au seuil |t|>2 : environ 5% des marches.")
    print("  Il faut nettement plus que ca pour parler de signal.")


if __name__ == "__main__":
    main()
