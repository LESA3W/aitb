# aitb/watchdog.py
"""
Surveillant du runner live.

Pourquoi ce fichier existe
--------------------------
Un forward test de 4 semaines ne peut dependre ni d'une fenetre de terminal
laissee ouverte, ni d'une session Claude Code, ni de la vigilance de l'operateur.
Trois pannes reelles ont ete observees le 2026-09-10 :

  1. le bot lance en tache de fond est mort avec la session parente
  2. le PC etait regle pour dormir apres 20 minutes
  3. MT5 tournait mais avait perdu sa session de trading (Authorization failed),
     le bot ne pouvait donc pas demarrer

Le watchdog traite les trois : il verifie que le terminal MT5 est lance et
joignable, relance le bot des qu'il s'arrete, ecrit un battement de coeur, et
previent physiquement si la situation ne se resout pas toute seule.

Lancement:
    python -m aitb.watchdog
    python -m aitb.watchdog --status    lit le heartbeat et sort
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import CONFIG
from utils.time_logs import log
from utils.colors import colors

from aitb import control
from aitb import arb_control
from aitb import market_data as md

PROJECT_ROOT = md.PROJECT_ROOT
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
HEARTBEAT = PROJECT_ROOT / "data" / "heartbeat.json"

MT5_EXE = CONFIG.get("MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")

CHECK_EVERY = 30          # secondes entre deux verifications
MT5_BOOT_WAIT = 45        # laisse au terminal le temps de se connecter
RESTART_BACKOFF = [10, 30, 60, 120, 300]   # progression avant chaque relance
ALERT_AFTER_FAILURES = 3  # nombre d'echecs consecutifs avant alerte physique

FLASH_SCRIPT = Path.home() / ".claude" / "hooks" / "flash.py"

# Le bot d'arbitrage papier n'a pas besoin de MT5 ni du terminal : il lit l'API
# publique OKX. On le fait battre ici plutot que dans une boucle separee, parce
# qu'une boucle lancee a la main meurt au premier redemarrage du PC et que le
# track record serait alors troue. Le watchdog, lui, est deja supervise.
ARB_EVERY = 900          # secondes entre deux relevés de financement
_arb_dernier = 0.0


def _cycle_arb(module, nom):
    """
    Un cycle pour UN bot d'arbitrage. Renvoie le nombre de versements encaisses.

    Les deux modules ont la meme forme mais pas les memes noms, heritage de leur
    ecriture separee :
        arb_bot : cycle_papier()  frais_entree  cumul_usdt  financement_annualise_pct
        arb_eu  : cycle()         frais         cumul       annuel_pct
    On normalise ici plutot que de renommer dans des modules qui tournent deja
    et dont la sortie console et les journaux dependent de ces noms.
    """
    import json as _json
    cycle = getattr(module, "cycle_papier", None) or getattr(module, "cycle")
    etat = module._charger(module.ETAT, None)
    if etat is None:
        return 0
    etat, evts = cycle(etat)
    module._sauver(module.ETAT, etat)
    if not evts:
        return 0
    with open(module.JOURNAL, "a", encoding="utf-8") as h:
        for e in evts:
            h.write(_json.dumps(e) + chr(10))
    b = module.bilan(etat)
    ann = b.get("financement_annualise_pct", b.get("annuel_pct", 0.0))
    net = b.get("cumul_usdt", b.get("cumul", 0.0))
    log(colors("💰 %s: %d versement(s) | financement %+.4f $ (%.2f%%/an) "
               "| net %+.4f $" % (nom, len(evts), b["financement"], ann, net),
               "cyan"))
    return len(evts)


def _tic_arbitrage():
    """
    Un cycle des bots d'arbitrage papier. Ne doit JAMAIS faire tomber le
    watchdog : sa mission premiere reste la surveillance du bot de trading.

    DEUX bots, pas un. arb_eu etait absent de cette boucle : ses 12 lignes
    X-Perps avaient paye leurs frais d'entree et n'encaissaient plus rien depuis
    38 heures, avec 0 versement au compteur. Un track record troue par un oubli
    de cablage, pas par le marche.

    L'interrupteur consulte est arb_control, JAMAIS control. Couper le bot forex
    depuis la page crypto, sur un compte prop firm en cours d'evaluation, serait
    exactement le genre de confusion qui coute un compte.
    """
    global _arb_dernier

    # Une demande de redemarrage force un releve immediat au lieu d'attendre la
    # fin des 15 minutes. C'est le seul sens que "relancer" peut avoir ici : il
    # n'y a aucun process d'arbitrage a tuer, juste un releve a refaire.
    try:
        if arb_control.redemarrage_demande():
            _arb_dernier = 0.0
            log(colors("🔄 Arbitrage: releve force depuis le tableau de bord",
                       "cyan"))
    except Exception:
        pass

    if time.time() - _arb_dernier < ARB_EVERY:
        return
    _arb_dernier = time.time()

    try:
        # OFF coupe le releve. PAUSE ne le coupe PAS, volontairement : les
        # positions deja ouvertes continuent d'encaisser le financement toutes
        # les 8 heures, et ne pas le comptabiliser trouerait le track record
        # sans rien empecher. PAUSE n'interdit que l'OUVERTURE de nouvelles
        # lignes, ce que cette boucle ne fait de toute facon jamais.
        if arb_control.mode() == arb_control.OFF:
            return
        from aitb import arb_bot, arb_eu
        for module, nom in ((arb_bot, "Arbitrage USDT"),
                            (arb_eu, "Arbitrage X-Perps")):
            try:
                _cycle_arb(module, nom)
            except Exception as e:
                # Un bot qui echoue ne doit pas empecher l'autre d'encaisser.
                log(colors(f"⚠️ {nom}: {e}", "yellow"))
    except Exception as e:
        log(colors(f"⚠️ Cycle arbitrage: {e}", "yellow"))


def _alert(reason: str):
    """
    Alerte physique via les ampoules Tuya deja installees.
    Fail-silent : une alerte qui plante ne doit jamais arreter le watchdog.
    """
    log(colors(f"🚨 ALERTE: {reason}", "red"))
    if not FLASH_SCRIPT.exists():
        return
    try:
        subprocess.Popen([sys.executable, str(FLASH_SCRIPT), "input"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _mt5_process_running() -> bool:
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == "terminal64.exe":
                return True
    except Exception:
        pass
    return False


def _mt5_api_ready():
    """
    (ok, message). Le process peut tourner sans que l'API reponde : c'est le cas
    quand le terminal a perdu sa session de trading. Il faut distinguer les deux,
    sinon on relance le bot en boucle contre un terminal qui ne repondra jamais.
    """
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            err = mt5.last_error()
            return False, f"initialize a echoue {err}"
        info = mt5.account_info()
        term = mt5.terminal_info()
        ok = bool(info) and bool(getattr(term, "connected", False))
        detail = f"compte {getattr(info, 'login', '?')} equity {getattr(info, 'equity', '?')}"
        mt5.shutdown()
        return (True, detail) if ok else (False, "terminal non connecte au serveur")
    except Exception as e:
        return False, f"exception {e}"


def _launch_mt5() -> bool:
    exe = Path(MT5_EXE)
    if not exe.exists():
        log(colors(f"⚠️ Terminal MT5 introuvable: {exe}", "yellow"))
        return False
    try:
        subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(colors(f"🚀 Terminal MT5 lance, attente {MT5_BOOT_WAIT}s...", "cyan"))
        time.sleep(MT5_BOOT_WAIT)
        return True
    except Exception as e:
        log(colors(f"⚠️ Lancement MT5 impossible: {e}", "yellow"))
        return False


def _write_heartbeat(state: str, extra: dict = None):
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": state,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "updated_ts": int(time.time()),
        }
        payload.update(extra or {})
        HEARTBEAT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_heartbeat() -> dict:
    try:
        return json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _battre_avant_releve():
    """
    Rafraichit l'horodatage du heartbeat sans toucher a son contenu.

    MESURE DU 2026-09-14 : un releve d'arbitrage prend 40,3 s (arb_bot 23,3 +
    arb_eu 17,0), soit 114 appels pagines a OKX dont environ 9 s de sommeil
    delibere entre les pages. Pendant ce temps la boucle ne bat pas, et le
    heartbeat atteint 70 s alors que status() declare MORT a 90 s. Ca passe,
    mais avec 22 % de marge seulement, et ce releve tombe 4 fois par heure.

    Une pastille rouge qui crie au loup finit par etre ignoree, et c'est ce
    jour-la qu'elle aura raison. On bat donc AVANT le releve, pas seulement
    apres.

    Contrepartie assumee : si le bot meurt PENDANT un releve, son deces est
    masque au plus 40 s. La boucle le detecte et le relance juste apres, dans
    la meme iteration. Un retard d'affichage de 40 s coute moins cher qu'une
    fausse alerte garantie quatre fois par heure.
    """
    hb = read_heartbeat()
    if not hb:
        return
    try:
        hb["updated_at"] = datetime.now().isoformat(timespec="seconds")
        hb["updated_ts"] = int(time.time())
        HEARTBEAT.write_text(json.dumps(hb, indent=2), encoding="utf-8")
    except Exception:
        pass


def _spawn_bot() -> subprocess.Popen:
    """Lance aitb.live en heritant de la console, pour garder les logs visibles."""
    return subprocess.Popen(
        [str(PYTHON), "-u", "-m", "aitb.live"],
        cwd=str(PROJECT_ROOT),
        env={**_env_utf8()},
    )


def _env_utf8():
    import os
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# Drapeaux SetThreadExecutionState (winbase.h)
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _empecher_veille(actif: bool = True) -> bool:
    """
    Demande a Windows de ne pas mettre la machine en veille tant que le
    watchdog tourne.

    Pourquoi : dans la nuit du 2026-10 au 2026-11 septembre, le PC s'est
    endormi et le bot avec. Les reglages powercfg ne suffisaient pas, la
    machine etant en Modern Standby (S0 Low Power Idle). Un verrou porte par le
    process est plus fiable qu'un reglage global : il vit et meurt avec le
    watchdog, et il ne laisse pas la machine bloquee eveillee apres coup.

    Limite honnete : ceci bloque la veille par INACTIVITE. Une veille demandee
    explicitement (menu Demarrer, fermeture du capot) passera quand meme. Pour
    du 24/7 vraiment garanti, il faut un VPS.
    """
    try:
        import ctypes
        etat = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if actif else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(etat))
    except Exception:
        return False


def _autre_watchdog_vivant() -> int:
    """
    PID d'un autre watchdog deja en cours, 0 sinon.

    Pourquoi ce garde-fou : le 2026-09-11, deux watchdogs ont tourne en meme
    temps apres un kill qui avait silencieusement echoue. Chacun a lance son
    propre bot, donc deux processus envoyaient des ordres sur le meme compte.
    Aucune position en double cette fois (ONE_POSITION_RULE et live_state.json
    ont tenu), mais sur un compte reel c'est exactement comme ca qu'on double
    une taille de position sans s'en rendre compte.
    """
    import os
    try:
        import psutil
    except Exception:
        return 0

    # .venv/Scripts/python.exe est un lanceur : il relance un python enfant avec
    # EXACTEMENT la meme ligne de commande. Sans exclure sa propre ascendance, le
    # watchdog se prend lui-meme pour un doublon et refuse de demarrer.
    famille = {os.getpid()}
    try:
        moi = psutil.Process()
        famille |= {a.pid for a in moi.parents()}
        famille |= {e.pid for e in moi.children(recursive=True)}
    except Exception:
        pass

    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["pid"] in famille:
            continue
        if "python" not in (proc.info["name"] or "").lower():
            continue
        try:
            if "aitb.watchdog" in " ".join(proc.cmdline()[1:]):
                return proc.info["pid"]
        except Exception:
            continue
    return 0


def run():
    autre = _autre_watchdog_vivant()
    if autre:
        log(colors(f"⛔ Un watchdog tourne deja (PID {autre}). Demarrage annule.", "red"))
        log(colors("   Deux watchdogs = deux bots = ordres en double.", "yellow"))
        log(colors(f"   Pour reprendre la main : taskkill /F /PID {autre}", "white"))
        return

    log(colors("🛡️  A.I.T.B WATCHDOG demarre", "cyan"))
    log(colors("    veille   " + ("bloquee tant que le watchdog tourne"
                                  if _empecher_veille(True)
                                  else "NON bloquee, le PC peut s'endormir"), "white"))
    log(colors(f"    projet   {PROJECT_ROOT}", "white"))
    log(colors(f"    heartbeat {HEARTBEAT}", "white"))
    print("")

    proc = None
    failures = 0
    restarts = 0
    started_at = datetime.now()

    try:
        while True:
            # L'arbitrage papier ne depend pas de MT5, et surtout pas de
            # l'interrupteur du bot FOREX : couper le trading ne doit pas
            # trouer le releve de financement. Il a son propre interrupteur,
            # arb_control, consulte dans _tic_arbitrage().
            #
            # Le battement precede le releve parce que ce dernier bloque la
            # boucle 40 s. Voir _battre_avant_releve().
            _battre_avant_releve()
            _tic_arbitrage()

            # ---------- 1. le terminal MT5 doit tourner ----------
            if not _mt5_process_running():
                log(colors("⚠️ Terminal MT5 absent", "yellow"))
                _write_heartbeat("mt5_absent")
                if not _launch_mt5():
                    failures += 1
                    if failures >= ALERT_AFTER_FAILURES:
                        _alert("terminal MT5 introuvable ou impossible a lancer")
                    time.sleep(CHECK_EVERY)
                    continue

            # ---------- 2. l'API doit repondre ----------
            api_ok, detail = _mt5_api_ready()
            if not api_ok:
                # Cas typique : le terminal tourne mais s'est deconnecte du compte.
                # Aucune relance automatique ne resout ca, il faut une action humaine.
                log(colors(f"⚠️ API MT5 indisponible: {detail}", "yellow"))
                _write_heartbeat("mt5_deconnecte", {"detail": detail})
                failures += 1
                if failures == ALERT_AFTER_FAILURES:
                    _alert("MT5 deconnecte du compte, reconnexion manuelle necessaire")
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc = None
                time.sleep(CHECK_EVERY)
                continue

            # ---------- 2bis. interrupteur du tableau de bord ----------
            if control.mode() == control.OFF:
                if proc and proc.poll() is None:
                    log(colors("⏹️  OFF demande depuis le tableau de bord, arret du bot", "yellow"))
                    proc.terminate()
                    proc = None
                _write_heartbeat("off", {"mt5": detail})
                time.sleep(CHECK_EVERY)
                continue

            # ---------- 2ter. redemarrage demande depuis le tableau de bord ----------
            # Consomme avant le bloc suivant pour que la relance soit immediate,
            # sans backoff et sans incrementer le compteur de pannes.
            if control.redemarrage_demande():
                log(colors("🔄 Redemarrage demande depuis le tableau de bord", "cyan"))
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except Exception:
                        proc.kill()
                proc = None
                _write_heartbeat("redemarrage", {"mt5": detail})

            # ---------- 3. le bot doit tourner ----------
            if proc is None or proc.poll() is not None:
                if proc is not None:   # arret subi, pas un redemarrage demande
                    code = proc.poll()
                    log(colors(f"⚠️ Bot arrete (code {code}), relance...", "yellow"))
                    restarts += 1
                    delay = RESTART_BACKOFF[min(restarts - 1, len(RESTART_BACKOFF) - 1)]
                    if restarts >= ALERT_AFTER_FAILURES:
                        _alert(f"le bot a redemarre {restarts} fois")
                    time.sleep(delay)
                proc = _spawn_bot()
                log(colors(f"✅ Bot lance (PID {proc.pid})", "green"))

            failures = 0
            _write_heartbeat(control.mode().lower(), {
                "bot_pid": proc.pid,
                "mt5": detail,
                "restarts": restarts,
                "uptime_h": round((datetime.now() - started_at).total_seconds() / 3600, 2),
            })
            time.sleep(CHECK_EVERY)

    except KeyboardInterrupt:
        print("")
        log(colors("⛔ Watchdog interrompu au clavier", "yellow"))
        _write_heartbeat("arrete_manuellement")
    finally:
        _empecher_veille(False)   # on rend la main a Windows
        if proc and proc.poll() is None:
            log(colors("   arret du bot...", "white"))
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


def status():
    hb = read_heartbeat()
    if not hb:
        print("  Aucun heartbeat. Le watchdog n'a jamais tourne.")
        return
    age = datetime.now() - datetime.fromtimestamp(hb.get("updated_ts", 0))
    alive = age < timedelta(seconds=CHECK_EVERY * 3)
    print(f"  etat        {hb.get('state')}")
    print(f"  mis a jour  {hb.get('updated_at')}  (il y a {int(age.total_seconds())}s)")
    print(f"  watchdog    {'VIVANT' if alive else 'MORT ou bloque'}")
    for k in ("bot_pid", "mt5", "restarts", "uptime_h", "detail"):
        if k in hb:
            print(f"  {k:11s} {hb[k]}")


if __name__ == "__main__":
    if "--status" in sys.argv[1:]:
        status()
    else:
        run()
