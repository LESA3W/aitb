"""make_mt5_screenshot.py - builds the MT5 terminal screenshot for the docs.

Starts from a chart capture taken by the legacy branch of the bot, then
repaints the Trade panel with FICTIONAL positions and balances. No account
number, no real equity, nothing identifying is left on the image.

    python tools/make_mt5_screenshot.py <source.png>
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images" / "mt5-terminal.png"
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else None

BG_ROW, BG_SUM = (44, 44, 44), (68, 68, 68)
TXT, GREEN, RED = (222, 222, 222), (110, 205, 125), (225, 105, 105)
COLS = [45, 250, 430, 660, 830, 1010, 1180, 1355, 1530, 1905]

ROWS = [
    ("EURUSD", "214 880 913", "2026.09.12 08:15:02", "buy", "1.50",
     "1.15802", "1.15742", "1.15942", "1.16021", "+1 642.50"),
    ("EURUSD", "214 881 402", "2026.09.12 09:02:44", "sell", "1.50",
     "1.15931", "1.15991", "1.15791", "1.15874", "+855.00"),
    ("XAUUSD", "214 882 117", "2026.09.12 10:48:19", "buy", "0.40",
     "3 412.65", "3 404.90", "3 428.40", "3 410.12", "-101.20"),
]

SUMMARY = ("Balance: 52 480.00 USD   Equity: 54 876.30   Margin: 3 412.00   "
           "Free Margin: 51 464.30   Margin Level: 1 608.04%")


def main():
    if not SRC or not SRC.exists():
        sys.exit("pass the path to a raw MT5 chart capture")
    im = Image.open(SRC).convert("RGB")
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 14)
    fb = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 14)

    d.rectangle([0, 714, im.width, 737], fill=BG_ROW)
    y = 716
    for row in ROWS:
        d.rectangle([0, y, im.width, y + 21], fill=BG_ROW)
        d.text((COLS[0], y + 3), row[0], font=fb, fill=TXT)
        for i, val in enumerate(row[1:9], start=1):
            d.text((COLS[i], y + 3), val, font=f, fill=TXT)
        d.text((COLS[9], y + 3), row[9], font=f, anchor="ra",
               fill=GREEN if row[9].startswith("+") else RED)
        y += 22

    d.rectangle([0, y, im.width, y + 21], fill=BG_SUM)
    d.text((COLS[0] - 15, y + 3), SUMMARY, font=fb, fill=TXT)
    d.text((COLS[9], y + 3), "+2 396.30", font=fb, fill=GREEN, anchor="ra")

    im.thumbnail((1600, 1600), Image.LANCZOS)
    im.save(OUT, optimize=True)
    print("written", OUT, im.size, OUT.stat().st_size // 1024, "kB")


if __name__ == "__main__":
    main()
