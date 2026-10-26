# aitb/arb_control.py
"""
Interrupteur des bots d'ARBITRAGE. Strictement separe de celui du forex.

Pourquoi un second module plutot que reutiliser control.py
-----------------------------------------------------------
control.py pilote le bot forex via data/bot_control.json. Si le tableau de bord
crypto ecrivait dans ce meme fichier, appuyer sur OFF depuis la page crypto
arreterait le bot FOREX, sur un compte prop firm, sans que rien ne le signale.
Deux metiers, deux fichiers, aucune ambiguite possible.

Trois etats, transposes au metier de l'arbitrage :
  ON     le bot ouvre de nouvelles lignes et encaisse le financement
  PAUSE  aucune nouvelle ligne n'est ouverte, les positions deja en place
         continuent d'encaisser le financement toutes les 8 heures
  OFF    aucune nouvelle ligne, et le watchdog ne relance pas le cycle

Comme cote forex, AUCUN etat ne ferme une position. Une position d'arbitrage
est delta-neutre : la couper n'est ni urgent ni gratuit, puisque c'est la
fermeture qui coute des frais, pas le maintien.

CE QUE CE DRAPEAU FAIT AUJOURD'HUI, ET CE QU'IL NE FAIT PAS
-----------------------------------------------------------
Il enregistre une intention, de facon durable et relisible. A ce jour aucun
processus d'arbitrage ne tourne en continu : tant que le cycle ne consultera
pas peut_ouvrir(), le drapeau ne bloquera rien tout seul.

Tout runner d'arbitrage doit donc appeler peut_ouvrir() AVANT d'ouvrir une
ligne, et redemarrage_demande() dans sa boucle. Sans ces deux appels,
l'interrupteur est decoratif, et un interrupteur decoratif est un mensonge.
"""

import json
import time
from pathlib import Path

CONTROL_PATH = Path(__file__).resolve().parent.parent / "data" / "arb_control.json"
RESTART_PATH = Path(__file__).resolve().parent.parent / "data" / "arb_restart.json"

ON = "ON"
PAUSE = "PAUSE"
OFF = "OFF"
ETATS = (ON, PAUSE, OFF)


def lire() -> dict:
    """Etat courant. Fichier absent ou corrompu : on retombe sur ON."""
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
    """Le bot a-t-il le droit d'ouvrir une nouvelle ligne d'arbitrage ?"""
    return mode() == ON


def ecrire(nouveau_mode: str, par: str = "dashboard_crypto") -> dict:
    if nouveau_mode not in ETATS:
        raise ValueError(f"Mode inconnu: {nouveau_mode}")
    d = {"mode": nouveau_mode, "since": int(time.time()), "by": par}
    CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# Demande de redemarrage
# ---------------------------------------------------------------------------
# Un fichier depose, consomme par le runner. Jamais un kill de PID : le
# tableau de bord n'a pas a tuer de process, et un kill par filtre sur la
# ligne de commande tue aussi le shell qui le lance.

def demander_redemarrage(par: str = "dashboard_crypto") -> dict:
    d = {"demande": True, "at": int(time.time()), "by": par}
    RESTART_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESTART_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d


def redemarrage_demande(max_age: int = 120) -> bool:
    """
    Consomme la demande. Une demande trop vieille est ignoree et effacee, pour
    ne pas relancer le cycle au demarrage a cause d'un fichier oublie.
    """
    try:
        d = json.loads(RESTART_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    try:
        RESTART_PATH.unlink()
    except Exception:
        pass
    return bool(d.get("demande")) and (time.time() - d.get("at", 0)) < max_age
