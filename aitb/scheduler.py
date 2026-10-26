# scheduler.py
import time
import schedule
import os
import glob
import shutil

# psutil est utile mais pas vital : on tombe en mode dégradé si absent
try:
    import psutil
except Exception:
    psutil = None

# Pour fermer les fenêtres par titre
import pygetwindow as gw
try:
    import win32gui, win32con
except Exception:
    win32gui = None
    win32con = None

from config.settings import CONFIG
from aitb.main import capture_mt5_window, screenshots_list, execute_decision
from aitb.read_chatgpt_output import get_decision_from_gpt_output
from utils.time_logs import log
from utils.colors import colors
from aitb.preflight import check_environment
from aitb.logger import refresh_open_rows_from_mt5

# 💡 Monitoring (BE/Trailing + perf à la clôture)
try:
    from aitb.monitor import monitor_positions_once, monitor_closed_positions_once
except Exception:
    monitor_positions_once = None
    monitor_closed_positions_once = None

# -- Cycle limit config
CYCLE_COUNT = 0
MAX_CYCLES = CONFIG.get("MAX_CYCLES", 999999)
JOB_CREATED = False  # évite la création multiple du même job

schedule.every(int(CONFIG.get("MONITOR_INTERVAL_SECONDS", 5))).seconds.do(refresh_open_rows_from_mt5)

def archive_screenshots():
    """Archive les screenshots dans le dossier old_screenshots"""
    folder_path = CONFIG["SCREENSHOTS_FOLDER"]
    old_path = r"D:\AITB\screenshots\old_screenshots"
    
    # Créer le dossier d'archive s'il n'existe pas
    os.makedirs(old_path, exist_ok=True)

    files = glob.glob(os.path.join(folder_path, "*"))
    for f in files:
        try:
            if os.path.isfile(f):
                fname = os.path.basename(f)
                dst = os.path.join(old_path, fname)
                # si un fichier du même nom existe déjà → on ajoute un suffixe timestamp
                if os.path.exists(dst):
                    base, ext = os.path.splitext(fname)
                    dst = os.path.join(old_path, f"{base}_{int(time.time())}{ext}")
                shutil.move(f, dst)
        except Exception as e:
            log(colors(f"Erreur en archivant {f} : {e}", "red"))

def close_mm_macro_files():
    """
    Termine TOUS les processus MiniMouseMacro (toute instance exécutant un .mmmacro).
    Si psutil n'est pas dispo, on log et on continue.
    """
    if psutil is None:
        log(colors("psutil indisponible : skip kill MiniMouseMacro", "yellow"))
        return

    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "MiniMouseMacro.exe" in name or ".mmmacro" in cmd.lower():
                proc.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed:
        log(colors(f"🐭 MMC --> ✅ KILLED", "green"))

def close_mmmacro_windows():
    """
    Ferme toutes les fenêtres dont le titre contient 'mmmacro' (insensible à la casse).
    Tentative soft via pygetwindow.close(), puis fallback WM_CLOSE si possible.
    """
    closed = 0
    targets = []
    try:
        targets = list(gw.getAllWindows())
    except Exception:
        targets = []

    for w in targets:
        try:
            title = (w.title or "").lower()
            if "mmmacro" in title:
                # tentative soft
                try:
                    w.activate()
                    time.sleep(0.1)
                    w.close()
                    closed += 1
                    continue
                except Exception:
                    pass

                # fallback WM_CLOSE si dispo
                if win32gui and win32con:
                    try:
                        hwnd = getattr(w, "_hWnd", None)
                        if hwnd:
                            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                            closed += 1
                    except Exception:
                        pass
        except Exception:
            continue

    if closed:
        log(colors(f"🧹 Fenêtres 'mmmacro' fermées: {closed}", "green"))

def _complete_if_needed():
    """
    Gère l'arrêt propre lorsque le nombre max de cycles est atteint.
    À appeler immédiatement après l'incrément de CYCLE_COUNT.
    """
    global CYCLE_COUNT
    if CYCLE_COUNT >= MAX_CYCLES:
        log(colors("All cycles", "white") + colors(f" ({MAX_CYCLES}) ", "yellow") + colors("completed ✅", "green"))
        time.sleep(1)
        print("")
        log(colors("🐐 A.I.T.B", "cyan") + colors(" --> ", "white") + colors("🔴 OFF", "red"))
        return schedule.CancelJob
    return None

def run_cycle():
    """
    Un cycle = éventuellement capture + envoi + décision + exécution + ménage
    (selon IMAGES_PER_ANALYSIS).
    
    ✅ MODIFIÉ : 
    - Capture IMMÉDIATE au début du cycle (pas d'attente)
    - Relance IMMÉDIATE du cycle suivant après le trade
    """
    global CYCLE_COUNT

    if CYCLE_COUNT >= MAX_CYCLES:
        log("✅ The requested number of cycles has been completed. Shutting down.")
        return schedule.CancelJob

    # Log d'entête de cycle (seulement quand on démarre un nouveau paquet d'images)
    if len(screenshots_list) == 0:
        log(colors("🔁 Cycle ", "white") + colors(f"#{CYCLE_COUNT + 1}", "yellow") + "\n")
        time.sleep(1)
        # ✅ PREMIÈRE CAPTURE IMMÉDIATE
        capture_mt5_window(len(screenshots_list))
        return None  # Sort et attend le prochain intervalle (5 min)

    # 2) Captures suivantes (après 5 minutes, 10 minutes, etc.)
    capture_mt5_window(len(screenshots_list))

    # 3) Lorsque le quota d'images est atteint, on envoie + OCR + exécute
    if len(screenshots_list) >= CONFIG.get("IMAGES_PER_ANALYSIS", 3):
        decision = get_decision_from_gpt_output()
        execute_decision(decision)

        # 4) Nettoyage
        #    a) vider screenshots
        screenshots_list.clear()
        archive_screenshots()

        #    b) tuer tous les processus MiniMouseMacro
        close_mm_macro_files()  # tue toutes les instances

        #    c) fermer toutes les fenêtres affichant "mmmacro" (équiv. Alt+F4/❌)
        close_mmmacro_windows()

        # 5) Incrément du compteur de cycles (après exécution d'un batch complet)
        CYCLE_COUNT += 1
        cancel = _complete_if_needed()
        if cancel is not None:
            return cancel
        
        # ✅ NOUVEAU : Attendre 5 secondes puis relancer IMMÉDIATEMENT le cycle suivant
        time.sleep(5)  # Petite pause de stabilité
        
        # Log du nouveau cycle + capture immédiate
        log(colors("🔁 Cycle ", "white") + colors(f"#{CYCLE_COUNT + 1}", "yellow") + "\n")
        time.sleep(1)
        capture_mt5_window(0)  # Première capture du nouveau cycle

    return None

def start_scheduler():
    """
    Armer le scheduler, annoncer 'Scheduler Started', puis lancer le 1er cycle.
    """
    global JOB_CREATED

    if not JOB_CREATED:
        # Armer les jobs AVANT tout run
        schedule.every(CONFIG["INTERVAL_MINUTES"]).minutes.do(run_cycle)

        monitor_every = int(CONFIG.get("MONITOR_INTERVAL_SECONDS", 5))
        if monitor_positions_once:
            schedule.every(monitor_every).seconds.do(monitor_positions_once)
        if monitor_closed_positions_once:
            schedule.every(monitor_every).seconds.do(monitor_closed_positions_once)

        JOB_CREATED = True

        check_environment()

        print("")
        log(colors("🔁 Scheduler Started", "green"))

        # ✅ Lancer immédiatement le premier cycle (capture immédiate)
        maybe_cancel = run_cycle()
        if maybe_cancel is schedule.CancelJob:
            return

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log(colors("⛔ Interruption clavier — arrêt propre.", "yellow"))
        return

if __name__ == "__main__":
    start_scheduler()