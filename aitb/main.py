# aitb/main.py
"""
aitb/
│
├── main.py                # Logique générale (orchestration locale)
├── scheduler.py           # Planification des cycles
├── prompt.py              # Instructions GPT (optionnel, non requis ici)
├── read_chatgpt_output.py # Envoi capture + OCR
├── trade_manager.py       # Gestion ordres MT5
├── settings.py            # Config
│
├── preflight.py           # Étape 1 : pré-checks (optionnel ici)
├── filters.py             # Étape 3 : validation du signal GPT (utilisé ici)
├── monitor.py             # Étape 6 : suivi post-exécution
├── logger.py              # Étape 7 : journalisation
└── utils/                 # Fonctions diverses
"""

import os
import time
import datetime
import pytz
import win32gui
import win32ui
import win32con

from tqdm import tqdm

from config.settings import CONFIG
from utils.time_logs import log
from utils.colors import colors

from aitb.read_chatgpt_output import get_decision_from_gpt_output
from aitb.trade_manager import initialize_mt5, open_trade

# (facultatif mais conseillé si tu as déjà ajouté filters.py / monitor.py)
try:
    from aitb.filters import validate_signal, remember_trade_time
except Exception:
    validate_signal = None
    remember_trade_time = None

try:
    from aitb.monitor import record_event, capture_position_snapshot
except Exception:
    record_event = None
    capture_position_snapshot = None

# ➕ Reverse auto si signal contraire
try:
    from aitb.monitor import get_open_position, close_position
except Exception:
    get_open_position = None
    close_position = None

# ➕ Contexte & perf tracking
try:
    from aitb.monitor import attach_context, note_open_ticket
except Exception:
    attach_context = None
    note_open_ticket = None

# -----------------------------
# État global (utilisé par OCR)
# -----------------------------
screenshots_list: list[str] = []


# -----------------------------
# Utilitaires internes
# -----------------------------
def _now():
    return datetime.datetime.now(pytz.timezone(CONFIG.get("TIME_ZONE", "Europe/Paris")))


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _find_mt5_window():
    """Retourne le premier handle de fenêtre qui matche WINDOW_KEYWORD, sinon None."""
    keyword = CONFIG.get("WINDOW_KEYWORD", "").lower().strip()
    if not keyword:
        log(colors("⚠️ WINDOW_KEYWORD non défini dans CONFIG.", "yellow"))
        return None

    hwnds = []

    def _collector(hwnd, acc):
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and keyword in title.lower():
                    acc.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(_collector, hwnds)
    return hwnds[0] if hwnds else None


# -----------------------------
# Capture d'écran MT5
# -----------------------------
def capture_mt5_window(index: int):
    """Capture la fenêtre MT5 complète et pousse le chemin dans screenshots_list."""
    now = _now()
    label_minutes = index * CONFIG.get("INTERVAL_MINUTES", 5)
    timestamp = now.strftime("%Mm%Ss")
    filename = f"{index+1}-EURUSD_M{label_minutes}_{timestamp}.png"

    folder = CONFIG.get("SCREENSHOTS_FOLDER", "SCREENSHOTS/EURUSD")
    _ensure_dir(folder)
    filepath = os.path.join(folder, filename)

    hwnd = _find_mt5_window()
    if not hwnd:
        log(colors(f"❌ MT5 is not OPENED --> '{CONFIG.get('WINDOW_KEYWORD')}' not found.", "red"))
        return None

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top

        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()

        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfcDC, width, height)
        saveDC.SelectObject(bitmap)
        saveDC.BitBlt((0, 0), (width, height), mfcDC, (0, 0), win32con.SRCCOPY)

        bitmap.SaveBitmapFile(saveDC, filepath)

        win32gui.DeleteObject(bitmap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)

        screenshots_list.append(filepath)
        log(f"{colors('📸 Screenshot', 'yellow') + colors(' -->', 'white')}  {colors(filename, 'magenta')}")
        return filepath
    except Exception as e:
        log(colors(f"❌ Échec capture MT5: {e}", "red"))
        return None


# -----------------------------
# Animation de démarrage
# -----------------------------
def loading_animation_1(duration=3):
    now = _now()
    date_str = now.strftime("[%H:%M:%S]")
    total_steps = 30  # 30 steps over 3 seconds = 100 ms per step
    sleep_time = max(0.01, duration / total_steps)

    for _ in tqdm(
        range(total_steps),
        desc=f"{colors(date_str, 'blue')} {colors('🐐 A.I.T.B ', 'cyan')}{colors('is', 'white')}{colors(' starting... ⏳', 'green')}",
        ncols=90,
        bar_format="{desc} [{bar}] " + colors("{percentage:3.0f}%", "green"),
    ):
        time.sleep(sleep_time)

    time.sleep(0.5)


# -----------------------------
# Exécution de la décision (+ reverse auto)
# -----------------------------
def execute_decision(decision: str | None):
    if not decision:
        log(colors("⚠️ No decision received. Skipping execution.", "yellow"))
        if record_event:
            record_event({"type": "noop", "reason": "no_decision"})
        return

    decision = decision.upper()

    if decision not in ("BUY", "SELL", "NOTHING"):
        log(colors("❌ Invalid decision received. Skipping.", "red"))
        if record_event:
            record_event({"type": "rejected", "reason": "invalid_decision", "decision": decision})
        return

    if decision == "NOTHING":
        log(colors("No trade executed.", "green"))
        if record_event:
            record_event({"type": "noop", "decision": "NOTHING"})
        return

    # 1) Initialiser/valider MT5 AVANT filtres (ticks/spread/ATR)
    if not initialize_mt5():
        log(colors("❌ MT5 init failed (connection/symbol).", "red"))
        if record_event:
            record_event({"type": "error", "reason": "init_mt5_failed"})
        return

    # 1bis) Reverse auto si une position opposée est ouverte
    if CONFIG.get("HARD_REVERSE_ON_SIGNAL", True) and get_open_position and close_position:
        try:
            pos = get_open_position()
            if pos:
                current_side = "BUY" if pos.type == 0 else "SELL"
                if (current_side == "BUY" and decision == "SELL") or (current_side == "SELL" and decision == "BUY"):
                    ok = close_position(pos.ticket, pos.volume)
                    if ok:
                        log(colors(f"🔁 Reverse: {current_side} fermé avant {decision}", "magenta"))
                        if record_event:
                            record_event({"type": "closed_for_reverse", "prev_side": current_side, "ticket": pos.ticket})
                    else:
                        log(colors("❌ Impossible de fermer la position pour reverse.", "red"))
                        return
        except Exception:
            # on ne bloque pas si le reverse échoue silencieusement
            pass

    # 2) Filtres (spread/ATR/session/one-position/cooldown)
    if validate_signal:
        ok, reason = validate_signal(decision)
        if not ok:
            log(colors(f"⛔ Signal rejeté: {reason}", "red"))
            if record_event:
                record_event({"type": "rejected", "reason": reason, "decision": decision})
            return

    # 3) Envoi de l'ordre au marché
    res = open_trade(decision)
    if res is not None:
        # mémoriser le temps & capture post envoi
        if remember_trade_time:
            remember_trade_time()
        if capture_position_snapshot:
            capture_position_snapshot(tag="after_send")

        # mémoriser le ticket de position pour le logger de performance
        if note_open_ticket and get_open_position:
            try:
                pos = get_open_position()
                if pos:
                    note_open_ticket(pos.ticket)
            except Exception:
                pass

        if record_event:
            record_event({"type": "executed", "decision": decision, "ticket": getattr(res, "order", None)})


# -----------------------------
# Cycle d'analyse
# -----------------------------
def run_cycle():
    # 1) Capture
    capture_mt5_window(len(screenshots_list))

    # 2) Lorsque le quota d'images est atteint → envoi + OCR + exécute
    if len(screenshots_list) >= CONFIG.get("IMAGES_PER_ANALYSIS", 3):
        # Contexte → attaché au prochain trade
        ctx = {
            "cycle_ts": _now().isoformat(),
            "screens": [os.path.basename(p) for p in screenshots_list],
            # Tu peux ajouter ici "session", "spread_pips", "notes", "news" si tu les as sous la main
        }
        if attach_context:
            try:
                attach_context(ctx)
            except Exception:
                pass

        # IMPORTANT : ne PAS passer screenshots_list (la fonction la lit déjà en interne)
        decision = get_decision_from_gpt_output()
        execute_decision(decision)
        screenshots_list.clear()
