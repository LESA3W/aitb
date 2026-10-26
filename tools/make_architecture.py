"""make_architecture.py - generates docs/images/architecture.svg."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images" / "architecture.svg"

W, H = 1280, 980
BG, LANE, GRID = "#0b0f14", "#111823", "#1b2534"
TXT, MUT = "#e6edf3", "#8b98a8"
PUR, GRN, AMB, BLU, GRY = "#a855f7", "#10b981", "#f59e0b", "#3b82f6", "#64748b"
P = []


def lane(y, h, label, color):
    P.append('<rect x="24" y="%d" width="%d" height="%d" rx="16" fill="%s" stroke="%s"/>'
             % (y, W - 48, h, LANE, GRID))
    P.append('<rect x="24" y="%d" width="6" height="%d" rx="3" fill="%s"/>' % (y, h, color))
    P.append('<text x="50" y="%d" fill="%s" font-family="Segoe UI,Arial,sans-serif" '
             'font-size="13" font-weight="700" letter-spacing="2.5">%s</text>'
             % (y + 27, color, label))


def box(x, y, w, h, title, sub, color, dashed=False):
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    P.append('<rect x="%d" y="%d" width="%d" height="%d" rx="12" fill="#0e1520" '
             'stroke="%s" stroke-width="1.6"%s/>' % (x, y, w, h, color, dash))
    P.append('<rect x="%d" y="%d" width="%d" height="4" rx="2" fill="%s"/>' % (x, y, w, color))
    P.append('<text x="%d" y="%d" text-anchor="middle" fill="%s" '
             'font-family="Segoe UI,Arial,sans-serif" font-size="15" font-weight="700">%s</text>'
             % (x + w // 2, y + 32, TXT, title))
    for i, line in enumerate(sub):
        P.append('<text x="%d" y="%d" text-anchor="middle" fill="%s" '
                 'font-family="Consolas,monospace" font-size="11.5">%s</text>'
                 % (x + w // 2, y + 54 + i * 16, MUT, line))


def arrow(x1, y1, x2, y2, color, dashed=False):
    dash = ' stroke-dasharray="5 5"' if dashed else ""
    P.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2" '
             'marker-end="url(#m%s)"%s/>' % (x1, y1, x2, y2, color, color[1:], dash))


def label(x, y, text, color, size=11, anchor="middle"):
    P.append('<text x="%d" y="%d" text-anchor="%s" fill="%s" '
             'font-family="Consolas,monospace" font-size="%s">%s</text>'
             % (x, y, anchor, color, size, text))


P.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
         % (W, H, W, H))
P.append("<defs>")
for c in (PUR, GRN, AMB, BLU, GRY):
    P.append('<marker id="m%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
             'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
             'fill="%s"/></marker>' % (c[1:], c))
P.append("</defs>")
P.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, BG))
P.append('<text x="40" y="50" fill="%s" font-family="Segoe UI,Arial,sans-serif" '
         'font-size="27" font-weight="800">A.I.T.B &#183; system architecture</text>' % TXT)
P.append('<text x="40" y="76" fill="%s" font-family="Segoe UI,Arial,sans-serif" '
         'font-size="14">Offline research decides what to trade. The live loop only '
         'executes it.</text>' % MUT)

# ---------------------------------------------------------------- offline research
lane(100, 180, "OFFLINE RESEARCH", PUR)
rx = [52, 300, 548, 796, 1044]
rw, ry, rh = 188, 140, 110
RES = [
    ("Market data", ["MT5 history", "CSV cache"]),
    ("Backtest engine", ["bar by bar", "spread + fees"]),
    ("Sweep", ["parameter grid", "thousands of runs"]),
    ("Walk forward", ["out of sample", "vs random"]),
    ("Acceptance gate", ["100+ trades, DD &lt; 20%", "PF &gt; 1.3, beats B&amp;H"]),
]
for (t, s), x in zip(RES, rx):
    box(x, ry, rw, rh, t, s, PUR)
for a, b in zip(rx, rx[1:]):
    arrow(a + rw + 4, ry + rh // 2, b - 6, ry + rh // 2, PUR)


# ---------------------------------------------------------------- frozen rule
P.append('<rect x="440" y="306" width="400" height="48" rx="24" fill="#0e1520" '
         'stroke="%s" stroke-width="1.8"/>' % PUR)
P.append('<text x="640" y="336" text-anchor="middle" fill="%s" '
         'font-family="Consolas,monospace" font-size="14">config/settings.py &#183; '
         'LIVE_STRATEGY frozen</text>' % TXT)
arrow(1138, 250, 1138, 306, PUR)
arrow(1138, 330, 846, 330, PUR)
arrow(640, 356, 640, 400, GRN)

# ---------------------------------------------------------------- live loop
lane(382, 300, "LIVE LOOP &#183; deterministic, no LLM", GRN)
lx = [52, 300, 548, 796, 1044]
lw, ly, lh = 188, 422, 108
LIVE = [
    ("Market data", ["aitb/market_data", "OHLC + tick"]),
    ("Indicators", ["ATR, ADX, RSI", "numpy only"]),
    ("Strategy rule", ["aitb/strategies", "BUY / SELL / none"]),
    ("Filters", ["spread, session", "ATR, cooldown"]),
    ("Risk manager", ["lot from risk %", "volume clamp"]),
]
for (t, s), x in zip(LIVE, lx):
    box(x, ly, lw, lh, t, s, GRN)
for a, b in zip(lx, lx[1:]):
    arrow(a + lw + 4, ly + lh // 2, b - 6, ly + lh // 2, GRN)

lx2 = [176, 424, 672, 920]
ly2 = 560
LIVE2 = [
    ("Trade manager", ["order_send, IOC", "SL + 3 take profits"]),
    ("Multi-TP monitor", ["TP1 halves size", "break even, trailing"]),
    ("Logger", ["JSON per trade", "HTML dashboard"]),
    ("Dashboards", ["weekly + trades", "port 8080 / 8081"]),
]
for (t, s), x in zip(LIVE2, lx2):
    box(x, ly2, lw, 100, t, s, GRN if x < 900 else BLU)
for a, b in zip(lx2, lx2[1:]):
    arrow(a + lw + 4, ly2 + 50, b - 6, ly2 + 50, GRN)
arrow(1138, 530, 1138, 542, GRN)
P.append('<path d="M1138 536 L1138 544 L262 544 L262 554" fill="none" stroke="%s" '
         'stroke-width="2" marker-end="url(#m%s)"/>' % (GRN, GRN[1:]))


# ---------------------------------------------------------------- guards
lane(700, 128, "GUARDS &#183; always on", AMB)
gx = [52, 300, 548, 796, 1044]
gy, gw, gh = 736, 188, 76
GUARDS = [
    ("Preflight", ["checks before start"]),
    ("Prop firm guard", ["stops, then closes"]),
    ("Watchdog", ["restarts the bot"]),
    ("Heartbeat", ["proves it is alive"]),
    ("Control file", ["ON / PAUSE / OFF"]),
]
for (t, s), x in zip(GUARDS, gx):
    box(x, gy, gw, gh, t, s, AMB)
    if x >= 300:   # keep the lane label area clear
        arrow(x + gw // 2, gy - 6, x + gw // 2, 668, AMB)

# ---------------------------------------------------------------- legacy
lane(850, 118, "LEGACY &#183; first generation, kept for the record", GRY)
ex = [52, 300, 548, 796, 1044]
ey = 886
LEG = [
    ("Screenshot", ["MT5 window"]),
    ("Mouse macro", ["paste into ChatGPT"]),
    ("LLM answer", ["BUY / SELL / NOTHING"]),
    ("OCR", ["Tesseract reads it"]),
    ("Execution", ["not backtestable"]),
]
for (t, s), x in zip(LEG, ex):
    box(x, ey, 188, 68, t, s, GRY, dashed=True)
for a, b in zip(ex, ex[1:]):
    arrow(a + 192, ey + 34, b - 6, ey + 34, GRY, dashed=True)

P.append("</svg>")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(P), encoding="utf-8")
print("written", OUT, OUT.stat().st_size, "bytes")
