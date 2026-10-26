# aitb/ledger.py
"""
Comptabilite reelle du projet : tout ce qui sort, tout ce qui rentre.

Pourquoi ce fichier existe
--------------------------
Xelos Trading, 2026-09-12 : « Le vrai danger, ce n'est pas de perdre un compte.
C'est de ne jamais savoir combien ca te coute reellement d'essayer de gagner. »

Jusqu'ici j'ai donne a Paul des esperances de revenu (134 EUR/semaine) sans
jamais tenir le compte de ce que le projet COUTE. Or un trader peut tres bien
encaisser un payout et etre negatif sur l'annee : frais de challenge, resets,
comptes qui sautent, cycles rates. Ces elements ne sont pas des accidents, ils
font partie du fonctionnement normal.

Ce module tient le registre. Il ne calcule pas une esperance, il enregistre des
faits : chaque euro sorti, chaque euro rentre, avec sa date et son motif.

Il suit aussi le CRITERE DES 4 MOIS. Casper, meme date : « pour reussir en prop
firm il faut deja etre rentable en compte propre, avec un track record de 4 mois
sur lequel vous gagnez de l'argent ». Tant que ce compteur n'est pas plein et
positif, aucun challenge payant ne doit etre achete.

Usage:
    python -m aitb.ledger                          affiche le bilan
    python -m aitb.ledger --depense 540 "challenge FTMO 80k"
    python -m aitb.ledger --recette 1200 "payout FTMO cycle 1"
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = PROJECT_ROOT / "data" / "ledger.json"

# Critere de Casper : 4 mois de track record rentable avant tout challenge paye.
TRACK_RECORD_JOURS = 120


def _lire() -> dict:
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"debut": None, "lignes": []}


def _ecrire(d: dict):
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def demarrer(quand: str = None) -> dict:
    """Fixe le jour 1 du track record. Sans effet s'il existe deja."""
    d = _lire()
    if not d.get("debut"):
        d["debut"] = quand or datetime.now().strftime("%Y-%m-%d")
        _ecrire(d)
    return d


def ajouter(montant: float, motif: str, categorie: str = "autre") -> dict:
    """
    Montant NEGATIF pour une depense, POSITIF pour une recette.

    Categories utiles : challenge, reset, payout, abonnement, autre.
    """
    d = _lire()
    d.setdefault("lignes", []).append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "montant": round(float(montant), 2),
        "motif": motif,
        "categorie": categorie,
    })
    _ecrire(d)
    return d


def bilan() -> dict:
    """Etat complet, sans interpretation."""
    d = _lire()
    lignes = d.get("lignes", [])
    depenses = sum(-l["montant"] for l in lignes if l["montant"] < 0)
    recettes = sum(l["montant"] for l in lignes if l["montant"] > 0)

    debut = d.get("debut")
    jours = 0
    if debut:
        try:
            jours = (datetime.now() - datetime.strptime(debut, "%Y-%m-%d")).days
        except Exception:
            jours = 0

    par_cat = {}
    for l in lignes:
        par_cat[l["categorie"]] = par_cat.get(l["categorie"], 0.0) + l["montant"]

    return {
        "debut": debut,
        "jours": jours,
        "jours_restants": max(TRACK_RECORD_JOURS - jours, 0),
        "track_record_pct": min(jours / TRACK_RECORD_JOURS * 100, 100) if debut else 0.0,
        "depenses": round(depenses, 2),
        "recettes": round(recettes, 2),
        # Le seul chiffre qui compte : ce qui reste apres tout.
        "net": round(recettes - depenses, 2),
        "par_categorie": {k: round(v, 2) for k, v in par_cat.items()},
        "nb_lignes": len(lignes),
        # Feu vert pour acheter un challenge : 4 mois ecoules ET projet non deficitaire.
        "peut_payer_challenge": bool(debut and jours >= TRACK_RECORD_JOURS),
    }


def afficher():
    b = bilan()
    print()
    print("  " + "=" * 56)
    print("   COMPTABILITE REELLE DU PROJET")
    print("  " + "=" * 56)
    if not b["debut"]:
        print("   Track record pas encore demarre.")
        print("   Lance : python -m aitb.ledger --demarrer")
    else:
        print(f"   depuis le      {b['debut']}  ({b['jours']} jours)")
        plein = int(b["track_record_pct"] / 5)
        barre = "#" * plein + "." * (20 - plein)
        print(f"   track record   [{barre}] {b['track_record_pct']:.0f}%  "
              f"({b['jours_restants']} jours restants sur {TRACK_RECORD_JOURS})")
    print()
    print(f"   sorties        {b['depenses']:>10,.2f} EUR")
    print(f"   entrees        {b['recettes']:>10,.2f} EUR")
    print(f"   NET            {b['net']:>10,.2f} EUR")
    if b["par_categorie"]:
        print()
        for k, v in sorted(b["par_categorie"].items(), key=lambda x: x[1]):
            print(f"     {k:<14} {v:>10,.2f} EUR")
    print()
    if b["peut_payer_challenge"]:
        print("   Les 4 mois sont ecoules. Verifier que le track record est POSITIF")
        print("   avant d'acheter un challenge.")
    else:
        print("   Challenge paye : NON. Le critere des 4 mois n'est pas rempli.")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--demarrer" in args:
        d = demarrer()
        print(f"  Track record demarre le {d['debut']}")
    elif "--depense" in args:
        i = args.index("--depense")
        ajouter(-abs(float(args[i + 1])), args[i + 2] if len(args) > i + 2 else "sans motif",
                "challenge" if "challenge" in " ".join(args).lower() else "autre")
    elif "--recette" in args:
        i = args.index("--recette")
        ajouter(abs(float(args[i + 1])), args[i + 2] if len(args) > i + 2 else "sans motif",
                "payout")
    afficher()
