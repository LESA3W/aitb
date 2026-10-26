"""demo_trades.py - rebuilds the Multi-TP trade dashboard with FICTIONAL trades.

Takes the real HTML template produced by aitb/logger.py and swaps its inline
data cache for a synthetic trading day. Used for the documentation screenshots
so no real trade history is published.

    python tools/demo_trades.py     writes docs/examples/trades_demo.html
"""
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "examples" / "trades_demo.html"
DATE = "2026-09-12"
PIP = 0.0001
USD_PER_PIP_PER_LOT = 10.0


def trade(n, ticket, side, entry, exit_, sl, tps, hits, status, t_in, t_out, vol):
    direction = 1 if side == "BUY" else -1
    pips = (float(exit_) - float(entry)) / PIP * direction if exit_ else 0.0
    usd = round(pips * float(vol) * USD_PER_PIP_PER_LOT, 2) if exit_ else ""
    return {
        "position_ticket": ticket, "order_id": ticket, "status": status,
        "time_entry": f"{DATE}T{t_in}", "time_exit": f"{DATE}T{t_out}" if exit_ else "",
        "duration_sec": "", "side": side, "volume": vol,
        "entry_price": entry, "current_price": exit_ or "1.16042", "exit_price": exit_ or "",
        "sl": sl, "tp1": tps[0], "tp2": tps[1], "tp3": tps[2],
        "sl_hit": str(hits[0]).lower(), "tp1_hit": str(hits[1]).lower(),
        "tp2_hit": str(hits[2]).lower(), "tp3_hit": str(hits[3]).lower(),
        "pnl_usd": f"{usd}" if exit_ else "", "pnl_eur": "",
        "pnl_pips": f"{pips:.1f}" if exit_ else "",
        "rr_ratio": "", "drawdown_pips": "", "equity_before": "", "equity_after": "",
        "commission_usd": "", "swap_usd": "", "symbol": "EURUSD", "session": "LONDON",
        "trend_context": "", "cycle_ts": "", "screenshots": "", "notes": "",
        "spread_pips": "0.2", "cycle_number": str(n), "decision": side,
    }


TRADES = [
    trade(1, "214880913", "BUY",  "1.15802", "1.15974", "1.15682", ["1.15902", "1.15962", "1.16022"], [0, 1, 1, 0], "CLOSED", "08:15:02", "08:41:17", "1.50"),
    trade(2, "214881402", "SELL", "1.15931", "1.15791", "1.16051", ["1.15851", "1.15791", "1.15731"], [0, 1, 1, 0], "CLOSED", "09:02:44", "09:28:03", "1.50"),
    trade(3, "214881988", "BUY",  "1.15866", "1.15746", "1.15746", ["1.15986", "1.16046", "1.16106"], [1, 0, 0, 0], "CLOSED", "10:10:31", "10:22:58", "1.50"),
    trade(4, "214882117", "BUY",  "1.15774", "1.16014", "1.15654", ["1.15894", "1.15954", "1.16014"], [0, 1, 1, 1], "CLOSED", "11:33:12", "12:15:40", "2.00"),
    trade(5, "214882640", "SELL", "1.16012", "1.15882", "1.16132", ["1.15932", "1.15882", "1.15832"], [0, 1, 1, 0], "CLOSED", "13:05:19", "13:30:55", "1.50"),
    trade(6, "214883204", "SELL", "1.15988", "1.16098", "1.16098", ["1.15868", "1.15808", "1.15748"], [1, 0, 0, 0], "CLOSED", "14:12:07", "14:26:31", "1.20"),
    trade(7, "214883755", "BUY",  "1.15902", "1.16082", "1.15782", ["1.16002", "1.16062", "1.16122"], [0, 1, 1, 0], "CLOSED", "15:40:22", "16:18:09", "2.00"),
    trade(8, "214884310", "SELL", "1.16089", "",        "1.16209", ["1.15989", "1.15929", "1.15869"], [0, 1, 0, 0], "OPEN",   "17:04:55", "",         "1.50"),
]

closed = [t for t in TRADES if t["status"] == "CLOSED"]
pnl = [float(t["pnl_usd"]) for t in closed]
wins = [p for p in pnl if p > 0]
losses = [p for p in pnl if p <= 0]
best = max(closed, key=lambda t: float(t["pnl_usd"]))
worst = min(closed, key=lambda t: float(t["pnl_usd"]))

stats = {
    "total_pnl_usd": round(sum(pnl), 2),
    "total_pnl_pct": round(sum(pnl) / 52480.0 * 100, 2),
    "win_rate": round(len(wins) / len(pnl) * 100, 1),
    "total_trades": len(pnl), "winning_trades": len(wins), "losing_trades": len(losses),
    "active_positions": 1, "active_buy": 0, "active_sell": 1,
    "best_trade_usd": float(best["pnl_usd"]), "best_trade_pips": float(best["pnl_pips"]),
    "worst_trade_usd": float(worst["pnl_usd"]), "worst_trade_pips": float(worst["pnl_pips"]),
    "total_volume": round(sum(float(t["volume"]) for t in TRADES), 2),
    "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else 0.0,
    "last_update": "12/09/26 - 17:35:10",
}

data = {DATE: {"dashboard_stats": stats, **{t["position_ticket"]: t for t in TRADES}}}

html = TEMPLATE.read_text(encoding="utf-8")
html = re.sub(r"const availableDates = \[.*?\];",
              'const availableDates = ["%s"];' % DATE, html)
html = re.sub(r"const inlineDataCache = \{.*?\};",
              "const inlineDataCache = " + json.dumps(data, ensure_ascii=False) + ";",
              html, flags=re.S)
html = html.replace('<meta http-equiv="refresh" content="15">', "")
TEMPLATE.write_text(html, encoding="utf-8")
print("written", TEMPLATE)
print("stats", stats)
