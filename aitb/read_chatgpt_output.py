def get_decision_from_gpt_output(delay_seconds=10):
    import time, datetime, os, re, subprocess, ctypes
    import pytz
    import pyautogui
    import pytesseract
    from PIL import ImageOps, ImageFilter
    from tqdm import tqdm

    from config.settings import CONFIG
    from utils.time_logs import log
    from utils.colors import colors

    from aitb.main import screenshots_list

    # 🖥️ DPI awareness (important si scaling ≠ 100%)
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    # 📅 Datetime
    now = datetime.datetime.now(pytz.timezone(CONFIG.get("TIME_ZONE", "Europe/Paris")))
    date_str = now.strftime("[%H:%M:%S]")

    # 🧠 Tesseract (si dispo)
    tess_path = CONFIG.get("TESSERACT_PATH")
    if tess_path:
        pytesseract.pytesseract.tesseract_cmd = tess_path

    # 🖼️ Zone de capture (toujours X,Y,WIDTH,HEIGHT)
    X, Y, W, H = CONFIG.get("GPT_REPLY_BBOX", (1220, 120, 1220, 940)) #left/top/right/bottom
    screen_w, screen_h = pyautogui.size()

    # ✅ Clamp (pas d'auto-conversion left/top/right/bottom)
    X = max(0, min(X, screen_w - 1))
    Y = max(0, min(Y, screen_h - 1))
    W = max(1, min(W, screen_w - X))
    H = max(1, min(H, screen_h - Y))

    if W <= 0 or H <= 0:
        log(colors(f"❌ BBOX invalide après clamp: (X={X},Y={Y},W={W},H={H})", "red"))
        return None

    # 🚀 Lance la macro d’envoi
    macros_dir = CONFIG.get("MACROS_FOLDER", "macros")
    macro_path = os.path.join(macros_dir, "send_screenshots.mmmacro")
    try:
        subprocess.Popen(macro_path, shell=True)
    except Exception as e:
        log(colors(f"⚠️ Macro non lancée ({macro_path}) : {e}", "yellow"))

    # ⏳ Attente post-macro
    post_macro_wait = max(0, int(CONFIG.get("POST_MACRO_WAIT", 16))) # 22 = temps que met la macro send_screenshots.mmmacro
    time.sleep(post_macro_wait)

    # 🧠 Log des fichiers envoyés
    if screenshots_list:
        try:
            files_str = " + ".join([colors(os.path.basename(f), "magenta") for f in screenshots_list])
            log(files_str + colors(" --> ", "white") + colors("sent ✅", "green"))
        except Exception:
            pass
    else:
        log(colors("⚠️ Aucun screenshot listé avant envoi.", "yellow"))

    time.sleep(1)

    # ⏳ Barre "GPT thinking"
    thinking_steps = max(1, int(CONFIG.get("GPT_THINKING_STEPS", 9)))
    for _ in tqdm(
        range(thinking_steps),
        desc=f"{colors(date_str,'blue')} {colors('🐐 A.I.T.B ','cyan')}{colors('GPT','white')}{colors(' thinking... ⏳','green')}",
        ncols=90,
        bar_format="{desc} [{bar}] " + colors("{percentage:3.0f}%", "green"),
    ):
        time.sleep(1)

    def _capture_and_ocr(tag=""):
        # 📸 Capture
        try:
            img = pyautogui.screenshot(region=(X, Y, W, H))
        except Exception as e:
            log(colors(f"❌ Capture GPT échouée: {e}", "red"))
            return ""

        # Debug visuel
        dbg_path = f"debug_bbox{tag}.png"
        try:
            img.save(dbg_path)
        except Exception:
            pass

        # 🔧 Prétraitement OCR
        proc = img.convert("L").filter(ImageFilter.SHARPEN)
        proc = proc.point(lambda p: 255 if p > 200 else (0 if p < 80 else p))
        inv = ImageOps.invert(proc)

        # 🧠 OCR (normal puis inversé)
        tess_cfg = r'--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/()[]{}:+-_*.,!?|<>#=~"\' 0123456789'
        try:
            txt = pytesseract.image_to_string(proc, config=tess_cfg) or ""
            if not txt.strip():
                txt = pytesseract.image_to_string(inv, config=tess_cfg) or ""
        except Exception as e:
            log(colors(f"❌ OCR échoué: {e}", "red"))
            return ""

        return txt

    # 🧠 OCR #1
    raw = (_capture_and_ocr(tag="")).strip().upper()

    # 🎯 Détection BUY/SELL/NOTHING
    decision = None
    for word in ("BUY", "SELL", "NOTHING"):
        if re.search(rf"\b{word}\b", raw):
            decision = word
            break

    if decision:
        stamp = colors('[' + datetime.datetime.now().strftime('%H:%M:%S') + ']', 'blue')
        msg = f"{stamp} {colors('🐐 A.I.T.B','cyan')} {colors('--> ','white')}{colors(decision,'green')} ✔️\n"
        print(msg)
        return decision

    # 🔁 Second chance
    time.sleep(max(0, int(CONFIG.get("GPT_FALLBACK_WAIT", 4))))
    raw2 = (_capture_and_ocr(tag="_retry")).strip().upper()
    for word in ("BUY", "SELL", "NOTHING"):
        if re.search(rf"\b{word}\b", raw2):
            decision = word
            stamp = colors('[' + datetime.datetime.now().strftime('%H:%M:%S') + ']', 'blue')
            msg = f"{stamp} {colors('🐐 A.I.T.B','cyan')} {colors('--> ','white')}{colors(decision,'green')} ✔️\n"
            print(msg)
            return decision

    # 🧪 Log utile si rien détecté
    log(colors("⚠️ No valid decision found in GPT output.", "red"))
    if raw or raw2:
        snippet = (raw2 or raw)[:200].replace("\n", " ")
        log(colors(f"🧪 OCR snippet: {snippet}", "yellow"))
    return None
