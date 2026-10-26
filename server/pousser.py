"""
Envoie un instantane EXPURGE du tableau de bord vers l'hebergement Vercel,
et RAPATRIE les commandes deposees par la page.

A lancer sur le serveur, a cote de dashboard.py, via ENVOI_VERCEL.bat.

POURQUOI LE SERVEUR TIRE LES COMMANDES AU LIEU DE LES RECEVOIR
--------------------------------------------------------------
La page est sur Internet, le bot est derriere une box. Ouvrir un port entrant
vers la machine qui tient le compte d'evaluation serait la pire idee du projet.
Le bouton depose donc une INTENTION dans la base, et ce script vient la
chercher a chaque cycle. Aucune connexion entrante, aucun port ouvert.

Consequence assumee : jusqu'a PERIODE secondes de delai entre le clic et
l'effet. La page affiche "en attente" pendant ce temps et ne leve l'attente
que lorsque l'instantane suivant montre le nouvel etat.

POURQUOI ON ENVOIE LES BLOCS HTML DEJA RENDUS
---------------------------------------------
dashboard.py produit deja, via fragments(), des blocs HTML prets. Les
reconstruire en React cote Vercel produirait un deuxieme tableau de bord a
maintenir, qui divergerait du vrai des la premiere modification.

CE QUI NE PART JAMAIS, ET POURQUOI
----------------------------------
1. "compte" et "serveur". Reunis, ils sont la moitie d'une tentative de
   connexion au compte. Ils ne figurent pas dans fragments(), on les retire
   quand meme explicitement.

2. LE JOURNAL. Piege non evident : le bloc "journal" contient les logs bruts du
   bot, et le bot ecrit au demarrage "Connecte au compte 00000000 @ FTMO-Demo".
   On remplace ces deux chaines dans tout le contenu avant l'envoi.

La protection repose sur une LISTE BLANCHE : on construit un dictionnaire neuf,
champ par champ. Si fragments() gagne une cle sensible demain, elle ne partira
pas toute seule.

Aucune dependance nouvelle : urllib est dans la bibliotheque standard.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashboard import collecte, fragments   # noqa: E402
from aitb import control                    # noqa: E402

URL = os.environ.get("AITB_VERCEL_URL", "").rstrip("/")
JETON = os.environ.get("AITB_PUSH_TOKEN", "")
PERIODE = int(os.environ.get("AITB_ENVOI_PERIODE", "60"))

CLES_FRAGMENTS = (
    "pf", "positions", "jours", "jdetail", "trades", "journal",
    "j_amt", "j_cls", "s_amt", "s_cls", "j_pct", "s_pct",
    "j_pct_cls", "s_pct_cls", "j_arc", "s_arc", "j_left", "s_left",
    "balance", "equity", "flottant", "flot_cls",
    "led_net", "led_net_cls", "led_out", "led_in", "led_tr", "led_bar",
    "nb", "nb_pos", "maj", "uptime", "pulse_cls", "pulse_ttl",
)


def horodate(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def _expurger(texte, compte, serveur):
    """Retire le numero de compte et le nom du serveur broker d'un bloc HTML."""
    if not isinstance(texte, str):
        return texte
    if compte:
        texte = re.sub(r"\b%s\b" % re.escape(str(compte)), "<compte>", texte)
    if serveur:
        texte = texte.replace(str(serveur), "<serveur>")
    return texte


def instantane():
    d = collecte(0)
    if "erreur" in d:
        return {"erreur": d["erreur"]}

    compte = d.get("compte")
    serveur = d.get("serveur")
    f = fragments(d)

    charge = {}
    for cle in CLES_FRAGMENTS:
        if cle in f:
            charge[cle] = _expurger(f[cle], compte, serveur)

    charge.update({
        "devise": d.get("devise", "EUR"),
        "obj_jour": d.get("obj_jour", 0),
        "obj_semaine": d.get("obj_semaine", 0),
        "libelle": d.get("libelle", ""),
        "est_courante": bool(d.get("est_courante", True)),
        "mode": d.get("mode", "?"),
        "vivant": bool(d.get("vivant")),
        "uptime_h": d.get("uptime", 0),
        "nb_positions": len(d.get("positions", [])),
    })
    return charge


def _appel(chemin, methode="GET", corps=None, timeout=20):
    req = urllib.request.Request(
        URL + chemin,
        data=(json.dumps(corps).encode("utf-8") if corps is not None else None),
        method=methode,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + JETON},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        brut = r.read().decode("utf-8", "replace")
        return r.status, (json.loads(brut) if brut.strip() else {})


def envoyer(charge):
    corps = json.dumps(charge, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        URL + "/api/push", data=corps, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + JETON},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, len(corps)


def traiter_commande():
    """
    Recupere la commande en attente et l'applique LOCALEMENT.

    On ne confirme (DELETE) qu'APRES application reussie. Confirmer avant ferait
    disparaitre une commande qui a echoue, et l'utilisateur croirait que son clic
    a fonctionne alors que rien n'a bouge.
    """
    try:
        _, rep = _appel("/api/commande", "GET")
    except Exception as e:
        horodate("commande: lecture impossible (%s)" % str(e)[:60])
        return

    action = (rep or {}).get("action")
    if not action:
        return

    horodate("commande recue : %s" % action)
    try:
        if action == "RESTART":
            # Le watchdog consomme la demande sous 30 s et relance le bot.
            # Aucune position n'est fermee : stops et objectifs sont chez le
            # broker, le bot relit l'etat du compte au demarrage.
            control.demander_redemarrage(par="vercel")
        elif action in control.ETATS:
            control.ecrire(action, par="vercel")
        else:
            horodate("commande IGNOREE, action inconnue : %s" % action)
            return
    except Exception as e:
        horodate("commande NON APPLIQUEE (%s), elle restera en attente" % str(e)[:60])
        return

    try:
        _appel("/api/commande", "DELETE")
        horodate("commande %s appliquee et confirmee" % action)
    except Exception as e:
        # L'action a eu lieu, seule la confirmation a echoue. Elle sera rejouee
        # au cycle suivant : ecrire deux fois le meme mode est sans effet.
        horodate("commande appliquee mais confirmation echouee (%s)" % str(e)[:50])


def main():
    if not URL or not JETON:
        horodate("ECHEC: AITB_VERCEL_URL ou AITB_PUSH_TOKEN absent.")
        horodate("       Ils sont poses par ENVOI_VERCEL.bat, lance ce fichier.")
        return 1

    horodate("envoi vers %s toutes les %d s" % (URL, PERIODE))
    echecs = 0
    while True:
        try:
            charge = instantane()
            if "erreur" in charge:
                # Cas normal : MT5 ferme la nuit, ou terminal en cours de
                # relance. On n'ecrase pas le dernier bon instantane par une
                # carcasse vide : l'age affiche cote Vercel dira la verite.
                horodate("pas d'envoi, %s" % charge["erreur"])
            else:
                code, taille = envoyer(charge)
                horodate("envoye (HTTP %s, %d o) | solde %s | %d position(s) | %s"
                         % (code, taille, charge.get("balance"),
                            charge.get("nb_positions", 0), charge.get("mode")))
                echecs = 0
        except urllib.error.HTTPError as e:
            echecs += 1
            detail = e.read().decode("utf-8", "replace")[:200]
            horodate("HTTP %s au %de echec: %s" % (e.code, echecs, detail))
            if e.code == 401:
                horodate("       jeton refuse. PUSH_TOKEN sur Vercel doit etre")
                horodate("       IDENTIQUE au contenu de jeton_envoi.txt.")
        except Exception as e:
            echecs += 1
            horodate("echec %d: %s" % (echecs, e))

        # Les commandes sont relevees APRES l'envoi : l'instantane qui vient de
        # partir reflete l'etat d'avant, et le suivant montrera l'effet du clic.
        traiter_commande()

        time.sleep(PERIODE)


if __name__ == "__main__":
    sys.exit(main() or 0)
