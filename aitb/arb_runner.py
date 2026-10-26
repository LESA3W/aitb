#!/usr/bin/env python3
# aitb/arb_runner.py
"""
Le processus qui fait VIVRE les bots d'arbitrage crypto.

VERROU CONTRE LE DOUBLE COMPTAGE
--------------------------------
Au 2026-09-15, aitb/watchdog.py contient _tic_arbitrage(), qui releve DEJA les
deux bots toutes les 900 s. Lancer ce runner EN PLUS compterait chaque versement
DEUX FOIS et les deux processus s'ecraseraient sur le meme JSON. Un track record
faux dans le sens flatteur est pire qu'absent, parce qu'on lui fait confiance.

Ce n'est pas laisse a la vigilance du lecteur : _watchdog_releve_deja() REFUSE
le demarrage tant que data/heartbeat.json bat. Un cran de surete vaut mieux
qu'un avertissement, et mieux qu'une suppression du fichier.

Le verrou s'ouvrira tout seul le jour ou _tic_arbitrage() sera retire du
watchdog. Condition convenue pour ce basculement, au premier des deux :
  1. l'evaluation FTMO se termine, reussie ou ratee
  2. l'arbitrage porte du capital reel et non plus du papier

Pourquoi ce fichier existe quand meme
--------------------------
Jusqu'au 2026-09-15, les bots d'arbitrage n'avaient AUCUN processus a eux. Ils
etaient releves par _tic_arbitrage() du watchdog FOREX. Quand ce watchdog a
migre sur le serveur maison, les deux bots sont morts en dommage collateral :
etats figes 4,6 h, dernier versement 8,2 h avant.

Une dependance cachee de ce genre ne se voit pas tant qu'elle tient. Ce module
la supprime : le cote crypto tourne desormais par lui-meme.

Et arb_papier.bat, le seul lanceur existant, ne relevait que arb_bot. arb_eu
n'a jamais eu de boucle, ce qui explique ses 0 versement pendant deux jours.

Ce que fait ce runner
---------------------
  - releve les DEUX bots a chaque tic, chacun dans son propre try/except pour
    qu'une panne de l'un n'empeche pas l'autre d'encaisser
  - honore aitb/arb_control.py : OFF arrete le releve, PAUSE le laisse tourner
    puisque le financement tombe que le bot regarde ou non
  - honore le drapeau RELANCER : un tic immediat au lieu d'attendre l'intervalle
  - ecrit data/arb_heartbeat.json pour que le tableau de bord sache s'il vit
  - empeche la mise en veille : sur un PC portable serveur, une veille coute
    des versements definitivement perdus

Verrou d'instance unique
------------------------
Par liaison d'un socket local, PAS par filtrage de ligne de commande. Filtrer
sur cmdline attrape ses propres shells et se tue soi-meme : voir la memoire
reference_psutil_cmdline_selfkill. Un bind est atomique, impose par l'OS, et
immunise contre la reutilisation de PID.

Aucune cle API, aucun compte, aucun euro engage. Mode papier strict.

Lancement :  python -m aitb.arb_runner
"""

import ctypes
import json
import socket
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aitb import arb_bot       # noqa: E402
from aitb import arb_control   # noqa: E402
from aitb import arb_eu        # noqa: E402

# Le financement tombe toutes les 4 heures. Relever toutes les 10 minutes laisse
# 24 occasions de rattraper un versement avant le suivant, tout en restant
# leger pour l'API publique d'OKX.
INTERVALLE = 600
PORT_VERROU = 8091
HEARTBEAT = ROOT / "data" / "arb_heartbeat.json"
JOURNAL = ROOT / "logs" / "arb_console.txt"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

# Le watchdog forex bat toutes les 30 s. Au-dela de 180 s sans battement il est
# mort ou arrete, et son _tic_arbitrage() ne releve plus rien.
HEARTBEAT_WATCHDOG = ROOT / "data" / "heartbeat.json"
AGE_WATCHDOG_VIVANT = 180


def _watchdog_releve_deja():
    """
    Cran de surete : le watchdog forex releve-t-il DEJA l'arbitrage ?

    Retourne l'age du battement en secondes s'il est vivant, None sinon.

    On lit le battement plutot que la liste des processus, parce que filtrer
    les processus par ligne de commande attrape ses propres shells : voir la
    memoire reference_psutil_cmdline_selfkill.

    LIMITE CONNUE, a ne pas oublier : ce fichier est LOCAL a la machine. Sur le
    serveur il reflete le watchdog du serveur, ce qui est le cas utile. Sur une
    autre machine il peut etre une branche morte, le verrou s'ouvrirait alors a
    tort. Le garde-fou reel contre ce cas est de ne faire tourner l'arbitrage
    qu'a UN seul endroit, et cet endroit est le serveur maison.
    """
    try:
        d = json.loads(HEARTBEAT_WATCHDOG.read_text(encoding="utf-8"))
        age = time.time() - float(d.get("updated_ts", 0))
    except Exception:
        return None
    return age if age < AGE_WATCHDOG_VIVANT else None


def _dire(msg):
    ligne = "%s  %s" % (datetime.now().strftime("%d/%m %H:%M:%S"), msg)
    print(ligne, flush=True)
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except Exception:
        pass


def _empecher_veille():
    """Sur un portable serveur, une veille coute des versements perdus."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        return True
    except Exception:
        return False


def _verrou():
    """
    Instance unique par liaison de socket, jamais par filtrage de cmdline.

    Le socket est garde ouvert pour toute la duree du process : le rendre
    global evite qu'un ramasse-miettes le ferme et libere le verrou.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PORT_VERROU))
        s.listen(1)
        return s
    except OSError:
        return None


def _battre(tics, versements, erreurs, debut):
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(json.dumps({
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "updated_ts": int(time.time()),
            "mode": arb_control.mode(),
            "tics": tics,
            "versements": versements,
            "erreurs": erreurs,
            "uptime_h": round((time.time() - debut) / 3600.0, 2),
        }, indent=2), encoding="utf-8")
    except Exception:
        pass


def _relever(module, nom, fonction):
    """
    Un bot, isole. Retourne le nombre de versements encaisses a ce tic.

    Chaque bot a son propre try/except : une panne reseau sur l'un ne doit pas
    empecher l'autre d'encaisser. C'est la lecon du watchdog forex, ou les deux
    bots partageaient un seul bloc de gestion d'erreur.
    """
    chemin = Path(module.ETAT)
    if not chemin.exists():
        _dire("[%s] aucun etat sur disque, rien a relever" % nom)
        return 0
    try:
        etat = json.loads(chemin.read_text(encoding="utf-8"))
    except Exception as e:
        _dire("[%s] etat illisible : %s" % (nom, str(e)[:60]))
        return 0

    avant = sum(p.get("versements", 0)
                for p in (etat.get("positions") or {}).values())
    try:
        resultat = fonction(etat)
        etat = resultat[0] if isinstance(resultat, tuple) else resultat
    except Exception as e:
        _dire("[%s] cycle en echec : %s" % (nom, str(e)[:80]))
        return 0

    try:
        chemin.write_text(json.dumps(etat, indent=2), encoding="utf-8")
    except Exception as e:
        _dire("[%s] ecriture de l'etat en echec : %s" % (nom, str(e)[:60]))
        return 0

    apres = sum(p.get("versements", 0)
                for p in (etat.get("positions") or {}).values())
    neufs = apres - avant
    if neufs > 0:
        _dire("[%s] %d versement%s encaisse%s"
              % (nom, neufs, "s" if neufs > 1 else "", "s" if neufs > 1 else ""))
    return max(neufs, 0)


def boucle():
    age = _watchdog_releve_deja()
    if age is not None:
        print("DEMARRAGE REFUSE : le watchdog bat depuis %.0f s." % age)
        print("Il releve deja l'arbitrage via _tic_arbitrage(), toutes les 900 s.")
        print("Lancer ce runner en plus compterait chaque versement DEUX FOIS")
        print("et les deux processus s'ecraseraient sur le meme fichier d'etat.")
        print()
        print("Pour utiliser ce runner : retirer _tic_arbitrage() de")
        print("aitb/watchdog.py, puis relancer. Le verrou s'ouvrira seul.")
        return 1

    verrou = _verrou()
    if verrou is None:
        print("Un autre arb_runner tourne deja (verrou sur le port %d)."
              % PORT_VERROU)
        return 1

    debut = time.time()
    tics = versements = erreurs = 0
    _dire("=" * 58)
    _dire("A.I.T.B ARBITRAGE CRYPTO - runner demarre")
    _dire("  intervalle %d s | veille bloquee : %s"
          % (INTERVALLE, "oui" if _empecher_veille() else "NON"))
    _dire("  mode papier strict : aucune cle, aucun compte, aucun euro")
    _dire("=" * 58)

    try:
        while True:
            mode = arb_control.mode()
            if mode == arb_control.OFF:
                _dire("[mode] OFF, releve suspendu")
            else:
                tics += 1
                # Le heartbeat bat AVANT le releve : un tic peut durer plusieurs
                # dizaines de secondes si OKX ralentit, et un battement trop
                # espace ferait passer le runner pour mort alors qu'il attend
                # simplement le reseau.
                _battre(tics, versements, erreurs, debut)
                try:
                    versements += _relever(arb_bot, "OKX USDT",
                                           arb_bot.cycle_papier)
                except Exception:
                    erreurs += 1
                    _dire("[OKX USDT] " + traceback.format_exc(limit=1).strip())
                try:
                    versements += _relever(arb_eu, "X-PERPS", arb_eu.cycle)
                except Exception:
                    erreurs += 1
                    _dire("[X-PERPS] " + traceback.format_exc(limit=1).strip())

            _battre(tics, versements, erreurs, debut)

            # Attente fractionnee, pour consommer une demande de relance sans
            # faire attendre l'utilisateur jusqu'a 10 minutes.
            reste = INTERVALLE
            while reste > 0:
                if arb_control.redemarrage_demande():
                    _dire("[RELANCER] demande recue, releve immediat")
                    break
                pause = min(15, reste)
                time.sleep(pause)
                reste -= pause
    except KeyboardInterrupt:
        _dire("arret demande au clavier, les positions restent ouvertes")
        return 0
    finally:
        try:
            verrou.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(boucle() or 0)
