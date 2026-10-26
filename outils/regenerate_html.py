#!/usr/bin/env python3
"""
Script pour régénérer le fichier HTML du dashboard
Le HTML charge dynamiquement les différents fichiers JSON par jour
"""
import sys
import json
from pathlib import Path

# Ajouter le dossier parent au path pour importer les modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Chemins directs
REPORTS = Path("logs")
HTML_FILE = REPORTS / "trade_logs.html"

print("=" * 80)
print("🔄 RÉGÉNÉRATION DU DASHBOARD HTML")
print("=" * 80)

# Vérifier que le dossier logs existe
if not REPORTS.exists():
    print(f"\n⚠️  Le dossier {REPORTS} n'existe pas")
    print("💡 Création du dossier...")
    REPORTS.mkdir(parents=True, exist_ok=True)

# Compter les fichiers JSON disponibles
json_files = list(REPORTS.glob("trade_logs_*.json"))
num_json = len(json_files)

print(f"\n📊 Fichiers JSON trouvés : {num_json}")
if json_files:
    dates = []
    for f in json_files:
        try:
            date_str = f.stem.replace("trade_logs_", "")
            dates.append(date_str)
        except:
            pass
    
    if dates:
        dates.sort()
        print(f"📅 Dates disponibles :")
        for date in dates:
            print(f"   - {date}")

print("\n📝 Régénération du HTML...")

try:
    from aitb.logger import _write_html
    _write_html()
    
    print(f"\n✅ HTML régénéré avec succès : {HTML_FILE}")
    print(f"📂 Ouvre maintenant : {HTML_FILE.absolute()}")
    
    if num_json > 0:
        print(f"\n🔄 Le HTML chargera automatiquement les {num_json} fichiers JSON")
        print("◀️  Utilise les boutons ← et → pour naviguer entre les jours")
    else:
        print("\n⚠️  Aucun fichier JSON trouvé")
        print("💡 Lance 'python test_logger.py' pour générer des données de test")
    
    print("\n" + "=" * 80)
    
except Exception as e:
    print(f"\n❌ Erreur : {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)