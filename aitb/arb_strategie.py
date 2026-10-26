# aitb/arb_strategie.py
"""
Strategie complete d'arbitrage de financement, bidirectionnelle et composee.

Comment on est arrive a cette structure
---------------------------------------
Quatre iterations, chacune mesuree, en partant de 1,18 $/semaine sur 500 EUR :

  1,18 -> 2,60   achat du spot A CREDIT. Emprunter coute 3,5 %/an, le
                 financement paie 18,9 %. Chaque dollar emprunte est rentable.
  2,60 -> 5,94   le COTE NEGATIF. Un financement negatif signifie que les shorts
                 paient les longs : on inverse la position et on encaisse.
                 3,5 fois plus rentable que le cote positif.
  5,94 -> 9,90   levier 5x au lieu de 3x. Justifie parce que la position est
                 delta-neutre : un mouvement de prix ne fait pas bouger l'equity
                 du compte, seulement l'exigence de marge.
  9,90 -> 16,47  REINVESTISSEMENT. Le financement tombe toutes les 8 heures,
                 soit 1 095 fois par an. Le laisser dormir coutait 66 %.

Deux pistes testees et ECARTEES, pour ne pas les reexplorer :
  - Hyperliquid seul : 11 %/an cote positif, -15 % cote negatif. Moins bon
    qu'OKX (18,9 % et 56,7 %).
  - Arbitrage entre plateformes : ecart de 37,7 %/an, soit exactement le meme
    que la structure OKX seule (37,8 %), mais avec DEUX pools de marge separes.
    Meme rendement, risque operationnel superieur.

Les deux cotes
--------------
COTE POSITIF, financement > 0, les longs paient :
    on est SHORT le X-Perp   +   LONG le spot (achete a credit)
COTE NEGATIF, financement < 0, les shorts paient :
    on est LONG le X-Perp    +   SHORT le spot (emprunte puis vendu)

Dans les deux cas l'exposition au prix est nulle. On n'encaisse que le loyer.

Le cote negatif rapporte 3 fois plus, et ce n'est pas une anomalie : il faut
EMPRUNTER un token que tout le monde veut vendre. Le pret est rare et cher, on
est paye pour fournir un service rare. C'est un prix du risque, pas un cadeau.

Limites connues, a garder en tete
---------------------------------
  - seulement 3 instruments valides du cote negatif (RE, ZIL, MON)
  - 46 a 86 jours d'historique : ces marches ont ete lances en juin 2026
  - aucun forward test : tous les chiffres sont retrospectifs
  - le rappel d'emprunt est le scenario de perte le plus probable
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                      # noqa: E402

from aitb import okx                                    # noqa: E402

CACHE = PROJECT_ROOT / "data" / "funding"

# ---------------------------------------------------------------- reglages

# Levier par jambe. A 5x, il faut un mouvement de 20 % pour consommer la marge
# d'une jambe, mais la position etant delta-neutre l'autre jambe gagne autant :
# l'equity du compte ne bouge pas, seule l'exigence de marge augmente avec le
# notionnel. A 10x il ne resterait aucun tampon pour absorber un decalage
# d'execution, et sur des actifs illiquides ce decalage arrivera.
LEVIER_SPOT = 5.0
LEVIER_PERP = 5.0

# Part du notionnel allouee au cote negatif. Il rapporte 3 fois plus mais ne
# compte que 3 instruments valides : au-dela de la moitie, la concentration
# devient le risque dominant, pas le rendement.
PART_NEGATIVE = 0.50

# Frais aller-retour, les deux jambes comprises, slippage inclus. Pessimiste.
FRAIS_PCT = 0.30

# Cout d'emprunt de l'USDT pour financer la jambe spot du cote positif.
COUT_USDT_PCT = 3.50

# --- filtres du cote POSITIF
POS_MIN_PCT_POSITIFS = 90.0
POS_MIN_ANNUEL = 12.0
POS_PIRE_8H_MAX = -0.10
POS_MAX_LIGNES = 10

# --- filtres du cote NEGATIF, plus severes : le rendement est plus eleve donc
#     la tentation de se raconter des histoires aussi
NEG_MIN_JOURS = 40
NEG_MAX_ANNUEL = -15.0
NEG_MIN_PCT_NEGATIFS = 45.0
NEG_MIN_NET_ROBUSTE = 15.0
NEG_MAX_LIGNES = 5


def _emprunts():
    out = {}
    for bloc in okx._get("/api/v5/public/interest-rate-loan-quota"):
        for x in bloc.get("basic", []):
            try:
                out[x["ccy"]] = {"taux": float(x["rate"]) * 365 * 100,
                                 "quota": float(x.get("quota", 0) or 0)}
            except (TypeError, ValueError):
                continue
    return out


def _prix_futures():
    out = {}
    for d in okx._get("/api/v5/market/tickers?instType=FUTURES"):
        v = d.get("last") or d.get("markPx") or ""
        try:
            out[d["instId"]] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def _spots():
    out = {}
    for d in okx._get("/api/v5/public/instruments?instType=SPOT"):
        if d.get("state") == "live":
            out.setdefault(d.get("baseCcy"), []).append(d["instId"])
    return out


def _xperps():
    out = []
    for d in okx._get("/api/v5/public/instruments?instType=FUTURES"):
        inst = d.get("instId", "")
        if "XPERP" not in inst.upper() or d.get("state") != "live":
            continue
        out.append({"inst": inst, "base": inst.split("-")[0],
                    "ctVal": float(d.get("ctVal") or 0),
                    "minSz": float(d.get("minSz") or 1)})
    return out


def _stats(inst, pages=25):
    h = okx.financement_historique(inst, pages=pages)
    if len(h) < 60:
        return None
    tx = np.array([t for _, t in h])
    jours = (h[-1][0] - h[0][0]) / 86400000.0
    if jours < 5:
        return None
    pj = len(tx) / jours
    # "sans_top5" retire les 5 versements les plus favorables. C'est le test
    # anti-loterie : si le rendement s'effondre, tout tenait a une poignee de
    # pics et le timing decide de tout.
    return {
        "points": len(tx), "jours": jours,
        "annuel": float(tx.mean() * pj * 365 * 100),
        "sans_top5_pos": float(np.sort(tx)[:-5].mean() * pj * 365 * 100),
        "sans_top5_neg": float(np.sort(tx)[5:].mean() * pj * 365 * 100),
        "pct_pos": float((tx > 0).mean() * 100),
        "pct_neg": float((tx < 0).mean() * 100),
        "pire_8h": float(tx.min() * 100),
    }


def construire():
    """Selectionne les deux cotes et retourne les lignes candidates."""
    emp = _emprunts()
    spots = _spots()
    prix = _prix_futures()
    lot = _xperps()

    positifs, negatifs = [], []
    for x in lot:
        p = prix.get(x["inst"])
        if not p:
            continue
        s = _stats(x["inst"])
        if s is None:
            continue
        notionnel = x["ctVal"] * max(x["minSz"], 1) * p
        base = {**x, **s, "prix": p, "notionnel_min": notionnel}

        # --- cote positif : il faut pouvoir ACHETER le spot
        if (x["base"] in spots
                and s["pct_pos"] >= POS_MIN_PCT_POSITIFS
                and s["annuel"] >= POS_MIN_ANNUEL
                and s["pire_8h"] >= POS_PIRE_8H_MAX
                and s["sans_top5_pos"] - COUT_USDT_PCT > 0):
            positifs.append({**base, "sens": "positif",
                             "spot": spots[x["base"]][0],
                             "net": s["annuel"] - COUT_USDT_PCT,
                             "net_robuste": s["sans_top5_pos"] - COUT_USDT_PCT})

        # --- cote negatif : il faut pouvoir EMPRUNTER l'actif
        e = emp.get(x["base"])
        if (e is not None
                and s["jours"] >= NEG_MIN_JOURS
                and s["annuel"] <= NEG_MAX_ANNUEL
                and s["pct_neg"] >= NEG_MIN_PCT_NEGATIFS):
            net_rob = -s["sans_top5_neg"] - e["taux"]
            if net_rob >= NEG_MIN_NET_ROBUSTE:
                negatifs.append({**base, "sens": "negatif",
                                 "taux_emprunt": e["taux"], "quota": e["quota"],
                                 "net": -s["annuel"] - e["taux"],
                                 "net_robuste": net_rob})

    positifs.sort(key=lambda r: -r["net_robuste"])
    negatifs.sort(key=lambda r: -r["net_robuste"])
    return positifs[:POS_MAX_LIGNES], negatifs[:NEG_MAX_LIGNES]


def portefeuille(capital_eur, positifs=None, negatifs=None):
    """Repartit le capital sur les deux cotes et calcule le rendement attendu."""
    if positifs is None or negatifs is None:
        positifs, negatifs = construire()
    capital_usd = capital_eur * 1.09
    notionnel_total = capital_usd / (1 / LEVIER_SPOT + 1 / LEVIER_PERP)

    lignes = []
    for lot, part in ((negatifs, PART_NEGATIVE), (positifs, 1 - PART_NEGATIVE)):
        if not lot:
            continue
        cible = notionnel_total * part / len(lot)
        for r in lot:
            n = int(cible // r["notionnel_min"])
            if n < 1:
                continue
            lignes.append({**r, "contrats": n,
                           "notionnel": n * r["notionnel_min"]})
    return lignes


def resume(lignes, capital_eur):
    no = sum(l["notionnel"] for l in lignes)
    if no <= 0:
        return None
    rdt = sum(l["notionnel"] * l["net_robuste"] for l in lignes) / no
    capital_usd = capital_eur * 1.09
    simple = capital_usd * rdt / 100
    # Le financement tombe toutes les 8 heures, soit 1 095 fois par an. Le
    # reinvestir a chaque versement change le resultat de deux tiers.
    compose = capital_usd * ((1 + rdt / 100 / 1095) ** 1095 - 1)
    return {
        "lignes": len(lignes), "notionnel": no,
        "positif": sum(1 for l in lignes if l["sens"] == "positif"),
        "negatif": sum(1 for l in lignes if l["sens"] == "negatif"),
        "rendement_pct": rdt,
        "an_simple": simple, "an_compose": compose,
        "semaine_simple": simple / 52, "semaine_compose": compose / 52,
        "frais_entree": no * FRAIS_PCT / 100,
    }


if __name__ == "__main__":
    cap = float(sys.argv[1]) if len(sys.argv) > 1 else 500.0
    print("=" * 92)
    print(f"  STRATEGIE COMPLETE  —  capital {cap:.0f} EUR")
    print(f"  levier {LEVIER_SPOT:.0f}x/{LEVIER_PERP:.0f}x, "
          f"{PART_NEGATIVE*100:.0f}% cote negatif, financement reinvesti")
    print("=" * 92, flush=True)

    pos, neg = construire()
    lignes = portefeuille(cap, pos, neg)

    print("  %-9s %-9s %9s %12s %12s %13s"
          % ("actif", "sens", "contrats", "notionnel", "net/an", "net robuste"))
    print("  " + "-" * 74)
    for l in sorted(lignes, key=lambda x: (x["sens"], -x["net_robuste"])):
        print("  %-9s %-9s %9d %10.2f $ %10.1f%% %11.1f%%"
              % (l["base"], l["sens"], l["contrats"], l["notionnel"],
                 l["net"], l["net_robuste"]))

    r = resume(lignes, cap)
    if r:
        print("\n" + "=" * 92)
        print("  %d lignes (%d positives, %d negatives) | notionnel %.2f $"
              % (r["lignes"], r["positif"], r["negatif"], r["notionnel"]))
        print("  rendement pondere ROBUSTE : %.1f%%/an" % r["rendement_pct"])
        print("  frais d'entree ponctuels  : %.2f $" % r["frais_entree"])
        print()
        print("  sans reinvestir : %8.0f $/an  =  %6.2f $/semaine"
              % (r["an_simple"], r["semaine_simple"]))
        print("  EN REINVESTISSANT : %6.0f $/an  =  %6.2f $/semaine  (+%.0f%%)"
              % (r["an_compose"], r["semaine_compose"],
                 (r["an_compose"] / r["an_simple"] - 1) * 100))
