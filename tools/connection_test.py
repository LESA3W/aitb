# aitb/connection_test.py
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import MetaTrader5 as mt5
import time, csv, datetime, statistics

# === CONFIG ===
LOGIN = int(os.environ.get("MT5_LOGIN", "0"))
PASSWORD = os.environ.get("MT5_PASSWORD", "")
SERVER = "ICMarketsSC-Demo"
SYMBOL = "EURUSD"
VOLUME = 0.01
DEVIATION = 10
MAGIC = 12345
N_TRADES = 5  # nombre d'ordres tests à ouvrir


# === INITIALIZATION ===
print("Initializing connection...")

if not mt5.initialize(server=SERVER, login=LOGIN, password=PASSWORD):
    print("❌ Initialization failed:", mt5.last_error())
    quit()

info = mt5.account_info()
if info is None:
    print("❌ Unable to retrieve account info.")
    mt5.shutdown()
    quit()

print(f"✅ Connected to {info.server}")
print(f"Account: {info.name}")
print(f"Balance: {info.balance} USD | Leverage: {info.leverage}")
print(f"Trade allowed: {info.trade_allowed} | Trade mode: {info.trade_mode}\n")

# === SANITY CHECK ===
if not info.trade_allowed or info.trade_mode not in (2, 3):
    print("⚠️ Trading may be restricted or disabled (check server mode).")
else:
    print("✅ Trading permissions look good.\n")

# === SPREAD TEST ===
print("📏 Measuring spreads for 10 seconds...")
spreads = []
for _ in range(10):
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick:
        spread = (tick.ask - tick.bid) * 10000
        spreads.append(spread)
    time.sleep(1)

if spreads:
    print(f"Spread (pips): min={min(spreads):.2f}, max={max(spreads):.2f}, avg={statistics.mean(spreads):.2f}")
else:
    print("⚠️ No tick data received. Market might be closed.")


# === TRADE EXECUTION TEST ===
print("\n🚀 Testing order execution...")
results = []

for i in range(N_TRADES):
    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick:
        print("⚠️ No tick data available.")
        break

    price = tick.ask
    start_time = time.time()

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": VOLUME,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": f"AITB_test_{i+1}",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    # Tester les différents modes de filling
    success = False
    for fill_mode in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
        request["type_filling"] = fill_mode
        result = mt5.order_send(request)
        latency = (time.time() - start_time) * 1000  # ms

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            executed_price = result.price
            slippage = abs(executed_price - price) * 10000
            print(f"✅ Trade {i+1} executed | latency={latency:.1f} ms | slippage={slippage:.2f} pips | filling={fill_mode}")
            success = True

            results.append({
                "timestamp": datetime.datetime.now().isoformat(),
                "trade": i+1,
                "latency_ms": round(latency, 1),
                "slippage_pips": round(slippage, 2),
                "retcode": result.retcode,
                "executed_price": executed_price,
                "requested_price": price,
                "filling_mode": fill_mode
            })

            # Fermer le trade immédiatement
            close_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": SYMBOL,
                "volume": VOLUME,
                "type": mt5.ORDER_TYPE_SELL,
                "position": result.order,
                "price": mt5.symbol_info_tick(SYMBOL).bid,
                "deviation": DEVIATION,
                "magic": MAGIC,
                "comment": "AITB_close",
            }
            mt5.order_send(close_request)
            break

    if not success:
        print(f"❌ Trade {i+1} failed | retcode={result.retcode if result else 'None'} ({getattr(result, 'comment', '')}) | latency={latency:.1f} ms")

    time.sleep(2)


# === CHECK SYMBOL INFO ===
symbol_info = mt5.symbol_info(SYMBOL)
if symbol_info:
    print(f"\nPreferred filling mode: {symbol_info.filling_mode}")
else:
    print("\n⚠️ Unable to fetch symbol info (possibly after disconnection).")


# === SAVE REPORT ===
if results:
    filename = f"aitb_diagnostics_{datetime.date.today()}.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"\n📊 Report saved: {filename}")

    avg_latency = statistics.mean([r["latency_ms"] for r in results])
    avg_slip = statistics.mean([r["slippage_pips"] for r in results])
    print(f"\n📈 Average latency: {avg_latency:.1f} ms | Average slippage: {avg_slip:.3f} pips")

else:
    print("\n⚠️ No successful trades, report not saved.")


mt5.shutdown()
print("\n✅ Done.")
