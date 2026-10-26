# aitb/risk_manager.py
import MetaTrader5 as mt5
import math

from config.settings import CONFIG
from utils.time_logs import log
from utils.colors import colors

# Récupération des limites depuis config
MIN_LOT = float(CONFIG.get("MIN_LOT", 0.01))

# BUG CORRIGE LE 2026-09-11 : MAX_LOT etait une constante a 0.10, calibree pour
# le compte demo de 1000 USD. Le plafond est un garde-fou de MARGE, pas de
# risque : le risque est deja borne par le calcul de lot lui-meme. Fige a 0.10,
# il ecrasait silencieusement la taille des positions des qu'on changeait de
# compte. Sur un compte FTMO de 10 000 USD, une entree EURUSD demandant 0.263
# lot partait a 0.10, soit un risque reel de 0.38% au lieu de 1%. Le bot aurait
# eu l'air de ne pas marcher alors que c'est le plafond qui bridait tout.
#
# Le plafond est donc devenu proportionnel au capital, avec la meme valeur pour
# 1000 USD qu'avant pour ne rien changer au compte demo en cours.
MAX_LOT_PER_1K = float(CONFIG.get("MAX_LOT_PER_1K", 0.10))
MAX_LOT_ABS = float(CONFIG.get("MAX_LOT_ABS", 20.0))


def max_lot_for(capital: float) -> float:
    """Plafond de volume adapte au capital. Jamais en dessous de MIN_LOT."""
    if capital is None or capital <= 0:
        return MAX_LOT_PER_1K
    return max(MIN_LOT, min(MAX_LOT_PER_1K * capital / 1000.0, MAX_LOT_ABS))


# Conserve pour les appels qui n'ont pas de capital sous la main.
MAX_LOT = float(CONFIG.get("MAX_LOT", 0.5))
LOT_STEP = float(CONFIG.get("LOT_STEP", 0.01))


def calc_lot(capital: float, risk_pct: float, sl_pips: float, symbol="EURUSD") -> float:
    """
    Calcule automatiquement la taille du lot optimale selon:
    - Capital disponible
    - Risque en % (ex: 1% = 10 USD pour 1000 USD)
    - Stop Loss en pips
    
    Formule:
    Lot = (Capital × Risk%) / (SL_pips × Pip_value_per_lot)
    
    Exemple:
    calc_lot(1000, 1.0, 20, "EURUSD") → 0.05 lot
    - Risque: 10 USD (1% de 1000)
    - SL: 20 pips
    - 10 USD / (20 × 10) = 0.05 lot
    
    Args:
        capital: Balance du compte en USD
        risk_pct: Pourcentage du capital à risquer (1.0 = 1%)
        sl_pips: Stop Loss en pips
        symbol: Paire forex (défaut: EURUSD)
    
    Returns:
        float: Taille du lot arrondie aux contraintes broker
    """
    
    # ==================== VALIDATION ENTRÉES ====================
    if capital <= 0:
        log(colors(f"⚠️ Capital invalide: {capital}", "yellow"))
        return MIN_LOT
    
    from config.settings import CONFIG

    MAX_RISK_PCT = CONFIG.get("MAX_RISK_PCT", 5.0)  # Valeur par défaut = 5.0

    if risk_pct > MAX_RISK_PCT:
        return 0.01
    
    if sl_pips <= 0:
        log(colors(f"⚠️ SL invalide: {sl_pips} pips", "yellow"))
        return MIN_LOT
    
    # ==================== INFO SYMBOLE ====================
    info = mt5.symbol_info(symbol)
    if not info:
        log(colors(f"❌ Symbol {symbol} introuvable sur MT5", "red"))
        return MIN_LOT
    
    # Sélectionner le symbole si pas visible
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            log(colors(f"❌ Impossible de sélectionner {symbol}", "red"))
            return MIN_LOT
        info = mt5.symbol_info(symbol)
    
    # ==================== CALCUL PIP VALUE ====================
    # Pour les paires XXX/USD (quote currency = USD):
    # - 1 lot standard = 100,000 unités
    # - 1 pip = 0.0001 (sur broker 5 digits)
    # - Pip value = 0.0001 × 100,000 = 10 USD par lot
    
    # Valeur d'un pip pour 1 lot, formule generique valable sur TOUS les
    # instruments : forex 5 digits, paires JPY 3 digits, metaux, indices.
    #
    # BUG CORRIGE LE 2026-09-10 : l'ancienne version codait "1 pip = 0.0001" en
    # dur. Sur les paires JPY, dont le point vaut 0.001, un pip vaut 0.01 et non
    # 0.0001, ce qui sous-estimait la valeur du pip d'un facteur 100. Le lot
    # calcule etait donc 100 fois trop gros, et seul le plafond MAX_LOT evitait
    # la catastrophe. Detecte en forward test sur USDJPY et EURJPY.
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or info.point)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    pip = info.point * 10.0

    if tick_size > 0 and tick_value > 0:
        pip_value_per_lot = (pip / tick_size) * tick_value
    elif symbol.endswith("USD"):
        pip_value_per_lot = 10.0
    else:
        log(colors(f"⚠️ Valeur du pip indisponible pour {symbol}, trade refusé", "yellow"))
        return 0.0

    if pip_value_per_lot <= 0:
        log(colors(f"⚠️ Valeur du pip nulle pour {symbol}, trade refusé", "yellow"))
        return 0.0
    
    # ==================== CALCUL LOT ====================
    risk_amount = capital * (risk_pct / 100.0)
    
    # Lot = Risque / (SL × PipValue)
    lot_raw = risk_amount / (sl_pips * pip_value_per_lot)
    
    # ==================== ARRONDI AUX CONTRAINTES BROKER ====================
    # Récupérer les limites du broker
    broker_min = getattr(info, "volume_min", MIN_LOT)
    broker_max = getattr(info, "volume_max", MAX_LOT)
    broker_step = getattr(info, "volume_step", LOT_STEP)
    
    # Utiliser les limites les plus restrictives (config vs broker)
    final_min = max(MIN_LOT, broker_min)
    final_max = min(max_lot_for(capital), broker_max)
    final_step = max(LOT_STEP, broker_step)
    
    # Arrondir au step inférieur (conservateur)
    lot_rounded = math.floor(lot_raw / final_step) * final_step
    
    # Appliquer les bornes
    lot_final = max(final_min, min(lot_rounded, final_max))
    
    # Arrondir à 2 décimales pour éviter erreurs float
    lot_final = round(lot_final, 2)
    
    # ==================== LOGS DEBUG ====================
    # ETIQUETTE CORRIGEE LE 2026-09-14 : "USD" etait code en dur alors que
    # trade_tick_value est renvoye par MT5 dans la DEVISE DU DEPOT. Sur le
    # compte FTMO en EUR, les logs annoncaient donc des USD pour des montants
    # en EUR. Le calcul etait juste, seule l'etiquette mentait, ce qui a coute
    # une fausse alerte de devise pendant la bascule.
    log(colors(f"💰 Calcul lot: Capital={capital:.2f} | Risque={risk_pct}% ({risk_amount:.2f}) | SL={sl_pips} pips", "cyan"))
    log(colors(f"📊 Pip value={pip_value_per_lot:.2f}/lot (devise du compte) | Lot brut={lot_raw:.4f} → Lot final={lot_final:.2f}", "cyan"))
    
    # Vérification sécurité
    if lot_final < final_min:
        log(colors(f"⚠️ Lot {lot_final} < min {final_min} → forcé à {final_min}", "yellow"))
        return final_min
    
    if lot_final > final_max:
        log(colors(f"⚠️ Lot {lot_final} > max {final_max} → forcé à {final_max}", "yellow"))
        return final_max
    
    return lot_final


# ==================== FONCTION UTILITAIRE ====================
def get_risk_in_usd(lot: float, sl_pips: float, symbol="EURUSD") -> float:
    """
    Calcule le risque en USD pour un lot et SL donnés.
    Utile pour vérifier avant d'envoyer l'ordre.
    
    Args:
        lot: Taille du lot
        sl_pips: Stop Loss en pips
        symbol: Paire forex
    
    Returns:
        float: Risque en USD
    """
    info = mt5.symbol_info(symbol)
    if not info:
        return 0.0
    
    # Pip value
    if symbol.endswith("USD"):
        pip_value_per_lot = 10.0
    else:
        tick_value = getattr(info, "trade_tick_value", 10.0)
        tick_size = getattr(info, "trade_tick_size", 0.00001)
        pip_in_ticks = 0.0001 / tick_size if tick_size > 0 else 1
        pip_value_per_lot = tick_value * pip_in_ticks
    
    risk_usd = lot * sl_pips * pip_value_per_lot
    return round(risk_usd, 2)


def validate_lot_size(lot: float, symbol="EURUSD") -> bool:
    """
    Vérifie si une taille de lot est valide selon les contraintes broker.
    
    Args:
        lot: Taille du lot à vérifier
        symbol: Paire forex
    
    Returns:
        bool: True si valide
    """
    info = mt5.symbol_info(symbol)
    if not info:
        return False
    
    min_lot = max(MIN_LOT, getattr(info, "volume_min", 0.01))
    max_lot = min(MAX_LOT, getattr(info, "volume_max", 100.0))
    
    if lot < min_lot:
        log(colors(f"❌ Lot {lot} < minimum {min_lot}", "red"))
        return False
    
    if lot > max_lot:
        log(colors(f"❌ Lot {lot} > maximum {max_lot}", "red"))
        return False
    
    return True


# ==================== TEST UNITAIRE ====================
if __name__ == "__main__":
    """
    Test du risk manager avec différents scénarios.
    Lance avec: python -m aitb.risk_manager
    """
    print("\n" + "="*60)
    print("🧪 TEST RISK MANAGER - Compte 1000 USD")
    print("="*60 + "\n")
    
    # Init MT5
    if not mt5.initialize():
        print("❌ MT5 init failed")
        exit(1)
    
    # Scénarios de test
    scenarios = [
        {"capital": 1000, "risk_pct": 1.0, "sl_pips": 20, "desc": "Standard"},
        {"capital": 1000, "risk_pct": 1.0, "sl_pips": 10, "desc": "SL serré"},
        {"capital": 1000, "risk_pct": 1.0, "sl_pips": 30, "desc": "SL large"},
        {"capital": 990, "risk_pct": 1.0, "sl_pips": 20, "desc": "Après perte"},
        {"capital": 1020, "risk_pct": 1.0, "sl_pips": 20, "desc": "Après gain"},
    ]
    
    for s in scenarios:
        print(f"\n📋 Scénario: {s['desc']}")
        lot = calc_lot(s["capital"], s["risk_pct"], s["sl_pips"])
        risk = get_risk_in_usd(lot, s["sl_pips"])
        print(f"   → Lot calculé: {lot}")
        print(f"   → Risque réel: {risk:.2f} USD ({(risk/s['capital']*100):.2f}%)")
        print(f"   → Valide: {validate_lot_size(lot)}")
    
    mt5.shutdown()
    print("\n" + "="*60 + "\n")