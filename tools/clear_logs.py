#!/usr/bin/env python3
"""
clear_logs.py - Vide le fichier trade_logs de la date du jour
"""
import sys
import os
from pathlib import Path
from datetime import datetime

# Ajouter le chemin du projet
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import CONFIG

def clear_today_logs():
    """Vide le fichier trade_logs de la date du jour"""
    
    # Récupérer le dossier des rapports
    reports = Path(CONFIG.get("REPORTS_FOLDER", "logs"))
    
    # Obtenir la date du jour
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Chemin du fichier JSON du jour
    json_file = reports / f"trade_logs_{today}.json"
    
    # Structure JSON vide avec stats à zéro
    empty_data = {
        "dashboard_stats": {
            "total_pnl_usd": 0.0,
            "total_pnl_pct": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "active_positions": 0,
            "active_buy": 0,
            "active_sell": 0,
            "best_trade_usd": 0.0,
            "best_trade_pips": 0.0,
            "worst_trade_usd": 0.0,
            "worst_trade_pips": 0.0,
            "total_volume": 0.0,
            "profit_factor": 0.0,
            "last_update": datetime.now().strftime("%d/%m/%y - %H:%M:%S")
        }
    }
    
    # Écrire le fichier vide
    import json
    json_file.write_text(json.dumps(empty_data, indent=4, ensure_ascii=False) + "\n", encoding='utf-8')
    
    print(f"✅ {json_file.name} vidé (date: {today})")
    
    # Dupliquer aussi dans screenshots/EURUSD si nécessaire
    screenshots_folder = Path(CONFIG.get("SCREENSHOTS_FOLDER", "screenshots"))
    symbol = CONFIG.get("SYMBOL", "EURUSD")
    
    # Déterminer le bon chemin
    screenshots_folder_str = str(screenshots_folder).replace("\\", "/").upper()
    if screenshots_folder_str.endswith(f"/{symbol.upper()}") or screenshots_folder_str.endswith(symbol.upper()):
        symbol_screenshots = screenshots_folder
    else:
        symbol_screenshots = screenshots_folder / symbol
    
    # Dupliquer dans screenshots
    if symbol_screenshots.exists():
        screenshots_json = symbol_screenshots / f"trade_logs_{today}.json"
        screenshots_json.write_text(json.dumps(empty_data, indent=4, ensure_ascii=False) + "\n", encoding='utf-8')
        print(f"✅ {screenshots_json.name} vidé dans screenshots/{symbol}")
    
    # Régénérer le HTML
    from aitb.logger import _flush
    _flush()
    print("✅ trade_logs.html régénéré")

if __name__ == "__main__":
    clear_today_logs()