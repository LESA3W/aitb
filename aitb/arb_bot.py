# aitb/arb_bot.py
"""
Bot d'arbitrage de financement sur OKX.

Ce que fait la strategie
------------------------
Pour un sous-jacent donne, on achete X au comptant et on vend X en perpetuel.
Les deux jambes s'annulent : si le prix double ou s'effondre, le resultat est
le meme. On n'encaisse qu'une chose, le taux de financement verse toutes les
8 heures par les longs du perpetuel.

Ce n'est pas une prediction, c'est un loyer. C'est la raison pour laquelle
cette strategie a survecu a tous les tests qui ont tue les ~5 000 combinaisons
directionnelles de ce projet.

Chiffres mesures sur OKX, 96 jours, 10 paires : 4,14 %/an brut, 3,84 % net.
Pire versement observe sur 8 heures : -0,036 %.

MODE PAPIER PAR DEFAUT
----------------------
Le bot demarre en simulation. Il lit les vrais taux, les vrais prix, les vrais
carnets, et tient une comptabilite exacte de ce qu'il aurait gagne, sans aucune
cle API et sans aucun compte ouvert. C'est le seul moyen de constituer un track
record reel a cout nul avant d'engager le moindre euro.

Passer en reel demandera CONFIG["ARB"]["LIVE"] = True plus des cles API, et ce
n'est pas encore implemente : tant qu'aucun track record n'existe, envoyer des
ordres serait prematuree.

Usage:
    python -m aitb.arb_bot --scan          classe les paires par qualite
    python -m aitb.arb_bot --faisable 500  ce qui est tradable avec 500 EUR
    python -m aitb.arb_bot --run           lance la boucle en mode papier
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                      # noqa: E402

from aitb import okx                                    # noqa: E402

ETAT = PROJECT_ROOT / "data" / "arb_state.json"
JOURNAL = PROJECT_ROOT / "data" / "arb_journal.jsonl"

# Frais aller-retour, les deux jambes comprises. OKX taker 0,05 % par jambe,
# donc 0,20 % pour ouvrir et fermer les deux cotes. On compte 0,30 % pour
# absorber le slippage. C'est volontairement pessimiste.
FRAIS_ALLER_RETOUR_PCT = 0.30

# NE PAS ajouter de filtre qui sort avant les financements negatifs.
# Mesure du 2026-09-12 : eviter les negatifs demande 224 a 437 flips par an,
# soit 67 a 131 % du capital en frais. Le filtre fait passer le rendement de
# +3,21 %/an a -93 %/an. On reste investi en permanence, point.
#
# Une paire n'est retenue que si son financement a ete positif au moins
# MIN_POS % du temps. En dessous, on paierait le financement trop souvent.
MIN_POS_PCT = 65.0
MIN_ANNUEL_PCT = 2.0


def _charger(p, defaut):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return defaut


def _sauver(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- selection

def qualite(inst_id: str):
    """Statistiques de financement d'une paire, sur tout l'historique dispo."""
    h = okx.financement_historique(inst_id)
    if len(h) < 100:
        return None
    tx = np.array([t for _, t in h])
    jours = (h[-1][0] - h[0][0]) / 86400000.0
    par_jour = len(tx) / max(jours, 1)
    return {
        "inst": inst_id,
        "points": len(tx),
        "jours": round(jours),
        "annuel_pct": float(tx.mean() * par_jour * 365 * 100),
        "median_pct": float(np.median(tx) * par_jour * 365 * 100),
        "pct_positifs": float((tx > 0).mean() * 100),
        "pire_8h_pct": float(tx.min() * 100),
    }


def retenue(q):
    return (q is not None
            and q["pct_positifs"] >= MIN_POS_PCT
            and q["annuel_pct"] >= MIN_ANNUEL_PCT
            and q["jours"] >= 60)


# ------------------------------------------------------- taille minimale

def taille_minimale(paire, prix_spot):
    """
    Notionnel minimum d'une position neutre, en USDT.

    Le piege : un perpetuel OKX se traite en CONTRATS, pas en unites. BTC vaut
    0,01 BTC par contrat, DOGE 1000 DOGE par contrat. Le minimum imposable est
    donc parfois enorme au regard d'un petit compte, exactement comme le plafond
    MAX_LOT qui ecrasait les positions du bot forex sans rien signaler.

    On renvoie le notionnel d'UN contrat : c'est le ticket d'entree incompressible.
    """
    ctval = paire.get("ctVal") or 0
    minsz = paire.get("minSz") or 1
    if ctval <= 0 or not prix_spot:
        return None
    return ctval * max(minsz, 1) * prix_spot


def faisable(capital_usdt, marge_perp=0.5):
    """
    Ce qui est reellement tradable avec ce capital.

    Une position neutre immobilise le notionnel au comptant PLUS la marge du
    short perpetuel. Avec 'marge_perp' de 0,5, un short de 100 USDT demande
    50 USDT de marge, donc 150 USDT au total pour 100 USDT d'exposition.
    """
    paires = okx.arbitrables()
    ids = [p["spot"] for p in paires]
    px = okx.prix_multiples(set(ids))
    out = []
    for p in paires:
        ps = px.get(p["spot"])
        mini = taille_minimale(p, ps)
        if not mini:
            continue
        besoin = mini * (1 + marge_perp)
        out.append({**p, "prix": ps, "notionnel_min": mini,
                    "capital_requis": besoin,
                    "tenable": besoin <= capital_usdt})
    return sorted(out, key=lambda x: x["capital_requis"])


# ---------------------------------------------------------------- boucle

def cycle_papier(etat):
    """
    Un passage : reconstruit les versements depuis le REGISTRE PUBLIE d'OKX.

    DEFAUT CORRIGE LE 2026-09-12
    -----------------------------
    La premiere version detectait un versement en observant que l'heure du
    prochain financement avait change entre deux sondages. Cette approche a deux
    defauts qui auraient fausse le track record :

      1. si le process est arrete au moment du versement (PC eteint, redemarrage,
         coupure reseau), le versement est perdu et ne sera jamais rattrape
      2. elle utilise le taux vu au sondage precedent, pas le taux reellement
         applique au moment du versement

    Un track record dont les chiffres dependent de la disponibilite du process
    ne vaut rien. On lit donc l'historique publie par OKX et on additionne tous
    les versements tombes depuis l'ouverture de la position. C'est exact, c'est
    rattrapable apres n'importe quelle coupure, et c'est verifiable.

    On ne simule pas les prix des deux jambes : la position etant neutre, leur
    somme est nulle par construction. Seuls le financement et les frais comptent.
    """
    positions = etat.get("positions", {})
    if not positions:
        return etat, []

    evenements = []
    maintenant = int(time.time() * 1000)
    for inst, pos in positions.items():
        try:
            hist = okx.financement_historique(inst, pages=6)
        except Exception:
            continue
        if not hist:
            continue

        ouvert = pos.get("ouvert_le", maintenant)
        dernier = pos.get("dernier_versement_ms", ouvert)
        nouveaux = [(t, tx) for t, tx in hist if t > dernier]
        if not nouveaux:
            continue

        for t, tx in sorted(nouveaux):
            gain = pos["notionnel"] * tx
            pos["cumul"] = pos.get("cumul", 0.0) + gain
            pos["versements"] = pos.get("versements", 0) + 1
            pos["dernier_versement_ms"] = t
            evenements.append({
                "t": t, "inst": inst, "type": "financement",
                "taux": tx, "gain": gain, "cumul": pos["cumul"],
            })

    etat["positions"] = positions
    etat["maj"] = maintenant
    return etat, evenements


def ouvrir(etat, inst, notionnel):
    pos = etat.setdefault("positions", {})
    if inst in pos:
        return etat
    frais = notionnel * FRAIS_ALLER_RETOUR_PCT / 100.0
    t0 = int(time.time() * 1000)
    pos[inst] = {"notionnel": notionnel, "ouvert_le": t0,
                 "cumul": -frais, "versements": 0,
                 "frais_payes": frais,
                 # borne basse des versements a comptabiliser : tout ce qui
                 # tombe APRES l'ouverture compte, rien avant.
                 "dernier_versement_ms": t0}
    return etat


def bilan(etat):
    """
    Separe les FRAIS D'ENTREE, payes une seule fois, du FINANCEMENT, qui est
    recurrent. Les melanger donne des annualisations absurdes : sur 3 jours, un
    frais unique de 0,30 % s'annualise a -36 %/an alors qu'il ne se reproduira
    jamais. C'est le rendement du financement seul qui dit si la strategie
    fonctionne ; le net dit ce qu'il reste en poche aujourd'hui.
    """
    pos = etat.get("positions", {})
    notionnel = sum(p["notionnel"] for p in pos.values())
    frais = sum(p.get("frais_payes", 0.0) for p in pos.values())
    cumul = sum(p.get("cumul", 0.0) for p in pos.values())
    financement = cumul + frais          # le cumul demarre a -frais
    versements = sum(p.get("versements", 0) for p in pos.values())

    debut = etat.get("debut")
    jours = (time.time() * 1000 - debut) / 86400000.0 if debut else 0.0

    ann = (financement / notionnel * 365 / jours * 100
           if notionnel and jours > 0.5 else 0.0)
    # Jours avant que le financement ait rembourse les frais d'entree.
    seuil = (frais / (financement / jours) if financement > 0 and jours > 0.5
             else float("inf"))
    return {
        "positions": len(pos), "notionnel": notionnel, "jours": jours,
        "frais_entree": frais, "financement": financement, "versements": versements,
        "cumul_usdt": cumul,
        "financement_annualise_pct": ann,
        "par_semaine": financement / jours * 7 if jours > 0.5 else 0.0,
        "jours_avant_rentabilite": seuil,
    }


# ---------------------------------------------------------------- interface

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scan", action="store_true")
    p.add_argument("--faisable", type=float, metavar="CAPITAL")
    p.add_argument("--run", action="store_true")
    p.add_argument("--top", type=int, default=20)
    args = p.parse_args()

    if args.faisable is not None:
        lot = faisable(args.faisable)
        ok = [x for x in lot if x["tenable"]]
        print("=" * 78)
        print(f"  CE QUI EST TRADABLE AVEC {args.faisable:,.0f} USDT")
        print("=" * 78)
        print("  %-8s %12s %16s %16s" %
              ("paire", "prix", "notionnel min", "capital requis"))
        print("  " + "-" * 60)
        for x in lot[:args.top]:
            marque = "" if x["tenable"] else "   hors budget"
            print("  %-8s %12.4f %14.2f $ %14.2f $%s"
                  % (x["base"], x["prix"] or 0, x["notionnel_min"],
                     x["capital_requis"], marque))
        print(f"\n  {len(ok)} paires sur {len(lot)} tenables avec ce capital.")
        if ok:
            print(f"  la moins chere : {ok[0]['base']} a "
                  f"{ok[0]['capital_requis']:.2f} $ de capital immobilise")
        return

    if args.scan:
        paires = okx.arbitrables()
        print("=" * 92)
        print(f"  QUALITE DU FINANCEMENT  ({len(paires)} paires arbitrables, "
              f"les {args.top} plus liquides testees)")
        print("=" * 92)
        print("  %-8s %7s %7s %12s %12s %11s %11s" %
              ("paire", "pts", "jours", "annuel", "median", "% positifs", "pire 8h"))
        print("  " + "-" * 78)
        gardees = []
        for pr in paires[:args.top]:
            try:
                q = qualite(pr["perp"])
            except Exception:
                continue
            if q is None:
                continue
            marque = "  RETENUE" if retenue(q) else ""
            if retenue(q):
                gardees.append(q)
            print("  %-8s %7d %7d %11.2f%% %11.2f%% %10.1f%% %10.3f%%%s"
                  % (pr["base"], q["points"], q["jours"], q["annuel_pct"],
                     q["median_pct"], q["pct_positifs"], q["pire_8h_pct"], marque),
                  flush=True)
        print(f"\n  {len(gardees)} paires retenues "
              f"(financement positif > {MIN_POS_PCT:.0f}% du temps "
              f"et > {MIN_ANNUEL_PCT:.0f}%/an)")
        if gardees:
            m = float(np.mean([g["annuel_pct"] for g in gardees]))
            print(f"  rendement moyen des retenues : {m:.2f}%/an brut, "
                  f"{m - FRAIS_ALLER_RETOUR_PCT:.2f}% net")
        return

    if args.run:
        etat = _charger(ETAT, {"debut": int(time.time() * 1000), "positions": {}})
        etat, evts = cycle_papier(etat)
        _sauver(ETAT, etat)
        if evts:
            with open(JOURNAL, "a", encoding="utf-8") as h:
                for e in evts:
                    h.write(json.dumps(e) + "\n")
        b = bilan(etat)
        print("  %d positions | %.2f $ notionnel | %.2f jours | %d versements"
              % (b["positions"], b["notionnel"], b["jours"], b["versements"]))
        print("  financement encaisse %+.4f $  ->  %.2f%%/an  ->  %.3f $/semaine"
              % (b["financement"], b["financement_annualise_pct"], b["par_semaine"]))
        print("  frais d'entree %.4f $ (ponctuels) | net en poche %+.4f $"
              % (b["frais_entree"], b["cumul_usdt"]))
        if b["jours_avant_rentabilite"] < 1e6:
            print("  les frais seront rembourses au bout de %.0f jours"
                  % b["jours_avant_rentabilite"])
        return

    p.print_help()


if __name__ == "__main__":
    main()
