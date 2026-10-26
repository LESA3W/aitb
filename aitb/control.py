# aitb/control.py
"""
Interrupteur partage entre le tableau de bord et le bot.

Le dashboard ecrit dans un fichier, le bot le lit a chaque cycle. Pas de socket,
pas de serveur : un simple fichier JSON, donc ca survit a un redemarrage de
n'importe lequel des deux et ca ne peut pas se desynchroniser.

Trois etats :
  ON     le bot trade normalement
  PAUSE  le bot n'ouvre AUCUN nouveau trade, mais continue de gerer les
         positions deja ouvertes : break-even, suivi, journalisation.
         Les stops et objectifs restent chez le broker dans tous les cas.
  OFF    le bot n'ouvre aucun trade et le watchdog ne le relance pas.
         Les positions ouvertes gardent leur stop et leur objectif chez le
         broker, elles se fermeront toutes seules. Rien n'est cloture de force.

Aucun etat ne ferme une position. Couper le bot ne fait jamais perdre d'argent.
"""

import json
import time
from pathlib import Path

CONTROL_PATH = Path(__file__).resolve().parent.parent / "data" / "bot_control.json"
RESTART_PATH = Path(__file__).resolve().parent.parent / "data" / "bot_restart.json"

ON = "ON"
PAUSE = "PAUSE"
OFF = "OFF"
ETATS = (ON, PAUSE, OFF)


def lire() -> dict:
    """Etat courant. En cas de fichier absent ou corrompu, on retombe sur ON."""
    try:
        d = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        if d.get("mode") in ETATS:
            return d
    except Exception:
        pass
    return {"mode": ON, "since": int(time.time()), "by": "defaut"}


def mode() -> str:
    return lire()["mode"]


def peut_ouvrir() -> bool:
    """Le bot a-t-il le droit d'ouvrir un nouveau trade ?"""
    return mode() == ON


def ecrire(nouveau_mode: str, par: str = "dashboard") -> dict:
    if nouveau_mode not in ETATS:
        raise ValueError(f"Mode inconnu: {nouveau_mode}")
    d = {"mode": nouveau_mode, "since": int(time.time()), "by": par}
    CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# Demande de redemarrage
# ---------------------------------------------------------------------------
# Le tableau de bord depose un fichier, le watchdog le consomme et relance le
# bot. On passe par un fichier plutot que par un kill direct du PID pour deux
# raisons : le watchdog ne compte pas ce redemarrage comme une panne (donc pas
# d'alerte ampoules), et le dashboard n'a jamais besoin de tuer un process.
#
# Un redemarrage ne ferme AUCUNE position : les stops et objectifs sont chez le
# broker. Le bot relit l'etat du compte au demarrage et reprend la main.

def demander_redemarrage(par: str = "dashboard") -> dict:
    d = {"demande": True, "at": int(time.time()), "by": par}
    RESTART_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESTART_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d


def redemarrage_demande(max_age: int = 120) -> bool:
    """Consomme la demande. Une demande trop vieille est ignoree et effacee,
    pour ne pas relancer le bot au demarrage du watchdog a cause d'un fichier
    oublie."""
    try:
        d = json.loads(RESTART_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    try:
        RESTART_PATH.unlink()
    except Exception:
        pass
    return bool(d.get("demande")) and (time.time() - d.get("at", 0)) < max_age
