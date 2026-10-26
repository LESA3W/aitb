# run.py
import time

from utils.time_logs import log
from utils.colors import colors
from config.settings import CONFIG

# Bannière (optionnelle)
try:
    from utils.aitb_banner import aitb_banner
except Exception:
    aitb_banner = None
    
# Scheduler (nouveau point d'entrée) + fallback ancien run_cycle
try:
    from aitb.scheduler import start_scheduler
except Exception:
    start_scheduler = None

try:
    from aitb.scheduler import run_cycle
except Exception:
    run_cycle = None

# Animation de démarrage (log visuel)
try:
    from aitb.main import loading_animation_1
except Exception:
    def loading_animation_1(duration=1):
        time.sleep(duration)

# INITIALISATION LOGS.TXT
try:
    from aitb.logger import init_logs_txt
    init_logs_txt()  # Supprime l'ancien et crée un nouveau logs.txt
except Exception as e:
    print(f"⚠️ Impossible d'initialiser logs.txt: {e}")


def _boot_banner():
    time.sleep(1)
    if aitb_banner:
        try:
            aitb_banner()
        except Exception:
            pass
    time.sleep(1)
    print("\n")


if __name__ == "__main__":
    _boot_banner()
    log(colors("🐐 A.I.T.B ", "cyan") + colors("--> ", "white") + colors("🟢 ON", "green"))
    time.sleep(1)
    print("")

    # Petit effet de chargement
    loading_animation_1()

    # Démarre le scheduler si dispo, sinon fallback: un run unique
    if start_scheduler:
        start_scheduler()
    else:
        log(colors("⚠️ start_scheduler() indisponible — exécution d'un cycle unique.", "yellow"))
        if run_cycle:
            run_cycle()
        else:
            log(colors("❌ Aucun planificateur ni run_cycle disponible.", "red"))