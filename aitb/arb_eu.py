# aitb/arb_eu.py
"""
Bot d'arbitrage de financement sur les X-PERPS EUROPEENS d'OKX.

Pourquoi ce module remplace arb_bot.py
--------------------------------------
arb_bot.py visait les perpetuels GLOBAUX d'OKX (BTC-USDT-SWAP). Ces produits ne
sont pas accessibles a un resident francais : depuis la fin de la periode de
transition MiCA, le 1er juillet 2026, les Europeens passent par OKX Europe
Markets Limited, sous licence MiFID II maltaise, qui propose des X-Perps.

Un X-Perp est un future a echeance 2031 concu pour se comporter comme un
perpetuel. Verifie avant d'ecrire ce module : il verse bien un financement
toutes les 8 heures (formulaType "withRate").

Trois differences majeures avec les perps globaux, toutes en notre faveur :
  - contrat BTC a 0,0001 BTC, soit ~7,70 USD, contre 773 USD sur le global.
    Cent fois plus petit, donc dimensionne pour un compte de 500 EUR.
  - financement nettement plus genereux : 17,2 %/an de moyenne sur les dix
    meilleurs, contre 4,14 % sur les perps globaux. Ces marches sont recents et
    personne ne les a encore arbitres.
  - 77 instruments arbitrables tiennent dans 500 EUR.

Alternative ecartee : Crypto.com propose un bot d'arbitrage integre et gratuit,
mais a 2,6 a 8,8 %/an, et surtout sans licence MiFID II documentee, donc ses
derives ne sont probablement pas accessibles depuis la France.

La position
-----------
    achat de X au comptant   +   vente du X-Perp equivalent
    -> exposition au prix nulle, les deux jambes s'annulent
    -> on encaisse le financement toutes les 8 heures

Ce n'est pas une prediction, c'est un loyer.

MODE PAPIER PAR DEFAUT. Le passage en reel exige des cles API et un choix
explicite, il n'est volontairement pas automatique.

Usage:
    python -m aitb.arb_eu --scan             classe les X-Perps arbitrables
    python -m aitb.arb_eu --plan 500         le portefeuille pour 500 EUR
    python -m aitb.arb_eu --ouvrir 500       ouvre les positions en papier
    python -m aitb.arb_eu --run              un cycle : encaisse et journalise
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                      # noqa: E402

from aitb import okx                                    # noqa: E402

ETAT = PROJECT_ROOT / "data" / "arb_eu_state.json"
JOURNAL = PROJECT_ROOT / "data" / "arb_eu_journal.jsonl"

# Frais : OKX taker 0,05 % par jambe, deux jambes a l'ouverture et deux a la
# fermeture. On compte 0,30 % pour absorber le slippage. Pessimiste volontaire.
FRAIS_ALLER_RETOUR_PCT = 0.30

# NE PAS ajouter de filtre qui sort avant les financements negatifs. Mesure du
# 2026-09-12 : eviter les negatifs demande 224 a 437 allers-retours par an, soit
# 67 a 131 % du capital en frais. Le rendement passe de +3,21 %/an a -93 %/an.
# On reste investi en permanence.
MIN_POS_PCT = 75.0          # part de versements positifs exigee
MIN_ANNUEL_PCT = 5.0        # rendement annualise minimum
MIN_JOURS = 20              # historique minimum, les X-Perps sont recents
PIRE_8H_MAX = -0.25         # on ecarte les instruments a versements extremes


def _charger(p, defaut):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return defaut


def _sauver(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- univers

def xperps_arbitrables():
    """
    X-Perps vivants dont le sous-jacent existe AUSSI au comptant.

    Le filtre spot est eliminatoire : sans jambe comptant, la position n'est pas
    neutre, c'est un pari directionnel deguise. Cela ecarte notamment tous les
    X-Perps sur actions (SPY, GOOGL, SOFTBANK, SOXL...), qu'OKX ne propose pas
    au comptant.
    """
    spots = {}
    for d in okx._get("/api/v5/public/instruments?instType=SPOT"):
        if d.get("state") == "live":
            spots.setdefault(d.get("baseCcy"), []).append(d["instId"])

    out = []
    for d in okx._get("/api/v5/public/instruments?instType=FUTURES"):
        inst = d.get("instId", "")
        if "XPERP" not in inst.upper() or d.get("state") != "live":
            continue
        base = inst.split("-")[0]
        if base not in spots:
            continue
        out.append({
            "inst": inst, "base": base, "spot": spots[base][0],
            "ctVal": float(d.get("ctVal") or 0),
            "minSz": float(d.get("minSz") or 1),
        })
    return out


def qualite(inst, pages=30):
    h = okx.financement_historique(inst, pages=pages)
    if len(h) < 30:
        return None
    tx = np.array([t for _, t in h])
    jours = (h[-1][0] - h[0][0]) / 86400000.0
    if jours < 1:
        return None
    par_jour = len(tx) / jours
    return {
        "points": len(tx), "jours": jours,
        "annuel_pct": float(tx.mean() * par_jour * 365 * 100),
        "pct_positifs": float((tx > 0).mean() * 100),
        "pire_8h_pct": float(tx.min() * 100),
        "par_jour": par_jour,
    }


def retenu(q):
    return (q is not None
            and q["jours"] >= MIN_JOURS
            and q["pct_positifs"] >= MIN_POS_PCT
            and q["annuel_pct"] >= MIN_ANNUEL_PCT
            and q["pire_8h_pct"] >= PIRE_8H_MAX)


def scanner(limite=None):
    """Classe les X-Perps arbitrables par qualite de financement."""
    lot = xperps_arbitrables()
    if limite:
        lot = lot[:limite]
    prix = {}
    for typ in ("SPOT", "FUTURES"):
        for d in okx._get(f"/api/v5/market/tickers?instType={typ}"):
            v = d.get("last") or d.get("markPx") or ""
            try:
                prix[d["instId"]] = float(v)
            except (TypeError, ValueError):
                pass

    res = []
    for x in lot:
        p = prix.get(x["inst"])
        if not p:
            continue
        q = qualite(x["inst"])
        if q is None:
            continue
        notionnel = x["ctVal"] * max(x["minSz"], 1) * p
        res.append({**x, **q, "prix": p, "notionnel_min": notionnel,
                    "retenu": retenu(q)})
    res.sort(key=lambda r: -r["annuel_pct"])
    return res


# ---------------------------------------------------------------- plan

# Levier retenu sur la jambe short : 3x, soit 33 % de marge.
#
# Paliers reels d'OKX : jusqu'a 50x autorise, 2 % de marge initiale. Le choix
# de 3x n'est donc pas une contrainte technique, c'est un arbitrage :
#
#   1x  -> 0,84 $/sem, liquide si le prix monte de 100 %
#   3x  -> 1,26 $/sem, liquide si le prix monte de  33 %   <- retenu
#  10x  -> 1,52 $/sem, liquide si le prix monte de  10 %
#
# Point cle souvent mal compris : une liquidation du short n'est PAS une
# catastrophe ici. Si le short saute sur une hausse de 33 %, le spot a gagne
# 33 % en meme temps. On sort autour de l'equilibre, en perdant la position et
# les frais, pas le capital. La couverture protege meme dans ce cas.
#
# A 10x il faudrait une bougie de +10 %, ce qui arrive plusieurs fois par an sur
# un altcoin. A 3x il faut +33 % en 8 heures : rare, et surveillable.
LEVIER = 3.0
MARGE = 1.0 / LEVIER

# Au-dela de ce taux d'utilisation de la marge, on ferme la ligne avant que
# l'exchange ne la liquide. Une sortie choisie coute moins cher qu'une
# liquidation forcee, qui se fait au pire prix disponible.
SEUIL_FERMETURE = 0.70


def plan(capital_eur, marge=MARGE, max_lignes=12):
    """
    Repartit le capital sur les meilleurs X-Perps.

    `marge` : capital immobilise en plus du notionnel comptant, pour la marge du
    short. A 1.0 on double, ce qui est prudent : chez OKX Europe le comptant est
    sous l'entite MiCA et les derives sous l'entite MiFID, et rien ne garantit
    que la marge soit fongible entre les deux.
    """
    capital_usd = capital_eur * 1.09
    res = [r for r in scanner() if r["retenu"]]
    if not res:
        return [], 0.0
    res = res[:max_lignes]
    budget = capital_usd / len(res)

    lignes, total = [], 0.0
    for r in res:
        cout_unitaire = r["notionnel_min"] * (1 + marge)
        n = int(budget // cout_unitaire)
        if n < 1:
            continue
        notionnel = n * r["notionnel_min"]
        lignes.append({**r, "contrats": n, "notionnel": notionnel,
                       "capital": notionnel * (1 + marge)})
        total += notionnel * (1 + marge)
    return lignes, total


# ---------------------------------------------------------------- papier

def ouvrir(etat, ligne):
    pos = etat.setdefault("positions", {})
    if ligne["inst"] in pos:
        return etat
    frais = ligne["notionnel"] * FRAIS_ALLER_RETOUR_PCT / 100.0
    t0 = int(time.time() * 1000)
    pos[ligne["inst"]] = {
        "base": ligne["base"], "spot": ligne["spot"],
        "contrats": ligne["contrats"], "notionnel": ligne["notionnel"],
        "ouvert_le": t0, "dernier_versement_ms": t0,
        "cumul": -frais, "frais_payes": frais, "versements": 0,
        "annuel_attendu": ligne["annuel_pct"],
        "prix_entree": ligne.get("prix"),
        "levier": LEVIER,
    }
    return etat


def cycle(etat):
    """
    Reconstruit les versements depuis le REGISTRE PUBLIE par OKX.

    On ne se fie pas a un sondage : si le PC est eteint au moment du versement,
    un sondage le perdrait a jamais et le track record serait troue. En lisant
    l'historique publie, tout se rattrape apres n'importe quelle coupure.
    """
    positions = etat.get("positions", {})
    evenements = []
    for inst, pos in positions.items():
        try:
            hist = okx.financement_historique(inst, pages=4)
        except Exception:
            continue
        dernier = pos.get("dernier_versement_ms", pos.get("ouvert_le", 0))
        for t, tx in sorted((t, tx) for t, tx in hist if t > dernier):
            gain = pos["notionnel"] * tx
            pos["cumul"] = pos.get("cumul", 0.0) + gain
            pos["versements"] = pos.get("versements", 0) + 1
            pos["dernier_versement_ms"] = t
            evenements.append({"t": t, "inst": inst, "taux": tx, "gain": gain,
                               "cumul": pos["cumul"]})
    etat["maj"] = int(time.time() * 1000)
    return etat, evenements


def risque_liquidation(etat):
    """
    Distance a la liquidation de chaque short, en pourcentage de hausse du prix.

    On compare le prix courant au prix d'entree : le short perd quand le prix
    monte. A `marge` de 33 %, la liquidation arrive vers +33 %, moins la marge
    de maintenance. On alerte bien avant.
    """
    alertes = []
    positions = etat.get("positions", {})
    if not positions:
        return alertes
    try:
        px = {}
        for d in okx._get("/api/v5/market/tickers?instType=FUTURES"):
            v = d.get("last") or d.get("markPx") or ""
            try:
                px[d["instId"]] = float(v)
            except (TypeError, ValueError):
                pass
    except Exception:
        return alertes

    for inst, pos in positions.items():
        p0 = pos.get("prix_entree")
        p1 = px.get(inst)
        if not p0 or not p1:
            continue
        hausse = (p1 / p0 - 1.0)
        conso = hausse / MARGE if MARGE > 0 else 0.0
        if conso >= SEUIL_FERMETURE:
            alertes.append({"inst": inst, "hausse_pct": hausse * 100,
                            "conso": conso,
                            "action": "FERMER" if conso >= 1.0 else "ALERTE"})
    return alertes


def bilan(etat):
    """
    Separe les FRAIS D'ENTREE, ponctuels, du FINANCEMENT, recurrent. Les
    melanger donne des annualisations absurdes : sur trois jours, un frais unique
    de 0,30 % s'annualise a -36 %/an alors qu'il ne se reproduira jamais.
    """
    pos = etat.get("positions", {})
    notionnel = sum(p["notionnel"] for p in pos.values())
    frais = sum(p.get("frais_payes", 0.0) for p in pos.values())
    cumul = sum(p.get("cumul", 0.0) for p in pos.values())
    financement = cumul + frais
    jours = ((time.time() * 1000 - etat.get("debut", 0)) / 86400000.0
             if etat.get("debut") else 0.0)
    return {
        "positions": len(pos), "notionnel": notionnel, "frais": frais,
        "financement": financement, "cumul": cumul, "jours": jours,
        "versements": sum(p.get("versements", 0) for p in pos.values()),
        "annuel_pct": (financement / notionnel * 365 / jours * 100
                       if notionnel and jours > 0.5 else 0.0),
        "par_semaine": financement / jours * 7 if jours > 0.5 else 0.0,
    }


# ---------------------------------------------------------------- cli

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scan", action="store_true")
    p.add_argument("--plan", type=float, metavar="EUR")
    p.add_argument("--ouvrir", type=float, metavar="EUR")
    p.add_argument("--run", action="store_true")
    p.add_argument("--top", type=int, default=20)
    a = p.parse_args()

    if a.scan:
        res = scanner()
        gardes = [r for r in res if r["retenu"]]
        print("=" * 92)
        print(f"  X-PERPS ARBITRABLES  ({len(res)} mesures, {len(gardes)} retenus)")
        print(f"  criteres : >= {MIN_POS_PCT:.0f}% de versements positifs, "
              f">= {MIN_ANNUEL_PCT:.0f}%/an, >= {MIN_JOURS} jours, "
              f"pire 8h >= {PIRE_8H_MAX}%")
        print("=" * 92)
        print("  %-10s %9s %7s %11s %11s %12s" %
              ("actif", "annuel", "jours", "% positifs", "pire 8h", "1 contrat"))
        print("  " + "-" * 70)
        for r in gardes[:a.top]:
            print("  %-10s %8.2f%% %7.0f %10.1f%% %10.3f%% %10.2f $"
                  % (r["base"], r["annuel_pct"], r["jours"], r["pct_positifs"],
                     r["pire_8h_pct"], r["notionnel_min"]))
        if gardes:
            m = float(np.mean([r["annuel_pct"] for r in gardes[:10]]))
            print(f"\n  moyenne des 10 meilleurs : {m:.2f}%/an brut, "
                  f"{m - FRAIS_ALLER_RETOUR_PCT:.2f}% net la premiere annee")
        return

    if a.plan is not None:
        lignes, total = plan(a.plan)
        print("=" * 84)
        print(f"  PORTEFEUILLE POUR {a.plan:.0f} EUR")
        print("=" * 84)
        print("  %-10s %9s %12s %13s %11s" %
              ("actif", "contrats", "notionnel", "capital", "annuel"))
        print("  " + "-" * 62)
        for l in lignes:
            print("  %-10s %9d %10.2f $ %11.2f $ %9.2f%%"
                  % (l["base"], l["contrats"], l["notionnel"],
                     l["capital"], l["annuel_pct"]))
        if lignes:
            no = sum(l["notionnel"] for l in lignes)
            pond = sum(l["notionnel"] * l["annuel_pct"] for l in lignes) / no
            print(f"\n  capital immobilise {total:.2f} $ sur {a.plan * 1.09:.0f} $")
            print(f"  notionnel total    {no:.2f} $")
            print(f"  rendement pondere  {pond:.2f}%/an")
            print(f"  soit               {no * pond / 100:.2f} $/an  =  "
                  f"{no * pond / 100 / 52:.2f} $/semaine")
        return

    if a.ouvrir is not None:
        lignes, _ = plan(a.ouvrir)
        etat = {"debut": int(time.time() * 1000), "positions": {},
                "capital_eur": a.ouvrir, "mode": "papier"}
        for l in lignes:
            etat = ouvrir(etat, l)
        etat, _ = cycle(etat)
        _sauver(ETAT, etat)
        b = bilan(etat)
        print("  %d positions ouvertes en PAPIER | notionnel %.2f $ | frais %.4f $"
              % (b["positions"], b["notionnel"], b["frais"]))
        print("  etat : %s" % ETAT)
        return

    if a.run:
        etat = _charger(ETAT, None)
        if etat is None:
            print("  Aucun etat. Lance d'abord --ouvrir 500")
            return
        etat, evts = cycle(etat)
        _sauver(ETAT, etat)
        if evts:
            with open(JOURNAL, "a", encoding="utf-8") as h:
                for e in evts:
                    h.write(json.dumps(e) + chr(10))
        b = bilan(etat)
        print("  %d pos | %.2f j | %d versements | financement %+.4f $ (%.2f%%/an)"
              % (b["positions"], b["jours"], b["versements"],
                 b["financement"], b["annuel_pct"]))
        print("  frais %.4f $ (ponctuels) | net %+.4f $ | %.3f $/semaine"
              % (b["frais"], b["cumul"], b["par_semaine"]))
        for al in risque_liquidation(etat):
            print("  %s %s : le prix a monte de %.1f%%, %.0f%% de la marge "
                  "consommee" % ("🚨" if al["action"] == "FERMER" else "⚠️",
                                 al["inst"].split("-")[0], al["hausse_pct"],
                                 al["conso"] * 100))
        return

    p.print_help()


if __name__ == "__main__":
    main()
