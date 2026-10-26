#!/usr/bin/env python3
"""
Test du système de logging A.I.T.B
Vérifie que les fichiers JSON sont bien créés et accessibles
"""

import sys
from pathlib import Path

# Ajouter le dossier parent au path
sys.path.insert(0, str(Path(__file__).parent))

from aitb.logger import logger
from config.settings import CONFIG

def test_logging_system():
    """Test complet du système de logging"""
    
    print("=" * 80)
    print("🧪 TEST DU SYSTÈME DE LOGGING A.I.T.B")
    print("=" * 80)
    print()
    
    # 1. Vérifier les chemins
    print("📁 Vérification des chemins:")
    print(f"   REPORTS: {logger.REPORTS}")
    print(f"   HTML: {logger.HTML_FILE}")
    print(f"   SCREENSHOTS: {logger.SYMBOL_SCREENSHOTS}")
    print()
    
    # 2. Vérifier que les dossiers existent
    print("📂 Vérification des dossiers:")
    logger._ensure_reports()
    print(f"   ✅ {logger.REPORTS} existe: {logger.REPORTS.exists()}")
    print(f"   ✅ {logger.SYMBOL_SCREENSHOTS} existe: {logger.SYMBOL_SCREENSHOTS.exists()}")
    print()
    
    # 3. Vérifier les dates disponibles
    print("📅 Dates disponibles:")
    dates = logger._get_all_json_dates()
    if dates:
        for date in dates:
            json_path = logger._get_json_path_for_date(date)
            screenshots_path = logger._get_screenshots_json_path_for_date(date)
            print(f"   📄 {date}:")
            print(f"      Logs: {json_path.exists()} - {json_path}")
            print(f"      Screenshots: {screenshots_path.exists()} - {screenshots_path}")
    else:
        print("   ⚠️ Aucune date trouvée")
    print()
    
    # 4. Charger les données du jour
    today = logger._get_today_date()
    print(f"📊 Données du jour ({today}):")
    rows = logger._load_rows_for_date(today)
    if rows:
        print(f"   ✅ {len(rows)} positions trouvées:")
        for ticket, row in rows.items():
            status = row.get('status', '?')
            side = row.get('side', '?')
            volume = row.get('volume', '?')
            entry = row.get('entry_price', '?')
            print(f"      #{ticket}: {status} {side} {volume} lots @ {entry}")
    else:
        print("   ⚠️ Aucune position trouvée")
    print()
    
    # 5. Vérifier le HTML
    print("🌐 Dashboard HTML:")
    if logger.HTML_FILE.exists():
        size_kb = logger.HTML_FILE.stat().st_size / 1024
        print(f"   ✅ Existe: {logger.HTML_FILE}")
        print(f"   📏 Taille: {size_kb:.1f} KB")
    else:
        print(f"   ❌ Introuvable: {logger.HTML_FILE}")
    print()
    
    # 6. Test de duplication
    print("🔄 Test de duplication:")
    if dates:
        test_date = dates[-1]  # Dernière date
        source = logger._get_json_path_for_date(test_date)
        destination = logger._get_screenshots_json_path_for_date(test_date)
        print(f"   📄 Source: {source.exists()} - {source}")
        print(f"   📄 Destination: {destination.exists()} - {destination}")
        
        if source.exists() and destination.exists():
            src_size = source.stat().st_size
            dst_size = destination.stat().st_size
            if src_size == dst_size:
                print(f"   ✅ Duplication OK ({src_size} bytes)")
            else:
                print(f"   ⚠️ Tailles différentes: {src_size} vs {dst_size}")
        elif not destination.exists():
            print("   ⚠️ Destination manquante - tentative de duplication...")
            logger._duplicate_json_to_screenshots(test_date)
    print()
    
    print("=" * 80)
    print("✅ TEST TERMINÉ")
    print("=" * 80)

if __name__ == "__main__":
    test_logging_system()