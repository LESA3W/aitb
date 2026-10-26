#!/usr/bin/env python3
# pousser_crypto.py
"""
Pousse l'instantane du tableau de bord CRYPTO vers le miroir Vercel.

Le serveur POUSSE, Vercel ne tire jamais : la machine de la maison n'est pas
joignable depuis internet, et c'est tres bien ainsi. Aucun port ouvert, aucune
redirection, rien a exposer.

NOMS DISTINCTS, VOLONTAIREMENT
------------------------------
Les fichiers crypto cohabitent avec ceux du forex dans C:\\AITB. Tout ce qui
touche a l'envoi porte donc un suffixe _crypto, sinon on ecrase le pousser.py
du forex et on casse un systeme qui marche :

    pousser_crypto.py          ce fichier
    ENVOI_VERCEL_CRYPTO.bat    le lanceur
    jeton_envoi_crypto.txt     le jeton porteur, JAMAIS ouvert ni affiche

LISTE BLANCHE, PAS SUPPRESSION APRES COUP
-----------------------------------------
CLES_PUBLIEES enumere explicitement ce qui part. Tout fragment absent de cette
liste reste a la maison, meme s'il apparait plus tard dans le tableau de bord.

C'est l'inverse de "envoyer tout puis retirer ce qui gene" : cette seconde
approche a fait fuiter le book forex complet, parce qu'on retire ce a quoi on
pense, et on ne pense jamais a tout. Une liste blanche echoue du bon cote.

CE QUI PART, ET CE QUE PAUL A VALIDE LE 2026-09-15
---------------------------------------------------
Audit par categorie, mesure sur donnees de production, 35 fragments, 32 081
caracteres :

    identifiants, cles, chemins, noms   ABSENTS
    composition du portefeuille         PRESENTE : 22 actifs, 23 notionnels
    taux de financement encaisses       PRESENTS
    chiffres agreges                    PRESENTS

Autrement dit la strategie est reconstructible par qui a le mot de passe. Paul
a choisi le miroir COMPLET en connaissance de cause, apres avoir eu le detail
ci-dessus et l'option "agreges seulement" qui l'evitait. Ce n'est pas un oubli.

SI UN COMPTE OKX REEL EST BRANCHE UN JOUR, REFAIRE L'AUDIT AVANT LE PREMIER
ENVOI. L'absence d'identifiants aujourd'hui est une propriete ACCIDENTELLE, due
au fait qu'aucun compte n'existe, pas une protection concue.

Lancement :  ENVOI_VERCEL_CRYPTO.bat   (tache planifiee, pas fenetre console)
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import dashboard_crypto as dc   # noqa: E402

INTERVALLE = 60
JETON = ROOT / "jeton_envoi_crypto.txt"
JOURNAL = ROOT / "logs" / "pousser_crypto.log"

# Renseigne par ENVOI_VERCEL_CRYPTO.bat, ou en dur apres le premier deploiement.
URL = None

# Les 35 fragments du tableau de bord, enumeres un par un. Ajouter un fragment
# au tableau de bord ne le publie PAS : il faut l'ajouter ici, deliberement.
CLES_PUBLIEES = (
    # bandeau et etat de vie
    "maj", "uptime", "pulse_cls", "pulse_ttl", "sw_cls",
    # objectif du jour
    "j_amt", "j_cls", "j_pct", "j_pct_cls", "j_arc", "j_left",
    # objectif de la semaine
    "s_amt", "s_cls", "s_pct", "s_pct_cls", "s_arc", "s_left",
    # indicateurs
    "balance", "flottant", "flot_cls", "equity", "nb", "nb_pos",
    # comptabilite du projet
    "led_out", "led_in", "led_net", "led_net_cls", "led_tr", "led_bar",
    # panneaux
    "pf", "positions", "jours", "jdetail", "trades", "journal",
)


def _dire(msg):
    ligne = "%s  %s" % (datetime.now().strftime("%d/%m %H:%M:%S"), msg)
    print(ligne, flush=True)
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except Exception:
        pass


def jeton():
    """
    Le jeton vit dans son propre fichier et n'est JAMAIS affiche.

    pathlib ecrit en CRLF sous Windows : un \\r colle en fin de chaine produit
    un 401 parfaitement incomprehensible, puisque le jeton a l'air correct.
    On retire donc tout blanc de fin, pas seulement les espaces.
    """
    try:
        brut = JETON.read_text(encoding="utf-8", errors="replace")
    except Exception:
        _dire("jeton introuvable : %s" % JETON)
        return None
    v = brut.strip().replace("\r", "").replace("\n", "")
    return v or None


def charge():
    """
    L'instantane a envoyer, filtre par liste blanche.

    On journalise ce qui est ECARTE : c'est le seul moyen de voir une derive le
    jour ou quelqu'un ajoute un fragment au tableau de bord. Un filtre muet
    finit par laisser passer ce qu'il ne connait pas.
    """
    d = dc.collecte(0)
    f = dc.fragments(d)
    envoye = {k: f[k] for k in CLES_PUBLIEES if k in f}
    ecarte = sorted(set(f) - set(CLES_PUBLIEES))
    manquant = sorted(set(CLES_PUBLIEES) - set(f))
    if ecarte:
        _dire("fragments NON publies (%d) : %s" % (len(ecarte), ", ".join(ecarte)))
    if manquant:
        _dire("attendus mais absents (%d) : %s" % (len(manquant), ", ".join(manquant)))
    return envoye


def envoyer(url, tok, donnees):
    """
    Envoie l'instantane, et rend la commande eventuellement en attente.

    La reponse du miroir porte le CANAL DE RETOUR. C'est ce qui permet aux
    boutons de la page distante d'agir sans ouvrir le moindre port ici : le
    serveur parle, Vercel repond. Aucune redirection, aucune entree.
    """
    corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/push",
        data=corps,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + tok},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            cmd = None
            try:
                cmd = (json.loads(r.read().decode("utf-8")) or {}).get("commande")
            except Exception:
                # Une reponse illisible ne doit pas faire passer un envoi
                # REUSSI pour un echec : le code HTTP fait foi, pas le corps.
                cmd = None
            return r.status, len(corps), cmd
    except urllib.error.HTTPError as e:
        # Ne jamais journaliser le jeton ni l'entete, seulement le code.
        return e.code, len(corps), None
    except Exception as e:
        _dire("envoi en echec : %s" % str(e)[:90])
        return None, len(corps), None


def appliquer(cmd):
    """
    Applique une commande venue du miroir.

    SEUL arb_control est touche, donc data/arb_control.json. Le bot FOREX vit
    dans bot_control.json et aucun chemin de cette fonction ne l'atteint : un
    OFF depuis la page crypto ne peut pas arreter le compte prop firm.

    Toute exception est avalee et journalisee. Le pousseur doit survivre a une
    commande malformee : son travail premier est d'alimenter le miroir, et une
    commande n'est jamais assez importante pour tuer la boucle.
    """
    if not isinstance(cmd, dict):
        return
    action = str(cmd.get("action") or "")
    if not action:
        return
    try:
        from aitb import arb_control
        if action == "RELANCER":
            arb_control.demander_redemarrage(par="miroir_vercel")
            _dire("commande RELANCER appliquee, demandee il y a %ss"
                  % cmd.get("ageSec"))
        elif action in arb_control.ETATS:
            arb_control.ecrire(action, par="miroir_vercel")
            _dire("commande %s appliquee, demandee il y a %ss"
                  % (action, cmd.get("ageSec")))
        else:
            _dire("commande IGNOREE, action inconnue : %s" % action[:20])
    except Exception as e:
        _dire("commande NON appliquee (%s) : %s" % (action[:20], str(e)[:90]))


def main():
    url = URL
    for a in sys.argv[1:]:
        if a.startswith("http"):
            url = a
    if not url:
        print("Usage : python pousser_crypto.py https://mon-projet.vercel.app")
        return 2

    tok = jeton()
    if not tok:
        print("Jeton absent. Cree %s avec le contenu de PUSH_TOKEN." % JETON)
        return 2

    _dire("=" * 58)
    _dire("POUSSEUR CRYPTO demarre | %d fragments publies | %s"
          % (len(CLES_PUBLIEES), url))
    _dire("=" * 58)

    envois = echecs = 0
    while True:
        try:
            code, taille, cmd = envoyer(url, tok, charge())
        except Exception as e:
            code, taille, cmd = None, 0, None
            _dire("construction de la charge en echec : %s" % str(e)[:90])

        if cmd:
            appliquer(cmd)

        if code == 200:
            envois += 1
            if envois == 1 or envois % 15 == 0:
                _dire("envoi %d OK, %d octets" % (envois, taille))
        else:
            echecs += 1
            _dire("envoi refuse, code %s (echec %d)" % (code, echecs))
            if code == 401:
                _dire("  401 = jeton refuse. Verifier PUSH_TOKEN sur Vercel et")
                _dire("  le contenu de jeton_envoi_crypto.txt, sans \\r final.")

        time.sleep(INTERVALLE)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        _dire("arret clavier, le tableau de bord local continue de tourner")
