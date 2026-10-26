import MetaTrader5 as mt5
import time

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

SYMBOL = "EURUSD"
LOT = 0.01
TP_PIPS = 30      # large pour éviter tout refus
SL_PIPS = 20
DEVIATION = 50    # tolérance au mouvement de prix

def pips_to_price_multiplier(digits):
    # 5 ou 3 digits -> 1 pip = 10 * point ; sinon 1 pip = 1 * point
    return 10 if digits in (3, 5) else 1

if not mt5.initialize():
    print("❌ MT5 init failed"); quit()
print("✅ MT5 initialisé")

if not mt5.symbol_select(SYMBOL, True):
    print("❌ symbol_select failed"); mt5.shutdown(); quit()
print(f"✅ {SYMBOL} sélectionné")

info = mt5.symbol_info(SYMBOL)
tick = mt5.symbol_info_tick(SYMBOL)
point = info.point
digits = info.digits
pip_mult = pips_to_price_multiplier(digits)

price = tick.ask
tp = price + TP_PIPS * point * pip_mult
sl = price - SL_PIPS * point * pip_mult

# Arrondis au bon nombre de décimales
price = round(price, digits)
tp    = round(tp,    digits)
sl    = round(sl,    digits)

print(f"🎯 BUY @ {price} | TP {tp} (+{TP_PIPS} pips) | SL {sl} (-{SL_PIPS} pips) | stops_level={info.trade_stops_level}")

request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": SYMBOL,
    "volume": LOT,
    "type": mt5.ORDER_TYPE_BUY,
    "sl": sl,
    "tp": tp,
    "deviation": DEVIATION,
    "magic": 999001,
    "comment": "TEST_TP_SL",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_FOK,   # ICMarkets/MetaQuotes aiment souvent FOK
}

result = mt5.order_send(request)

if result.retcode != mt5.TRADE_RETCODE_DONE:
    print(f"❌ Retcode: {result.retcode} | comment: {getattr(result,'comment',None)}")
    print("📦 Result dict:", result._asdict())
else:
    print(f"✅ BUY exécuté ! ticket: {result.order}")

time.sleep(2)
mt5.shutdown()
