import subprocess
import os
import time
import pygetwindow as gw

# ==========================
# CONFIGURATION
# ==========================
mt5_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
bat_path = r"D:\AITB\aitb_2.bat"
screenshots_path = r"D:\AITB\screenshots\EURUSD"
#https://chatgpt.com/g/EXEMPLE
aitb_gpt_url = "https://chatgpt.com/c/EXEMPLE"
chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

macro_open_gpt_path = r"D:\AITB\macros\open_gpt.mmmacro"
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080

# ==========================
# Helpers
# ==========================
def focus_and_maximize_window(
    include_substrings: list[str],
    exclude_substrings: list[str] | None = None,
    retries: int = 40,
    sleep_s: float = 0.25,
):
    """
    Maximise uniquement une fenêtre dont le titre matche include_substrings
    et ne matche pas exclude_substrings. Réessaye plusieurs fois.
    """
    exclude_substrings = exclude_substrings or []
    for _ in range(retries):
        for w in gw.getAllWindows():
            try:
                title = (w.title or "").lower()
                if any(s in title for s in [s.lower() for s in include_substrings]) and not any(
                    ex in title for ex in [e.lower() for e in exclude_substrings]
                ):
                    w.activate()
                    time.sleep(0.3)
                    try:
                        w.restore()
                    except:
                        pass
                    time.sleep(0.2)
                    w.maximize()
                    return True
            except Exception:
                continue
        time.sleep(sleep_s)
    return False

# ==========================
# 0. Ouvrir MetaTrader 5
# ==========================
subprocess.Popen(mt5_path)
time.sleep(1)

for w in gw.getAllWindows():
    try:    
        if "metatrader" in (w.title or "").lower():
            w.activate()
            time.sleep(0.5)
            w.maximize()
            break
    except Exception:
        pass

time.sleep(1.5)

# ==========================
# 1. Ouvrir dossier screenshots en plein écran
# ==========================
os.startfile(screenshots_path)
time.sleep(0.8)

for w in gw.getAllWindows():
    try:
        if "screenshots" in (w.title or "").lower():
            w.activate()
            time.sleep(0.5)
            w.maximize()
            break
    except Exception:
        pass

time.sleep(1.5)

# ==========================
# 2. Lancer aitb_2.bat → à gauche
# ==========================
subprocess.Popen(bat_path, shell=True)
time.sleep(0.8)

for w in gw.getAllWindows():
    try:
        title = (w.title or "").lower()
        if any(k in title for k in ["aitb", "launcher", "cmd", "invite", "terminal", "powershell"]):
            w.activate()
            time.sleep(0.5)
            w.moveTo(0, 0)
            w.resizeTo(SCREEN_WIDTH // 2, SCREEN_HEIGHT)
            break
    except Exception:
        pass

time.sleep(1.5)

# ==========================
# 3. Ouvrir ChatGPT (même profil, sans --start-maximized)
# ==========================
chrome_cmd = [
    chrome_path,
    "--new-window",
    aitb_gpt_url
]
subprocess.Popen(chrome_cmd)

# Attendre que la fenêtre apparaisse et maximiser UNIQUEMENT ChatGPT
focus_and_maximize_window(
    include_substrings=["New"],
    exclude_substrings=["gmail", "inbox", "claude", "outlook", "mail"],
    retries=40,
    sleep_s=0.25,
)

# ==========================
# 4. Lancer le script open_gpt.mmmacro
# ==========================
os.startfile(macro_open_gpt_path)
