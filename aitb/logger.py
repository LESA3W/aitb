# haitb/logger.py — UN HTML + JSON par jour + logs.txt + DUPLICATION dans screenshots/EURUSD/
import os, json, html, shutil
from pathlib import Path
from datetime import datetime, timedelta
from config.settings import CONFIG

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

# ---- Paths / config ----
REPORTS    = Path(CONFIG.get("REPORTS_FOLDER", "logs"))
HTML_FILE  = REPORTS / "trade_logs.html"  # ✅ UN SEUL fichier HTML
LOGS_TXT   = REPORTS / "console_logs.txt"
SYMBOL     = CONFIG.get("SYMBOL", "EURUSD")
AUTO_REFRESH = int(CONFIG.get("HTML_AUTO_REFRESH_SECONDS", 5))

# ✅ Chemin pour la duplication dans screenshots
SCREENSHOTS_FOLDER = Path(CONFIG.get("SCREENSHOTS_FOLDER", "screenshots"))

# Déterminer le chemin final pour les JSON
# Si le chemin se termine déjà par le symbole (EURUSD), on l'utilise tel quel
# Sinon, on ajoute le symbole
screenshots_folder_str = str(SCREENSHOTS_FOLDER).replace("\\", "/").upper()
if screenshots_folder_str.endswith(f"/{SYMBOL.upper()}") or screenshots_folder_str.endswith(SYMBOL.upper()):
    # Le chemin contient déjà EURUSD (ex: screenshots/EURUSD)
    SYMBOL_SCREENSHOTS = SCREENSHOTS_FOLDER
else:
    # Ajouter le symbole (ex: screenshots -> screenshots/EURUSD)
    SYMBOL_SCREENSHOTS = SCREENSHOTS_FOLDER / SYMBOL

FIELDS = [
    "position_ticket","order_id","status",
    "time_entry","time_exit","duration_sec",
    "side","volume",
    "entry_price","current_price","exit_price",
    "sl","tp1","tp2","tp3",
    "sl_hit","tp1_hit","tp2_hit","tp3_hit",
    "pnl_usd","pnl_eur","pnl_pips",
    "rr_ratio","drawdown_pips",
    "equity_before","equity_after",
    "commission_usd","swap_usd",
    "symbol","session","trend_context",
    "cycle_ts","screenshots","spread_pips","notes",
    "cycle_number","decision",
]

def _ensure_reports():
    REPORTS.mkdir(parents=True, exist_ok=True)
    # ✅ Créer aussi le dossier screenshots/EURUSD/ s'il n'existe pas
    SYMBOL_SCREENSHOTS.mkdir(parents=True, exist_ok=True)

def _now():
    return datetime.now().isoformat(timespec="seconds")

def _get_today_date():
    """Retourne la date du jour au format YYYY-MM-DD"""
    return datetime.now().strftime("%Y-%m-%d")

def _get_json_path_for_date(date_str: str = None):
    """Retourne le chemin JSON pour une date donnée"""
    if date_str is None:
        date_str = _get_today_date()
    return REPORTS / f"trade_logs_{date_str}.json"

def _get_screenshots_json_path_for_date(date_str: str = None):
    """Retourne le chemin JSON dans screenshots/EURUSD/ pour une date donnée"""
    if date_str is None:
        date_str = _get_today_date()
    return SYMBOL_SCREENSHOTS / f"trade_logs_{date_str}.json"

def _get_all_json_dates():
    """Retourne la liste triée de toutes les dates disponibles"""
    json_files = list(REPORTS.glob("trade_logs_*.json"))
    dates = []
    for f in json_files:
        try:
            # Extraire la date du nom de fichier: trade_logs_2025-11-14.json
            date_str = f.stem.replace("trade_logs_", "")
            dates.append(date_str)
        except:
            pass
    return sorted(dates)

# ---------- LOGS.TXT ----------
def init_logs_txt():
    _ensure_reports()
    try:
        if LOGS_TXT.exists():
            LOGS_TXT.unlink()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = ["=" * 80, f"AITB — Console Logs (démarrage: {timestamp})", "=" * 80, ""]
        LOGS_TXT.write_text("\n".join(header), encoding="utf-8")
        print(f"✅ Console logs initialisés: {LOGS_TXT}")
    except Exception as e:
        print(f"⚠️ Erreur init console_logs.txt: {e}")

def append_to_logs_txt(message: str):
    try:
        _ensure_reports()
        with open(LOGS_TXT, 'a', encoding='utf-8') as f:
            f.write(message + "\n")
    except Exception:
        pass

# ---------- DASHBOARD STATS ----------
def _calculate_dashboard_stats(rows: dict) -> dict:
    total_pnl = 0.0
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    active_positions = 0
    active_buy = 0
    active_sell = 0
    best_trade_usd = 0.0
    best_trade_pips = 0.0
    worst_trade_usd = 0.0
    worst_trade_pips = 0.0
    total_volume = 0.0
    
    for row in rows.values():
        status = str(row.get("status", "")).upper()
        pnl_usd = float(row.get("pnl_usd", 0) or 0)
        pnl_pips = float(row.get("pnl_pips", 0) or 0)
        volume = float(row.get("volume", 0) or 0)
        side = str(row.get("side", "")).upper()
        
        if status == "OPEN":
            active_positions += 1
            if side == "BUY":
                active_buy += 1
            else:
                active_sell += 1
        
        if status == "CLOSED":
            total_trades += 1
            total_pnl += pnl_usd
            total_volume += volume
            
            if pnl_usd > 0:
                winning_trades += 1
            elif pnl_usd < 0:
                losing_trades += 1
            
            if pnl_usd > best_trade_usd:
                best_trade_usd = pnl_usd
                best_trade_pips = pnl_pips
            
            if pnl_usd < worst_trade_usd:
                worst_trade_usd = pnl_usd
                worst_trade_pips = pnl_pips
    
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    total_wins = sum(float(r.get("pnl_usd", 0) or 0) for r in rows.values() 
                     if r.get("status") == "CLOSED" and float(r.get("pnl_usd", 0) or 0) > 0)
    total_losses = abs(sum(float(r.get("pnl_usd", 0) or 0) for r in rows.values() 
                          if r.get("status") == "CLOSED" and float(r.get("pnl_usd", 0) or 0) < 0))
    profit_factor = (total_wins / total_losses) if total_losses > 0 else 0.0
    
    return {
        "total_pnl_usd": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / 1000) * 100, 1) if total_pnl != 0 else 0.0,
        "win_rate": round(win_rate, 1),
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "active_positions": active_positions,
        "active_buy": active_buy,
        "active_sell": active_sell,
        "best_trade_usd": round(best_trade_usd, 2),
        "best_trade_pips": round(best_trade_pips, 1),
        "worst_trade_usd": round(worst_trade_usd, 2),
        "worst_trade_pips": round(worst_trade_pips, 1),
        "total_volume": round(total_volume, 2),
        "profit_factor": round(profit_factor, 2),
        "last_update": datetime.now().strftime("%d/%m/%y - %H:%M:%S")
    }

# ---------- JSON ----------
def _load_rows_for_date(date_str: str) -> dict:
    """Charge les trades pour une date spécifique"""
    _ensure_reports()
    json_path = _get_json_path_for_date(date_str)
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if k != "dashboard_stats"}
        except Exception:
            pass
    return {}

def _duplicate_json_to_screenshots(date_str: str):
    """
    ✅ Duplique le fichier JSON dans screenshots/EURUSD/ (silencieux)
    """
    try:
        source = _get_json_path_for_date(date_str)
        destination = _get_screenshots_json_path_for_date(date_str)
        
        if source.exists():
            shutil.copy2(source, destination)
            # Pas de print ici, on le fera une seule fois dans _flush()
    except Exception:
        pass  # Silencieux en cas d'erreur

def _save_rows_for_date(date_str: str, rows: dict):
    """Sauvegarde les trades pour une date spécifique + duplication"""
    _ensure_reports()
    json_path = _get_json_path_for_date(date_str)
    
    clean = {}
    for tk, row in rows.items():
        r = {k: row.get(k, "") for k in FIELDS}
        r["position_ticket"] = tk
        clean[tk] = r
    
    stats = _calculate_dashboard_stats(clean)
    
    json_data = {
        "dashboard_stats": stats,
        **clean
    }
    
    json_str = json.dumps(json_data, ensure_ascii=False, indent=4, sort_keys=False)
    json_path.write_text(json_str + "\n", encoding="utf-8")
    
    # ✅ Dupliquer dans screenshots/EURUSD/
    _duplicate_json_to_screenshots(date_str)

def _load_all_rows() -> dict:
    """Charge TOUS les trades de TOUS les jours"""
    all_rows = {}
    for date_str in _get_all_json_dates():
        rows = _load_rows_for_date(date_str)
        all_rows.update(rows)
    return all_rows

def _save_row(ticket: str, row: dict):
    """Sauvegarde un trade dans le JSON de son jour"""
    # Extraire la date du trade
    entry_time = row.get("time_entry", "")
    if entry_time:
        try:
            date_obj = datetime.fromisoformat(entry_time)
            date_str = date_obj.strftime("%Y-%m-%d")
        except:
            date_str = _get_today_date()
    else:
        date_str = _get_today_date()
    
    # Charger les trades de ce jour
    day_rows = _load_rows_for_date(date_str)
    day_rows[ticket] = row
    
    # Sauvegarder (avec duplication automatique)
    _save_rows_for_date(date_str, day_rows)

# ---------- Utilities ----------
def _pip(info):
    return 10 * info.point

def _pips_between(info, a, b):
    return (a - b) / _pip(info)

def _parse_ts(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z","+00:00"))
    except Exception:
        return datetime.min

# ---------- HTML ----------
def _write_html():
    """
    ✅ Génère UN SEUL fichier HTML qui charge dynamiquement les JSON par jour
    """
    _ensure_reports()
    
    # Obtenir toutes les dates disponibles
    all_dates = _get_all_json_dates()
    dates_json = json.dumps(all_dates)
    today = _get_today_date()
    
    # ✅ Charger les données du jour actuel pour les inclure directement
    inline_data = {}
    if all_dates:
        latest_date = all_dates[-1]
        json_path = _get_json_path_for_date(latest_date)
        if json_path.exists():
            try:
                inline_data[latest_date] = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{AUTO_REFRESH}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A.I.T.B | Trading Dashboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@700;800&family=JetBrains+Mono:wght@400;600&display=swap');

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

:root {{
  --bg-primary: #0a0d14;
  --bg-secondary: #0f1419;
  --bg-card: #151b26;
  --bg-hover: #1a2332;
  --border: #1f2937;
  --border-light: #374151;
  --text-primary: #f9fafb;
  --text-secondary: #9ca3af;
  --text-muted: #6b7280;
  --green: #10b981;
  --green-bg: rgba(16, 185, 129, 0.1);
  --red: #ef4444;
  --red-bg: rgba(239, 68, 68, 0.1);
  --blue: #3b82f6;
  --blue-bg: rgba(59, 130, 246, 0.1);
  --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.3);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.5);
}}

body {{
  background: var(--bg-primary);
  color: var(--text-primary);
  font-family: 'Montserrat', sans-serif;
  font-weight: 700;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  padding-bottom: 80px;
}}

.container {{
  max-width: 1800px;
  margin: 0 auto;
  padding: 24px;
}}

.header {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 32px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}}

.brand {{
  display: flex;
  align-items: center;
  gap: 12px;
  flex-direction: column;
  align-items: flex-start;
}}

.brand h1 {{
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.5px;
  background: linear-gradient(135deg, var(--blue) 0%, #60a5fa 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}

.brand .date-badge {{
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  padding: 4px 12px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
}}

.header-meta {{
  display: flex;
  align-items: center;
  gap: 24px;
}}

.status-indicator {{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
}}

.pulse {{
  width: 8px;
  height: 8px;
  background: var(--green);
  border-radius: 50%;
  animation: pulse 2s infinite;
}}

@keyframes pulse {{
  0%, 100% {{ opacity: 1; }}
  50% {{ opacity: 0.5; }}
}}

.last-update {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-muted);
}}

.nav-container {{
  display: flex;
  justify-content: center;
  gap: 16px;
  margin-bottom: 24px;
}}

.nav-btn {{
  padding: 10px 20px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-primary);
  text-decoration: none;
  font-size: 13px;
  font-weight: 600;
  transition: all 0.3s ease;
  cursor: pointer;
}}

.nav-btn:hover {{
  background: var(--bg-hover);
  border-color: var(--blue);
  transform: translateY(-2px);
}}

.nav-btn:disabled {{
  opacity: 0.3;
  cursor: not-allowed;
}}

.stats-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}}

.stat-card {{
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  transition: all 0.3s ease;
}}

.stat-card:hover {{
  border-color: var(--border-light);
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}}

.stat-label {{
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  margin-bottom: 8px;
}}

.stat-value {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 28px;
  font-weight: 600;
  line-height: 1.2;
}}

.stat-change {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  margin-top: 4px;
}}

.positive {{ color: var(--green); }}
.negative {{ color: var(--red); }}

.table-container {{
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  position: relative;
}}

.table-wrapper {{
  overflow-y: auto;
  max-height: 600px;
}}

.horizontal-scroll {{
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 60px;
  background: var(--bg-secondary);
  border-top: 2px solid var(--border);
  overflow-x: auto;
  overflow-y: hidden;
  z-index: 1000;
  padding: 0 20px;
}}

table {{
  width: 100%;
  border-collapse: collapse;
  min-width: 2000px;
}}

thead {{
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 10;
}}

th {{
  padding: 16px 12px;
  text-align: left;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-secondary);
  white-space: nowrap;
}}

th.tp-col, td.tp-col {{ width: 50px; min-width: 50px; max-width: 50px; }}
th.sl-col, td.sl-col {{ width: 50px; min-width: 50px; max-width: 50px; }}

tbody tr {{
  border-bottom: 1px solid var(--border);
  transition: background 0.2s;
}}

tbody tr:hover {{
  background: var(--bg-hover);
}}

td {{
  padding: 16px 12px;
  font-size: 13px;
}}

.mono {{
  font-family: 'JetBrains Mono', monospace;
  font-weight: 400;
}}

.badge {{
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.3px;
}}

.badge-cycle {{
  background: var(--blue-bg);
  color: var(--blue);
  border: 1px solid rgba(59, 130, 246, 0.2);
}}

.badge-decision {{
  background: rgba(156, 163, 175, 0.1);
  color: var(--text-primary);
  border: 1px solid rgba(156, 163, 175, 0.2);
}}

.badge-ticket {{
  background: var(--bg-secondary);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}}

.status-badge {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
}}

.status-open {{
  background: var(--green-bg);
  color: var(--green);
  border: 1px solid rgba(16, 185, 129, 0.2);
}}

.status-closed {{
  background: var(--red-bg);
  color: var(--red);
  border: 1px solid rgba(239, 68, 68, 0.2);
}}

.status-dot {{
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}}

.side-buy {{ color: var(--green); font-weight: 700; }}
.side-sell {{ color: var(--red); font-weight: 700; }}

.pnl-positive {{ color: var(--green); font-weight: 700; }}
.pnl-negative {{ color: var(--red); font-weight: 700; }}

.sl-value, .tp-value {{ color: var(--text-primary); font-weight: 700; }}
.sl-hit {{ color: var(--red) !important; font-weight: 700; }}
.tp-hit {{ color: var(--blue) !important; font-weight: 700; }}

.time-cell {{ color: var(--text-muted); font-size: 11px; }}

.screenshots {{
  color: var(--text-muted);
  font-size: 10px;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="brand">
      <h1>A.I.T.B Trading Dashboard</h1>
      <div class="date-badge" id="currentDate">📅 Loading...</div>
    </div>
    <div class="header-meta">
      <div class="status-indicator">
        <div class="pulse"></div>
        <span style="font-size: 13px;">Live</span>
      </div>
      <div class="last-update" id="lastUpdate">Last Update: --</div>
    </div>
  </div>

  <div class="nav-container">
    <button class="nav-btn" id="prevBtn" onclick="navigateDay(-1)">← </button>
    <div class="date-badge" id="navDate" style="font-size: 14px; padding: 8px 20px;">📅 Loading...</div>
    <button class="nav-btn" id="nextBtn" onclick="navigateDay(1)"> →</button>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">Total P&L</div>
      <div class="stat-value" id="totalPnl">$0.00</div>
      <div class="stat-change" id="totalPnlPct">↑ 0.0%</div>
    </div>
    
    <div class="stat-card">
      <div class="stat-label">Win Rate (PERF)</div>
      <div class="stat-value positive" id="winRate">0.0%</div>
      <div class="stat-change positive" id="winStats">↑ 0/0 Trades</div>
    </div>
    
    <div class="stat-card">
      <div class="stat-label">Active Positions</div>
      <div class="stat-value" style="color: var(--blue);" id="activePos">0</div>
      <div class="stat-change" style="color: var(--text-muted);" id="activeDetail">0 BUY · 0 SELL</div>
    </div>
    
    <div class="stat-card">
      <div class="stat-label">Best Trade</div>
      <div class="stat-value positive" id="bestTrade">+$0.00</div>
      <div class="stat-change" style="color: var(--text-muted);" id="bestPips">+0.0 pips</div>
    </div>
  </div>

  <div class="table-container">
    <div class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Cycle</th><th>Ticket</th><th>Decision</th><th>Status</th><th>Symbol</th><th>Side</th><th>Vol</th>
            <th>Entry</th><th>Current</th><th>Exit</th><th>P&L $</th><th>P&L pips</th>
            <th>Entry Time</th><th>Exit Time</th>
            <th class="sl-col">SL</th><th class="tp-col">TP1</th><th class="tp-col">TP2</th><th class="tp-col">TP3</th>
            <th>Screenshots</th>
          </tr>
        </thead>
        <tbody id="tradesTable">
          <tr><td colspan="19" style="text-align: center; padding: 40px;">Loading trades...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="horizontal-scroll">
</div>

<script>
// ✅ Configuration
const availableDates = {dates_json};
let currentDateIndex = availableDates.length - 1; // Dernier jour par défaut

// ✅ Cache des données inline (pour éviter CORS sur file://)
const inlineDataCache = {json.dumps(inline_data, ensure_ascii=False)};

// ✅ Charger les trades pour une date
async function loadTradesForDate(dateStr) {{
  // D'abord, vérifier si on a les données en cache inline
  if (inlineDataCache[dateStr]) {{
    console.log(`✅ Données chargées depuis le cache inline pour ${{dateStr}}`);
    return inlineDataCache[dateStr];
  }}
  
  try {{
    // Essayer d'abord le chemin relatif (dans le même dossier que le HTML)
    let response = await fetch(`trade_logs_${{dateStr}}.json`);
    
    // Si échec, essayer avec le chemin absolu depuis la racine
    if (!response.ok) {{
      response = await fetch(`logs/trade_logs_${{dateStr}}.json`);
    }}
    
    if (!response.ok) {{
      console.error(`Fichier trade_logs_${{dateStr}}.json introuvable`);
      return null;
    }}
    
    const data = await response.json();
    console.log(`✅ Données chargées depuis le fichier pour ${{dateStr}}:`, data);
    
    // Mettre en cache
    inlineDataCache[dateStr] = data;
    return data;
  }} catch (error) {{
    console.error('Erreur chargement JSON:', error);
    console.warn('💡 Astuce: Si vous ouvrez le HTML avec file://, lancez plutôt un serveur HTTP local');
    console.warn('💡 Commande: cd logs && python -m http.server 8000');
    return null;
  }}
}}

// ✅ Mettre à jour le dashboard
function updateDashboard(data) {{
  console.log('📊 Mise à jour dashboard avec:', data);
  
  if (!data) {{
    console.warn('⚠️ Aucune donnée reçue');
    document.getElementById('tradesTable').innerHTML = '<tr><td colspan="19" style="text-align: center; padding: 40px;">No data available</td></tr>';
    return;
  }}

  const stats = data.dashboard_stats || {{}};
  const trades = Object.values(data).filter(item => typeof item === 'object' && item.position_ticket);
  
  console.log(`📈 Stats trouvées:`, stats);
  console.log(`📝 Nombre de trades: ${{trades.length}}`);

  // ✅ Mise à jour des stats
  const totalPnl = stats.total_pnl_usd || 0;
  document.getElementById('totalPnl').textContent = `${{totalPnl >= 0 ? '+' : ''}}${{totalPnl.toFixed(2)}}`;
  document.getElementById('totalPnl').className = `stat-value ${{totalPnl >= 0 ? 'positive' : 'negative'}}`;
  document.getElementById('totalPnlPct').textContent = `↑ ${{Math.abs(stats.total_pnl_pct || 0).toFixed(1)}}%`;
  document.getElementById('totalPnlPct').className = `stat-change ${{totalPnl >= 0 ? 'positive' : 'negative'}}`;
  
  document.getElementById('winRate').textContent = `${{(stats.win_rate || 0).toFixed(1)}}%`;
  document.getElementById('winStats').textContent = `↑ ${{stats.winning_trades || 0}}/${{stats.total_trades || 0}} Trades`;
  
  document.getElementById('activePos').textContent = stats.active_positions || 0;
  document.getElementById('activeDetail').textContent = `${{stats.active_buy || 0}} BUY · ${{stats.active_sell || 0}} SELL`;
  
  document.getElementById('bestTrade').textContent = `+$${{(stats.best_trade_usd || 0).toFixed(2)}}`;
  document.getElementById('bestPips').textContent = `+${{(stats.best_trade_pips || 0).toFixed(1)}} pips`;
  
  document.getElementById('lastUpdate').textContent = `Last Update: ${{stats.last_update || 'N/A'}}`;

  // ✅ Mise à jour du tableau
  const tbody = document.getElementById('tradesTable');
  if (trades.length === 0) {{
    console.warn('⚠️ Aucun trade trouvé dans les données');
    tbody.innerHTML = '<tr><td colspan="19" style="text-align: center; padding: 40px;">No trades for this day</td></tr>';
    return;
  }}

  console.log(`✅ Affichage de ${{trades.length}} trades`);

  // Trier : OPEN en premier, puis par date
  trades.sort((a, b) => {{
    if (a.status === 'OPEN' && b.status !== 'OPEN') return -1;
    if (a.status !== 'OPEN' && b.status === 'OPEN') return 1;
    return new Date(b.time_entry || 0) - new Date(a.time_entry || 0);
  }});

  tbody.innerHTML = trades.map(trade => {{
    const slHit = trade.sl_hit === 'true';
    const tp1Hit = trade.tp1_hit === 'true';
    const tp2Hit = trade.tp2_hit === 'true';
    const tp3Hit = trade.tp3_hit === 'true';
    
    const pnlUsd = parseFloat(trade.pnl_usd || 0);
    const pnlPips = parseFloat(trade.pnl_pips || 0);
    
    const statusLower = (trade.status || '').toLowerCase();
    const sideLower = (trade.side || '').toLowerCase();
    
    return `<tr>
      <td><span class="badge badge-cycle">#${{trade.cycle_number || '?'}}</span></td>
      <td><span class="badge badge-ticket mono">${{trade.position_ticket}}</span></td>
      <td><span class="badge badge-decision">${{trade.decision || '-'}}</span></td>
      <td><span class="status-badge status-${{statusLower}}"><span class="status-dot"></span>${{trade.status}}</span></td>
      <td class="mono">${{trade.symbol}}</td>
      <td class="side-${{sideLower}}">${{trade.side}}</td>
      <td class="mono">${{trade.volume}}</td>
      <td class="mono">${{trade.entry_price}}</td>
      <td class="mono">${{trade.current_price}}</td>
      <td class="mono">${{trade.exit_price || '—'}}</td>
      <td class="pnl-${{pnlUsd >= 0 ? 'positive' : 'negative'}} mono">${{trade.pnl_usd}}</td>
      <td class="pnl-${{pnlPips >= 0 ? 'positive' : 'negative'}} mono">${{trade.pnl_pips}}</td>
      <td class="time-cell mono">${{trade.time_entry}}</td>
      <td class="time-cell mono">${{trade.time_exit || '—'}}</td>
      <td class="sl-col ${{slHit ? 'sl-hit' : 'sl-value'}} mono">${{trade.sl}}</td>
      <td class="tp-col ${{tp1Hit ? 'tp-hit' : 'tp-value'}} mono">${{trade.tp1}}</td>
      <td class="tp-col ${{tp2Hit ? 'tp-hit' : 'tp-value'}} mono">${{trade.tp2}}</td>
      <td class="tp-col ${{tp3Hit ? 'tp-hit' : 'tp-value'}} mono">${{trade.tp3}}</td>
      <td class="screenshots">${{trade.screenshots || ''}}</td>
    </tr>`;
  }}).join('');
}}

// ✅ Navigation entre les jours
async function navigateDay(direction) {{
  currentDateIndex += direction;
  if (currentDateIndex < 0) currentDateIndex = 0;
  if (currentDateIndex >= availableDates.length) currentDateIndex = availableDates.length - 1;
  
  await loadCurrentDay();
  updateNavButtons();
}}

// ✅ Charger le jour actuel
async function loadCurrentDay() {{
  const dateStr = availableDates[currentDateIndex];
  
  // ✅ Formater la date pour l'affichage
  const dateObj = new Date(dateStr + 'T00:00:00');
  const options = {{ year: 'numeric', month: 'long', day: 'numeric' }};
  const formattedDate = dateObj.toLocaleDateString('en-US', options);
  
  document.getElementById('currentDate').textContent = `📅 ${{formattedDate}}`;
  document.getElementById('navDate').textContent = `📅 ${{dateStr}}`;
  
  const data = await loadTradesForDate(dateStr);
  updateDashboard(data);
}}

// ✅ Mettre à jour les boutons de navigation
function updateNavButtons() {{
  document.getElementById('prevBtn').disabled = currentDateIndex === 0;
  document.getElementById('nextBtn').disabled = currentDateIndex === availableDates.length - 1;
}}

// ✅ Synchronisation du scroll
window.addEventListener('DOMContentLoaded', function() {{
  console.log('🚀 Dashboard chargé, initialisation...');
  console.log('📅 Dates disponibles:', availableDates);
  
  const tableWrapper = document.querySelector('.table-wrapper');
  const horizontalScroll = document.querySelector('.horizontal-scroll');
  
  if (tableWrapper && horizontalScroll) {{
    tableWrapper.addEventListener('scroll', function() {{
      horizontalScroll.scrollLeft = tableWrapper.scrollLeft;
    }});
    
    horizontalScroll.addEventListener('scroll', function() {{
      tableWrapper.scrollLeft = horizontalScroll.scrollLeft;
    }});
  }}
  
  // Vérifier qu'il y a des dates disponibles
  if (availableDates.length === 0) {{
    console.error('❌ Aucune date disponible !');
    document.getElementById('tradesTable').innerHTML = '<tr><td colspan="19" style="text-align: center; padding: 40px; color: var(--red);">⚠️ No trading data available. Please ensure JSON files exist in the logs folder.</td></tr>';
    return;
  }}
  
  // Charger le jour le plus récent
  console.log(`📂 Chargement de la date: ${{availableDates[currentDateIndex]}}`);
  loadCurrentDay();
  updateNavButtons();
}});
</script>
</body>
</html>"""

    HTML_FILE.write_text(html_content, encoding="utf-8")
    # Print retiré - sera fait dans _flush()

def _flush():
    """Regénère le HTML principal (SILENCIEUX)"""
    _write_html()

# ---------- API ----------
def log_open_trade(*, position_ticket: int, order_id: int | None, side: str, volume: float, entry_price: float, sl: float, tp1: float = None, tp2: float = None, tp3: float = None, context: dict | None = None):
    ctx = context or {}
    tk = str(position_ticket)
    
    row = {k: "" for k in FIELDS}
    row.update({
        "position_ticket": tk,
        "order_id": str(order_id or ""),
        "status": "OPEN",
        "time_entry": ctx.get("time_entry") or _now(),
        "side": side,
        "volume": str(volume),
        "entry_price": str(entry_price),
        "current_price": str(entry_price),
        "sl": str(sl or ""),
        "tp1": str(tp1 or ""),
        "tp2": str(tp2 or ""),
        "tp3": str(tp3 or ""),
        "sl_hit": "false",
        "tp1_hit": "false",
        "tp2_hit": "false",
        "tp3_hit": "false",
        "symbol": ctx.get("symbol", SYMBOL),
        "cycle_ts": ctx.get("cycle_ts", ""),
        "screenshots": ";".join(ctx.get("screens", [])),
        "spread_pips": ctx.get("spread_pips", ""),
        "session": ctx.get("session", ""),
        "notes": ctx.get("notes", ""),
        "cycle_number": str(ctx.get("cycle_number", "")),
        "decision": str(ctx.get("decision", "")).upper()
    })
    
    _save_row(tk, row)
    _flush()
    print(f"📊 Trade #{tk} ouvert : {side} {volume} @ {entry_price}")

def log_modify_trade(*, position_ticket: int, sl: float | None = None, tp1: float | None = None, tp2: float | None = None, tp3: float | None = None, current_price: float | None = None, notes: str = "", context: dict | None = None, silent: bool = True):
    """
    Modifie un trade (SILENCIEUX par défaut)
    Pour afficher un message, passer silent=False
    """
    tk = str(position_ticket)
    all_rows = _load_all_rows()
    
    if tk not in all_rows:
        all_rows[tk] = {k: "" for k in FIELDS}
        all_rows[tk].update({"position_ticket": tk, "status": "OPEN", "time_entry": _now()})
    
    row = all_rows[tk]
    
    if sl is not None:
        row["sl"] = str(sl)
    if tp1 is not None:
        row["tp1"] = str(tp1)
    if tp2 is not None:
        row["tp2"] = str(tp2)
    if tp3 is not None:
        row["tp3"] = str(tp3)
    if current_price is not None:
        row["current_price"] = str(current_price)
    if notes:
        row["notes"] = notes
    if context:
        if context.get("screens"): row["screenshots"] = ";".join(context["screens"])
        if "cycle_ts" in context: row["cycle_ts"] = context["cycle_ts"]
        if "session" in context: row["session"] = context["session"]
        if "spread_pips" in context: row["spread_pips"] = str(context["spread_pips"])
        if "cycle_number" in context: row["cycle_number"] = str(context["cycle_number"])
        if "decision" in context: row["decision"] = str(context["decision"]).upper()
    
    _save_row(tk, row)
    _flush()
    
    # Print SEULEMENT si explicitement demandé
    if not silent:
        print(f"📊 Trade #{tk} modifié")

def log_close_trade(*, position_ticket: int, exit_price: float, pnl_usd: float | None = None, pnl_eur: float | None = None, pnl_pips: float | None = None, time_exit: str | None = None, symbol: str | None = None, sl_hit: bool = False, tp1_hit: bool = False, tp2_hit: bool = False, tp3_hit: bool = False):
    tk = str(position_ticket)
    all_rows = _load_all_rows()
    
    if tk not in all_rows:
        return
    
    row = all_rows[tk]
    row["status"] = "CLOSED"
    row["time_exit"] = time_exit or _now()
    row["exit_price"] = str(exit_price)
    
    row["sl_hit"] = "true" if sl_hit else "false"
    row["tp1_hit"] = "true" if tp1_hit else "false"
    row["tp2_hit"] = "true" if tp2_hit else "false"
    row["tp3_hit"] = "true" if tp3_hit else "false"
    
    if row.get("time_entry"):
        try:
            t_entry = datetime.fromisoformat(row["time_entry"])
            t_exit = datetime.fromisoformat(row["time_exit"])
            duration = (t_exit - t_entry).total_seconds()
            row["duration_sec"] = str(int(duration))
        except:
            pass
    
    if pnl_usd is not None:
        row["pnl_usd"] = str(round(float(pnl_usd), 2))
    if pnl_eur is not None:
        row["pnl_eur"] = str(round(float(pnl_eur), 2))
    if pnl_pips is not None:
        row["pnl_pips"] = str(round(float(pnl_pips), 1))
    
    if symbol:
        row["symbol"] = symbol
    
    _save_row(tk, row)
    _flush()
    
    # Print du résultat
    pnl_str = f"{pnl_usd:+.2f} USD" if pnl_usd is not None else "N/A"
    pips_str = f"({pnl_pips:+.1f} pips)" if pnl_pips is not None else ""
    print(f"📊 Trade #{tk} fermé : {pnl_str} {pips_str}")

def refresh_open_rows_from_mt5():
    """Rafraîchit les prix des positions ouvertes depuis MT5 (SILENCIEUX)"""
    if not mt5: return
    all_rows = _load_all_rows()
    changed = False
    for p in (mt5.positions_get() or []):
        tk = str(p.ticket)
        if tk in all_rows and all_rows[tk].get("status") == "OPEN":
            all_rows[tk]["current_price"] = str(p.price_current)
            _save_row(tk, all_rows[tk])
            changed = True
    if changed:
        _write_html()  # Flush silencieux sans print

def log_tp_hit(*, position_ticket: int, tp_level: int, lot_closed: float, price: float, pips: float, lot_remaining: float):
    """
    Logger spécifique pour les TP hits
    
    Args:
        position_ticket: Numéro du trade
        tp_level: Niveau de TP (1, 2, ou 3)
        lot_closed: Volume fermé
        price: Prix de fermeture
        pips: Pips gagnés
        lot_remaining: Volume restant
    """
    tk = str(position_ticket)
    
    # ✅ RETRY LOGIC pour éviter "Permission denied"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            all_rows = _load_all_rows()
            
            if tk not in all_rows:
                return
            
            row = all_rows[tk]
            
            # Mettre à jour le flag TP correspondant
            if tp_level == 1:
                row["tp1_hit"] = "true"
            elif tp_level == 2:
                row["tp2_hit"] = "true"
            elif tp_level == 3:
                row["tp3_hit"] = "true"
            
            # Ajouter la note
            row["notes"] = f"TP{tp_level} hit: {lot_closed} lot @ {price} (+{pips:.1f} pips)"
            
            # Mettre à jour le volume si position partiellement fermée
            if lot_remaining > 0:
                row["volume"] = str(lot_remaining)
            
            _save_row(tk, row)
            _flush()
            
            # Print
            print(f"📊 Trade #{tk} → TP{tp_level} hit : {lot_closed} lot @ {price} (+{pips:.1f} pips) | Reste {lot_remaining} lot")
            
            # ✅ Si succès, sortir de la boucle
            break
            
        except PermissionError as e:
            if attempt < max_retries - 1:
                # Attendre un peu avant de réessayer
                import time
                time.sleep(0.1 * (attempt + 1))  # Backoff progressif
            else:
                # Dernière tentative échouée
                print(f"⚠️ Impossible de logger TP{tp_level} hit après {max_retries} tentatives: {e}")
        except Exception as e:
            print(f"⚠️ Erreur inattendue lors du log TP{tp_level} hit: {e}")
            break

def finalize_before_shutdown():
    refresh_open_rows_from_mt5()

def resume_after_restart():
    if not mt5: return
    all_rows = _load_all_rows()
    for p in (mt5.positions_get() or []):
        tk = str(p.ticket)
        if tk not in all_rows:
            log_open_trade(
                position_ticket=p.ticket,
                order_id=getattr(p, "order", None),
                side=("BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"),
                volume=p.volume,
                entry_price=p.price_open,
                sl=p.sl,
                tp1=p.tp if p.tp else None,
                context={"symbol": p.symbol}
            )
        else:
            log_modify_trade(position_ticket=p.ticket, current_price=p.price_current)