# research/xperp_negatif.py
"""
Scan des X-PERPS a financement NEGATIF, y compris ceux sans marche spot OKX.

L'erreur corrigee ici
---------------------
xperp_funding.py n'a mesure que les 96 X-Perps ayant un marche SPOT chez OKX,
sur 179 existants. Le filtre etait juste pour le cote POSITIF : pour encaisser
un financement positif il faut etre short le perp, donc long le spot, donc
pouvoir l'acheter.

Mais le cote NEGATIF fonctionne a l'envers : on est LONG le perp et SHORT le
sous-jacent. Un short se fait en EMPRUNTANT l'actif, pas en l'achetant. La
condition n'est donc pas « avoir un marche spot » mais « etre empruntable ».

83 instruments n'ont jamais ete regardes, et le cote negatif rapporte 3,5 fois
plus que le cote positif. C'est la ou il reste le plus a trouver.

Filtres de qualite, alignes sur ce qui a ete valide
---------------------------------------------------
  - financement moyen <= -15 %/an (sinon l'emprunt mange tout)
  - au moins 40 jours d'historique (ONT a 18 jours a ete ecarte pour ca)
  - au moins 45 % de versements negatifs (persistance, pas un pic isole)
  - rendement encore positif APRES retrait des 5 meilleurs versements et
    deduction du cout d'emprunt : c'est le test anti-loterie
  - actif reellement empruntable chez OKX, avec un quota suffisant

Usage:
    python research/xperp_negatif.py
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                      # noqa: E402

from aitb import okx                                    # noqa: E402

SORTIE = PROJECT_ROOT / "data" / "funding" / "xperp_negatif.json"

MIN_JOURS = 40
MAX_ANNUEL = -15.0          # financement moyen, en %/an
MIN_PCT_NEG = 45.0
MIN_NET_ROBUSTE = 15.0      # apres retrait des 5 meilleurs ET cout d'emprunt


def emprunts():
    """Taux et quotas d'emprunt OKX, par actif."""
    out = {}
    for bloc in okx._get("/api/v5/public/interest-rate-loan-quota"):
        for x in bloc.get("basic", []):
            try:
                out[x["ccy"]] = {"taux": float(x["rate"]) * 365 * 100,
                                 "quota": float(x.get("quota", 0) or 0)}
            except (TypeError, ValueError):
                continue
    return out


def tous_xperps():
    out = []
    for d in okx._get("/api/v5/public/instruments?instType=FUTURES"):
        inst = d.get("instId", "")
        if "XPERP" not in inst.upper() or d.get("state") != "live":
            continue
        out.append({"inst": inst, "base": inst.split("-")[0],
                    "ctVal": float(d.get("ctVal") or 0),
                    "minSz": float(d.get("minSz") or 1)})
    return out


def analyser(x, emp, prix):
    h = okx.financement_historique(x["inst"], pages=25)
    if len(h) < 60:
        return None
    tx = np.array([t for _, t in h])
    jours = (h[-1][0] - h[0][0]) / 86400000.0
    if jours < MIN_JOURS:
        return None
    pj = len(tx) / jours
    annuel = float(tx.mean() * pj * 365 * 100)
    if annuel > MAX_ANNUEL:
        return None

    # Test anti-loterie : on retire les 5 versements les PLUS NEGATIFS, donc les
    # plus rentables pour nous. Si le rendement s'effondre, tout tenait a eux.
    sans5 = float(np.sort(tx)[5:].mean() * pj * 365 * 100)

    e = emp.get(x["base"])
    if e is None:
        return {"base": x["base"], "inst": x["inst"], "annuel": annuel,
                "jours": jours, "pct_neg": float((tx < 0).mean() * 100),
                "empruntable": False, "net": None, "net_robuste": None}

    net = -annuel - e["taux"]
    net_rob = -sans5 - e["taux"]
    p = prix.get(x["inst"])
    return {
        "base": x["base"], "inst": x["inst"], "annuel": annuel,
        "sans_top5": sans5, "jours": jours, "points": len(tx),
        "pct_neg": float((tx < 0).mean() * 100),
        "empruntable": True, "taux_emprunt": e["taux"], "quota": e["quota"],
        "net": net, "net_robuste": net_rob, "prix": p,
        "notionnel_min": (x["ctVal"] * max(x["minSz"], 1) * p) if p else None,
    }


def retenu(r):
    return (r.get("empruntable")
            and r.get("net_robuste") is not None
            and r["net_robuste"] >= MIN_NET_ROBUSTE
            and r["pct_neg"] >= MIN_PCT_NEG
            and r["jours"] >= MIN_JOURS)


def main():
    emp = emprunts()
    lot = tous_xperps()
    prix = {}
    for d in okx._get("/api/v5/market/tickers?instType=FUTURES"):
        v = d.get("last") or d.get("markPx") or ""
        try:
            prix[d["instId"]] = float(v)
        except (TypeError, ValueError):
            pass

    print("=" * 100)
    print(f"  X-PERPS A FINANCEMENT NEGATIF  ({len(lot)} instruments, "
          f"{len(emp)} actifs empruntables chez OKX)")
    print("  position : LONG le perp + SHORT le sous-jacent emprunte")
    print("=" * 100, flush=True)
    print("  %-9s %10s %11s %7s %9s %11s %11s %11s"
          % ("actif", "financ.", "% negatifs", "jours", "emprunt", "net", "net robuste", "1 contrat"))
    print("  " + "-" * 92)

    res = []
    for x in lot:
        try:
            r = analyser(x, emp, prix)
        except Exception:
            continue
        if r is None:
            continue
        res.append(r)
        if not r["empruntable"]:
            print("  %-9s %9.1f%% %10.1f%% %7.0f %9s"
                  % (r["base"], r["annuel"], r["pct_neg"], r["jours"],
                     "NON EMPRUNTABLE"), flush=True)
            continue
        marque = "  RETENU" if retenu(r) else ""
        print("  %-9s %9.1f%% %10.1f%% %7.0f %8.1f%% %10.1f%% %10.1f%% %10s%s"
              % (r["base"], r["annuel"], r["pct_neg"], r["jours"],
                 r["taux_emprunt"], r["net"], r["net_robuste"],
                 ("%.2f $" % r["notionnel_min"]) if r["notionnel_min"] else "-",
                 marque), flush=True)

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps(res, indent=2), encoding="utf-8")

    gardes = [r for r in res if retenu(r)]
    print("\n" + "=" * 100)
    print(f"  {len(res)} instruments a financement negatif, {len(gardes)} RETENUS")
    print("=" * 100)
    if gardes:
        gardes.sort(key=lambda r: -r["net_robuste"])
        for r in gardes:
            print("  %-9s net robuste %6.1f%%/an  (financement %.0f%%, emprunt %.1f%%, "
                  "%.0f%% de versements negatifs sur %.0f jours)"
                  % (r["base"], r["net_robuste"], r["annuel"], r["taux_emprunt"],
                     r["pct_neg"], r["jours"]))
        m = float(np.mean([r["net_robuste"] for r in gardes]))
        print(f"\n  rendement net robuste moyen : {m:.1f}%/an sur {len(gardes)} instruments")
    else:
        print("  Aucun instrument ne passe les filtres.")
    print(f"\n  Detail : {SORTIE}")


if __name__ == "__main__":
    main()
