# CLAUDE.md — A.I.T.B

Fichier de contexte lu automatiquement par Claude Code au démarrage de chaque session.
Objectif : permettre à une session vierge de comprendre le projet, de trouver les fichiers, de connaître les pièges déjà rencontrés et de ne pas casser ce qui marche.

> Dernière vérification contre le code : 2026-09-10. Les valeurs de la section 5 ont été relues dans `config/settings.py`. Si tu modifies `settings.py`, mets ce fichier à jour dans la foulée.

---

## 1. Le projet en une phrase

**A.I.T.B** (Artificial Intelligence Trading Bot) est un bot de scalping forex autonome sur **EURUSD** qui capture des screenshots de MetaTrader 5, les envoie à ChatGPT via une macro souris qui pilote une fenêtre Chrome, lit la réponse à l'écran par OCR Tesseract pour en extraire une décision (BUY / SELL / NOTHING), exécute l'ordre via l'API Python MT5, puis suit la position en Multi-TP jusqu'à sa clôture complète, le tout visualisé dans un dashboard HTML temps réel.

Il n'y a **aucun appel à l'API OpenAI**. Le paquet `openai` est installé dans le venv mais n'est importé nulle part. Tout passe par l'interface web et l'OCR.

---

## 2. Environnement machine

| Élément | Valeur |
|---|---|
| OS | Windows 11 |
| Racine projet | `D:\AITB\` |
| Python | 3.12.10, dans `.venv\` à la racine du projet |
| Lancement bot | `aitb_2.bat` (active le venv puis `python run.py`) |
| Lancement launcher | `launcher.bat` (active le venv puis `python launcher.py`) |
| Broker | IC Markets, compte **RAW** |
| Compte MT5 | **00000000** (c'est la valeur de `WINDOW_KEYWORD` : le bot retrouve la fenêtre MT5 en cherchant ce numéro dans le titre) |
| Plateforme | MetaTrader 5, API Python `MetaTrader5` 5.0.5260 |
| OCR | Tesseract, binaire dans `D:\AITB\Tesseract-OCR\tesseract.exe` |
| Automation souris | **MiniMouseMacro** (`D:\AITB\MiniMouseMacro.exe`), fichiers `.mmmacro` dans `macros_fixe/`, tué en fin de chaque cycle |
| Navigateur GPT | profil Chrome dédié `.chrome_profile_aitb_chatgpt/` |
| Langue de travail | Français |

### requirements.txt est incomplet

Il ne liste que `pytesseract`, `pillow`, `pyautogui`, `pywin32`, `schedule`, `tqdm`, `pytz`.
Il **manque** au minimum : `MetaTrader5`, `psutil`, `pygetwindow`, `numpy`. Un `pip install -r requirements.txt` sur une machine vierge ne suffit pas à faire tourner le bot.

---

## 3. Arborescence réelle

```
A.I.T.B_2/
├── aitb/
│   ├── live.py                 # RUNNER HEADLESS, remplace main+scheduler+read_chatgpt_output
│   ├── market_data.py          # lecture OHLC via API MT5 + cache CSV, aucune capture d'écran
│   ├── indicators.py           # SMA, EMA, RSI, ATR, ADX, Donchian, Bollinger, Keltner, MACD (numpy pur)
│   ├── strategies.py           # stratégies déterministes + grilles de paramètres pour le sweep
│   ├── backtest.py             # moteur de backtest bougie par bougie, frais inclus, métriques
│   ├── main.py                 # LEGACY : capture MT5 (BitBlt win32) + execute_decision()
│   ├── scheduler.py            # LEGACY : boucle de cycles, kill MiniMouseMacro
│   ├── read_chatgpt_output.py  # LEGACY : lance la macro d'envoi + OCR de la réponse GPT
│   ├── trade_manager.py        # ordres MT5, filling mode, thread Multi-TP (623 l.)
│   ├── monitor.py              # BE, trailing, détection de clôture, perf.csv (504 l.)
│   ├── logger.py               # journalisation JSON + génération dashboard HTML (1136 l.)
│   ├── risk_manager.py         # calc_lot(), get_risk_in_usd(), validate_lot_size()
│   ├── filters.py              # validate_signal() : spread, session, ATR, cooldown
│   ├── preflight.py            # check_environment() au démarrage du scheduler
│   └── prompt.txt              # instructions envoyées à GPT
├── config/
│   └── settings.py             # CONFIG : dict central de configuration
├── utils/
│   ├── time_logs.py            # log() : print horodaté + écriture console_logs.txt
│   ├── colors.py               # colors() : coloration ANSI du terminal
│   └── aitb_banner.py          # bannière ASCII au démarrage
├── macros_fixe/                # macros MiniMouseMacro pour le poste fixe (ACTIF)
│   ├── open_gpt.mmmacro
│   └── send_screenshots.mmmacro
├── macros_portable/            # mêmes macros calibrées pour le portable
├── progs_tests-cmds/           # scripts utilitaires, PAS de la prod
│   ├── clear_logs.py           # vide le JSON du jour et régénère le HTML
│   ├── regenerate_html.py
│   ├── test_logger.py
│   ├── test_buy.py
│   ├── connection_test.py
│   ├── find_leverage
│   └── test_filling-mode
├── logs/                       # = CONFIG["REPORTS_FOLDER"]
│   ├── trade_logs.html         # UN SEUL fichier dashboard, régénéré à chaque flush
│   ├── trade_logs_YYYY-MM-DD.json   # UN fichier JSON PAR JOUR
│   ├── console_logs.txt        # miroir texte de la console
│   └── report_YYYYMMDD.jsonl   # événements bruts (snapshots, closes, rejets)
├── screenshots/
│   ├── EURUSD/                 # screenshots du cycle en cours + COPIE des JSON du jour
│   └── old_screenshots/        # archive après chaque cycle
├── .venv/
├── .chrome_profile_aitb_chatgpt/
├── tessdoc-main/               # doc Tesseract, pas du code
├── run.py                      # POINT D'ENTRÉE du bot
├── launcher.py                 # launcher séparé
├── aitb_2.bat / launcher.bat
├── requirements.txt
├── debug_bbox.png              # dernière capture OCR (écrasée à chaque cycle)
├── debug_bbox_retry.png        # capture de la seconde chance OCR
└── CLAUDE.md                   # ce fichier
```

Attention : `perf.csv` et `context.jsonl` sont écrits **à la racine du projet**, pas dans `logs/`. Voir 10.7.

---

## 3bis. Mode headless (nouvelle architecture, 2026-09-10)

La chaîne screenshot → MiniMouseMacro → ChatGPT → OCR est **abandonnée**. Elle est conservée en LEGACY mais ne doit plus être développée.

### Pourquoi

- Elle monopolise la souris et le premier plan, donc le PC est inutilisable pendant que le bot tourne
- Elle ne peut pas tourner sur un serveur
- Elle n'est pas backtestable : un LLM ne répond pas deux fois pareil, donc impossible de mesurer un edge
- Retour d'expérience mesuré (voir `notes personnelles`) : sur de l'argent réel, Claude Opus 4.5 est le modèle le plus cher ET celui qui perd le plus. Le coût par décision doit être nul.

### Le nouveau découpage

| Qui | Fait quoi |
|---|---|
| **Claude / une IA** | Cherche des stratégies **hors ligne**, les backteste en masse, jette les mauvaises. Jamais dans la boucle live. |
| **Le bot** | Exécute une règle **déterministe et figée**, lue dans `CONFIG["LIVE_STRATEGY"]`. |

### Commandes

```bash
# 1. Télécharger l'historique (terminal MT5 lancé et connecté)
python research/sweep.py --download EURUSD M15 EURUSD H1 XAUUSD M15

# 2. Chercher une stratégie qui passe la grille d'acceptation
python research/sweep.py --all --detail 3

# 3. Renseigner LIVE_STRATEGY / LIVE_STRATEGY_PARAMS dans config/settings.py

# 4. Vérifier sans envoyer d'ordre
python -m aitb.live --status
python -m aitb.live --dry-run

# 5. Lancer en réel
aitb_headless.bat
```

### Grille d'acceptation (figée, non négociable)

Une stratégie n'a le droit d'aller en forward test **que si** :

- au moins **100 trades** sur le backtest
- **max drawdown < 20%**
- **profit factor > 1.3**
- elle **bat le buy and hold** de l'instrument

Ces seuils viennent d'un retour d'expérience sur 2000 backtests dont plus de 1600 ont échoué en live. Le win rate ne fait pas partie des critères : une stratégie à 34% de win rate peut être excellente si le R:R est à l'endroit.

Ensuite, **forward test 4 semaines minimum** sur démo ou compte centime. Aucun euro réel avant.

### Règles du moteur de backtest

Chacune corrige une erreur qui fabrique de faux edges. Ne pas les assouplir.

1. Le signal calculé sur la bougie `i` clôturée s'exécute à l'**ouverture de la bougie i+1**. Le live utilise `fetch_rates(shift=1)` pour appliquer exactement la même règle.
2. Commission, spread et slippage sont **toujours** appliqués, non désactivables.
3. Si le SL et le TP sont dans le range de la même bougie, on considère le **SL touché en premier**.
4. Le volume est calculé pour que la distance jusqu'au stop vaille exactement `risk_pct` % du capital. Si le lot minimum du broker forcerait un risque plus élevé, le trade est **refusé**, pas dégradé.

Le moteur a été validé sur données synthétiques : signal oracle rentable, signal inverse perdant, frais mesurables, sizing à 1.06% pour une consigne de 1%, et surtout **toutes les stratégies perdent sur une marche aléatoire**. Un backtester qui trouve un edge dans du bruit est cassé.

### Contrainte serveur

La lib `MetaTrader5` parle au **terminal local**, pas aux serveurs du broker. Donc :

- sur le PC de Weasel : MT5 réduit dans la barre des tâches, le bot tourne par-dessus sans gêner
- sur un serveur : il faut un **VPS Windows** (~12 à 20€/mois) avec MT5 installé. Un VPS Linux à 5€ ne suffit pas.
- le code est transportable tel quel, aucune modification à faire pour passer du PC au VPS

---

## 4. Flux d'un cycle complet (LEGACY)

Point d'entrée : `aitb_2.bat` → `run.py` → `aitb.scheduler.start_scheduler()`.

`start_scheduler()` arme trois jobs `schedule` puis lance le premier cycle immédiatement :
- `run_cycle` toutes les `INTERVAL_MINUTES` (5 min)
- `monitor_positions_once` toutes les `MONITOR_INTERVAL_SECONDS` (2 s)
- `monitor_closed_positions_once` toutes les `MONITOR_INTERVAL_SECONDS`
- plus `refresh_open_rows_from_mt5` armé à l'import du module

```
Cycle #N démarre
  ├─ Screenshot 1  →  1-EURUSD_M0_XXmXXs.png     (T+0)
  ├─ Screenshot 2  →  2-EURUSD_M5_XXmXXs.png     (T+5min)
  ├─ Screenshot 3  →  3-EURUSD_M10_XXmXXs.png    (T+10min)
  │    capture_mt5_window() : BitBlt de la fenêtre dont le titre contient WINDOW_KEYWORD
  ├─ get_decision_from_gpt_output()
  │    ├─ subprocess.Popen("macros_fixe/send_screenshots.mmmacro")
  │    │    MiniMouseMacro pilote la souris : joint les 3 PNG dans ChatGPT et envoie
  │    ├─ sleep POST_MACRO_WAIT (16 s par défaut)
  │    ├─ log "--> sent ✅"
  │    ├─ barre tqdm "GPT thinking... 100%" sur GPT_THINKING_STEPS (9 s)
  │    ├─ screenshot de la zone GPT_REPLY_BBOX + prétraitement + Tesseract --psm 6
  │    ├─ regex \b(BUY|SELL|NOTHING)\b
  │    └─ si rien : seconde chance après GPT_FALLBACK_WAIT, sinon None
  ├─ execute_decision(decision)
  │    ├─ NOTHING → "No trade executed."
  │    ├─ initialize_mt5()  (démarre aussi le thread Multi-TP)
  │    ├─ reverse auto si HARD_REVERSE_ON_SIGNAL et position opposée ouverte
  │    ├─ validate_signal() : spread, ATR, session, one-position, cooldown
  │    ├─ open_trade() : calc_lot + ordre marché + SL/TP
  │    └─ note_open_ticket() pour le suivi de perf
  ├─ archive_screenshots() → screenshots/old_screenshots/
  ├─ MMC KILLED (close_mm_macro_files + close_mmmacro_windows)
  └─ pause 5 s puis Cycle #N+1 immédiat
```

Le bot s'arrête proprement après `MAX_CYCLES` (40).

---

## 5. Configuration (`config/settings.py`)

Le dict `CONFIG` est la source unique de vérité. Valeurs **réellement en place** :

### Paths
| Clé | Valeur | Rôle |
|---|---|---|
| `ROOT` | `str(Path(__file__).resolve().parent)` | ⚠️ pointe sur `config/`, PAS sur la racine. Piège, voir 10.8 |
| `SCREENSHOTS_FOLDER` | `"SCREENSHOTS/EURUSD"` | contient **déjà** le symbole, voir 6.4 |
| `REPORTS_FOLDER` | `"logs"` | JSON, HTML, logs texte |
| `TRADE_LOG_HTML` | `"trade_logs.html"` | |
| `HTML_AUTO_REFRESH_SECONDS` | `15` | meta refresh du dashboard |
| `PERF_CSV` | `"perf.csv"` | chemin relatif nu → atterrit à la racine |
| `LOG_CONTEXT_JSONL` | `"context.jsonl"` | idem |
| `MACROS_FOLDER` | `"macros_fixe"` | basculer sur `"macros_portable"` sur le laptop |
| `WINDOW_KEYWORD` | `"00000000"` | numéro de compte, sert à retrouver la fenêtre MT5 |
| `TESSERACT_PATH` | `D:\AITB\Tesseract-OCR\tesseract.exe` | |
| `TRADE_COMMENT` | `"🐐 A.I.T.B"` | commentaire de l'ordre MT5 |

### Schedule et marché
| Clé | Valeur |
|---|---|
| `TIME_ZONE` | `"Europe/Paris"` |
| `INTERVAL_MINUTES` | `5` |
| `IMAGES_PER_ANALYSIS` | `3` |
| `MAX_CYCLES` | `40` |
| `SYMBOL` | `"EURUSD"` |
| `MAX_SPREAD_PIPS` | `1.0` |
| `ALLOWED_SESSIONS` | `["EU", "US"]`, `SESSION_FILTER` à `True` |
| `MIN_ATR_POINTS` | `7` |

### Risque
| Clé | Valeur |
|---|---|
| `RISK_PCT` | `5.0` |
| `MAX_RISK_PCT` | `100.0` |
| `LOTS` | `0.04` |
| `TP_PIPS` | `5` |
| `SL_PIPS` | `3` |
| `MIN_LOT` / `MAX_LOT` / `LOT_STEP` | `0.01` / `0.1` / `0.01` |
| `MAX_OPEN_POSITIONS` | `100` |
| `ONE_POSITION_RULE` | `False` (stacking autorisé) |
| `COOLDOWN_MINUTES` | `0` |
| `DAILY_DRAWDOWN_LIMIT_PCT` | `6` |
| `MAX_DAILY_LOSS_USD` | `60` |

### Multi-TP
```python
"USE_MULTI_TP": True,
"MULTI_TP": [
    {"pips": 1, "close_pct": 60},
    {"pips": 2, "close_pct": 30},
    {"pips": 3, "close_pct": 10},
],
```

La clé est **`close_pct`**, pas `percent`. `trade_manager._create_tp_manager()` lit `tp_config.get("close_pct", 0)` et ignore silencieusement tout niveau à 0. Une faute de frappe sur cette clé désactive le Multi-TP sans le moindre message d'erreur.

Le total des `close_pct` peut être inférieur à 100 : le reliquat continue en trailing.

### BE, trailing, exécution
| Clé | Valeur |
|---|---|
| `BE_TRIGGER_PIPS` | `6` |
| `TRAIL_TRIGGER_PIPS` | `10` |
| `TRAIL_DISTANCE_PIPS` | `8` |
| `MONITOR_INTERVAL_SECONDS` | `2` |
| `HARD_REVERSE_ON_SIGNAL` | `False` |
| `MAGIC` | `20251104` |
| `DEVIATION_POINTS` | `20` |
| `FILLING_MODE` | `"IOC"` (obligatoire chez IC Markets RAW) |

### Clés lues par le code mais absentes de CONFIG (donc valeurs par défaut)

`read_chatgpt_output.py` s'appuie sur quatre clés qui ne sont pas définies dans `settings.py` :

| Clé | Défaut appliqué | Effet |
|---|---|---|
| `GPT_REPLY_BBOX` | `(1220, 120, 1220, 940)` en X, Y, W, H | zone OCR de la réponse GPT |
| `POST_MACRO_WAIT` | `16` s | doit couvrir la durée de `send_screenshots.mmmacro` |
| `GPT_THINKING_STEPS` | `9` s | durée de la barre de progression |
| `GPT_FALLBACK_WAIT` | `4` s | délai avant la seconde tentative OCR |

Si l'OCR rate systématiquement, c'est presque toujours la bbox ou `POST_MACRO_WAIT` qu'il faut ajuster. Les ajouter explicitement dans CONFIG est la bonne pratique.

---

## 6. `aitb/logger.py` : le cœur de la journalisation

### 6.1 Principe

- **UN fichier JSON par jour** : `logs/trade_logs_YYYY-MM-DD.json`
- **UN SEUL fichier HTML** : `logs/trade_logs.html`, régénéré à chaque écriture, avec navigation entre les jours
- **Duplication automatique** du JSON du jour dans `screenshots/EURUSD/` à chaque sauvegarde

### 6.2 Structure d'un JSON de jour

```json
{
  "dashboard_stats": { ... },
  "1330200246": { ...trade... },
  "1330211687": { ...trade... }
}
```

`dashboard_stats` est recalculé à chaque sauvegarde par `_calculate_dashboard_stats()` et contient : `total_pnl_usd`, `total_pnl_pct`, `win_rate`, `total_trades`, `winning_trades`, `losing_trades`, `active_positions`, `active_buy`, `active_sell`, `best_trade_usd`, `best_trade_pips`, `worst_trade_usd`, `worst_trade_pips`, `total_volume`, `profit_factor`, `last_update`.

### 6.3 FIELDS (schéma d'un trade)

Toutes les valeurs sont stockées **en string**, y compris les booléens (`"true"` / `"false"`). Ne pas changer ce contrat sans adapter le JavaScript du dashboard.

```
position_ticket, order_id, status,
time_entry, time_exit, duration_sec,
side, volume,
entry_price, current_price, exit_price,
sl, tp1, tp2, tp3,
sl_hit, tp1_hit, tp2_hit, tp3_hit,
pnl_usd, pnl_eur, pnl_pips,
rr_ratio, drawdown_pips,
equity_before, equity_after,
commission_usd, swap_usd,
symbol, session, trend_context,
cycle_ts, screenshots, spread_pips, notes,
cycle_number, decision
```

`status` vaut `"OPEN"` ou `"CLOSED"`. Le dashboard applique les classes CSS `status-open` / `status-closed` à partir de cette valeur en minuscules.

### 6.4 Résolution du chemin screenshots (piège connu)

`SCREENSHOTS_FOLDER` vaut `"SCREENSHOTS/EURUSD"` : il se termine **déjà** par le symbole. Le logger détecte ce cas pour éviter de créer `SCREENSHOTS/EURUSD/EURUSD/` (`logger.py:20-31`) :

```python
screenshots_folder_str = str(SCREENSHOTS_FOLDER).replace("\\", "/").upper()
if screenshots_folder_str.endswith(f"/{SYMBOL.upper()}") or screenshots_folder_str.endswith(SYMBOL.upper()):
    SYMBOL_SCREENSHOTS = SCREENSHOTS_FOLDER
else:
    SYMBOL_SCREENSHOTS = SCREENSHOTS_FOLDER / SYMBOL
```

**Ne pas simplifier cette logique.** Le bug du double dossier `EURUSD/EURUSD` est revenu deux fois.

Note : le dossier sur disque s'appelle `screenshots` en minuscules alors que CONFIG écrit `SCREENSHOTS`. Sans conséquence sur Windows, mais à ne pas "corriger" en cassant autre chose.

### 6.5 API publique du logger

**Toutes ces fonctions sont keyword-only** (signature en `*`). Un appel positionnel lève un `TypeError`.

| Fonction | Print ? | Usage |
|---|---|---|
| `init_logs_txt()` | oui | réinitialise `console_logs.txt` au démarrage |
| `append_to_logs_txt(msg)` | non | ajoute une ligne au fichier texte |
| `log_open_trade(...)` | **oui** | à l'exécution d'un ordre |
| `log_modify_trade(..., silent=True)` | non par défaut | modifications SL/TP/prix |
| `log_tp_hit(...)` | **oui** | quand un TP partiel est touché |
| `log_close_trade(...)` | **oui** | clôture complète avec P&L |
| `refresh_open_rows_from_mt5()` | **jamais** | rafraîchit les prix courants |
| `finalize_before_shutdown()` | non | avant arrêt |
| `resume_after_restart()` | non | réattache les positions ouvertes |

### 6.6 `log_tp_hit()` : la fonction critique

C'est elle, et **pas** `log_modify_trade()`, qui doit être appelée quand un TP partiel se déclenche. Elle fait quatre choses en un appel :

1. Passe `tpN_hit` à `"true"` dans le JSON (c'est ce flag qui colore la valeur en **bleu** dans le dashboard)
2. Écrit la note `"TPN hit: X lot @ prix (+Y pips)"`
3. Met à jour le `volume` restant et le champ `decision` (`"TP1"`, `"TP2"`, `"TP3"`)
4. Régénère le dashboard et print une ligne unique

Signature réelle (`logger.py:1056`), **keyword-only** :

```python
log_tp_hit(
    position_ticket=ticket,
    tp_level=1,
    lot_closed=0.06,
    price=1.15839,
    pips=1.0,
    lot_remaining=0.04,
)
```

Symétriquement, `sl_hit = "true"` colore le SL en **rouge**.

⚠️ **Actuellement le code ne l'appelle pas.** Voir bug 10.7.

---

## 7. Le dashboard (`logs/trade_logs.html`)

- Fichier **unique**, régénéré à chaque flush, jamais un fichier par jour
- `<meta http-equiv="refresh" content="15">` pour le temps réel
- Navigation gauche / droite entre les dates disponibles
- 4 cartes de stats : Total P&L, Win Rate, Active Positions, Best Trade
- Tableau : Cycle, Ticket, Decision, Status, Symbol, Side, Vol, Entry, Current, Exit, P&L $, P&L pips, Entry Time, Exit Time, SL, TP1, TP2, TP3, Screenshots
- Tri : positions `OPEN` en premier, puis par `time_entry` décroissant

`_write_html()` fait environ 640 lignes de f-string contenant tout le HTML/CSS/JS (`logger.py:275-915`).

### Chargement des données : cache inline obligatoire

Le HTML embarque les données du jour le plus récent directement dans le JS (`inlineDataCache`) avant de tenter un `fetch()`. **Raison** : ouvert en `file://`, un `fetch()` sur un JSON local est bloqué par CORS et le dashboard affiche "No data available" alors que le JSON est plein. Le cache inline règle ce cas.

Pour naviguer sur les jours antérieurs, il faut un serveur HTTP :

```bash
cd logs
python -m http.server 8000
# puis http://localhost:8000/trade_logs.html
```

### Coloration conditionnelle

| Condition | Classe CSS | Couleur |
|---|---|---|
| `sl_hit === 'true'` | `.sl-hit` | rouge |
| `tpN_hit === 'true'` | `.tp-hit` | bleu |
| `side === 'BUY'` | `.side-buy` | vert |
| `side === 'SELL'` | `.side-sell` | rouge |
| P&L ≥ 0 | `.pnl-positive` | vert |

---

## 8. Règle d'or : la console ne doit JAMAIS spammer

C'est le problème qui est revenu **quatre fois** dans l'historique du projet. Le monitoring Multi-TP tourne toutes les 2 secondes et appelle le logger plusieurs fois par cycle. Si le logger print à chaque sauvegarde, la console devient illisible.

### Règles

1. **`_flush()` et `_write_html()` sont TOTALEMENT silencieux.** Aucun `print()` dedans, jamais.
2. **`_duplicate_json_to_screenshots()` est silencieux.** Il avale ses exceptions.
3. **`refresh_open_rows_from_mt5()` est silencieux.** Il appelle `_write_html()` directement, pas une fonction qui print. Il est armé toutes les 2 secondes dans `scheduler.py:42`.
4. **`log_modify_trade()` a `silent=True` par défaut.** Passer `silent=False` uniquement pour un événement humainement significatif.
5. Seuls **quatre événements** produisent un print du logger : ouverture, TP hit, clôture, et une modification explicitement marquée non silencieuse.

### Sortie console attendue

```
[17:13:15] 🎯 BUY @ 1.15825 | Multi-TP: 1p(60%) → 2p(30%) → 3p(10%) | SL: 1.15795 | Lot: 0.1
[17:13:15] ✅ BUY EXECUTED : #1325500001
📊 Trade #1325500001 ouvert : BUY 0.1 @ 1.15825
[17:13:20] 🔁 Cycle #2
[17:13:27] ✅ TP1 HIT: #1325500001 | Fermé 0.06 lot @ 1.15839 (+1.0 pips)
📊 Trade #1325500001 → TP1 hit : 0.06 lot @ 1.15839 (+1.0 pips) | Reste 0.04 lot
```

Ce qu'on ne veut **jamais** revoir :

```
📊 Dashboard mis à jour (2025-11-18)
📊 Dashboard mis à jour (2025-11-18)
[x1000]
```

---

## 9. Le suivi de position : deux mécanismes distincts

Point important : le Multi-TP et le break-even/trailing ne vivent **pas** dans le même fichier.

### 9.1 Multi-TP → `aitb/trade_manager.py` (thread daemon)

`initialize_mt5()` démarre un thread `MultiTPManager` si `USE_MULTI_TP` est vrai (`trade_manager.py:59-67`).

- `_run_multi_tp_monitor()` (l. 228) : boucle infinie, scanne `mt5.positions_get(symbol, magic)` toutes les `MONITOR_INTERVAL_SECONDS`, crée un manager par ticket, appelle `manager.check_and_execute()`, nettoie les managers des positions disparues.
- `_create_tp_manager(position)` (l. 274) : définit une classe `TPManager` **inline**, calcule les prix cibles à partir de `MULTI_TP_CONFIG` avec `pip = 10 * info.point`.
- `_log_tp_hit(ticket, result)` (l. 396) : print console + écriture dashboard.

### 9.2 BE / trailing / clôture → `aitb/monitor.py` (jobs `schedule`)

État global du module :

| Variable | Contenu |
|---|---|
| `_last_open_ticket` | ticket de la position courante suivie |
| `_last_context` | contexte attaché au trade (screenshots, session, spread) |
| `_last_volume_tracked` | `{ticket: dernier_volume_connu}` pour détecter les fermetures partielles |
| `_position_closed_time` | `{ticket: timestamp}` pour temporiser la récupération des deals |

Fonctions :

- `attach_context(context)` : stocke le contexte du prochain trade et l'écrit dans `context.jsonl`
- `note_open_ticket(ticket)` : mémorise le ticket et initialise le tracking du volume
- `monitor_positions_once()` : break-even puis trailing stop
- `monitor_closed_positions_once()` : détecte fermetures partielles (Multi-TP) et totales
- `_fetch_closed_trade_result(ticket, retry_count)` : lit `mt5.history_deals_get()` sur 3 jours, agrège `DEAL_ENTRY_IN` et `DEAL_ENTRY_OUT`, retry jusqu'à 3 fois avec délai
- `_append_perf_row(row)` : écrit dans `perf.csv`
- `record_event(event)` : append JSONL dans `report_YYYYMMDD.jsonl`
- `get_open_position()`, `close_position()` : utilisées par le reverse auto de `main.py`

### 9.3 Détection Multi-TP par comparaison de volume

```
volume_actuel < volume_précédent  →  fermeture partielle
lot_fermé = volume_précédent - volume_actuel
```

Séquence type sur un lot de 0.1 : `0.1 → TP1 → 0.04 → TP2 → 0.01 → TP3 → 0.0`

---

## 10. Bugs connus et leurs correctifs

### 10.1 `Permission denied` sur le JSON

```
⚠️ Log HTML échoué: [Errno 13] Permission denied: 'logs\trade_logs_2025-11-20.json'
```

**Cause** : le fichier est verrouillé au moment de l'écriture (navigateur en auto-refresh, antivirus, écriture concurrente entre le thread Multi-TP et le thread principal).

**Correctif** : toute écriture de JSON passe par un retry avec backoff exponentiel (3 tentatives, 0.1 s puis 0.2 s puis 0.4 s). Ne pas retirer ce mécanisme.

### 10.2 "Impossible de retrouver la perf de la position fermée"

```
⏳ Tentative 1/3 : Données incomplètes, nouvelle tentative...
⚠️ Impossible de retrouver la perf de la position fermée après plusieurs tentatives.
```

**Cause réelle** : ce n'est pas un problème d'historique MT5. La position **n'est pas réellement fermée**. Après TP1 il reste 0.04 lot ouvert, mais `mt5.positions_get(ticket=...)` peut retourner une liste vide temporairement pendant la synchronisation. Le monitor croit alors à une clôture totale et va chercher un `DEAL_ENTRY_OUT` final qui n'existe pas encore.

**Correctif** : avant de chercher les deals, vérifier `_last_volume_tracked[ticket]`.

| Dernier volume connu | Action |
|---|---|
| > 0.01 | position encore ouverte, ne rien faire |
| entre 0 et 0.01 | volume résiduel, skip |
| = 0.0 | vraiment fermée, aller chercher les deals |

Plus un délai minimum de 2 secondes après détection de fermeture avant la première requête d'historique.

### 10.3 Volumes flottants sales

MT5 renvoie `0.04000000000000001` au lieu de `0.04`. Toujours arrondir à l'affichage : `round(volume, 2)`. Ne jamais comparer un volume avec `==`, utiliser un seuil (`< 0.01`).

### 10.4 Dashboard vide alors que le JSON est plein

Voir section 7 : CORS en `file://`. Le cache inline est la parade.

### 10.5 Double dossier `EURUSD/EURUSD`

Voir section 6.4.

### 10.6 OCR qui échoue

```
⚠️ No valid decision found in GPT output.
🧪 OCR snippet: S>CHATGPT.V PARTAGER- GITIE E ++POSERUNEQUESTION...
```

Comportement attendu : logger le snippet OCR, skip l'exécution, passer au cycle suivant. Ne pas planter, ne pas retry en boucle. Une seconde tentative est déjà intégrée.

Pour diagnostiquer : ouvrir `debug_bbox.png` et `debug_bbox_retry.png` à la racine, ce sont les captures brutes de la zone OCR. Si la réponse GPT n'est pas dedans, c'est `GPT_REPLY_BBOX` qu'il faut recalibrer.

### 10.7 ⚠️ OUVERT : les flags `tpN_hit` ne passent jamais à `"true"`

`trade_manager._log_tp_hit()` (l. 406-414) appelle **`log_modify_trade()`** et non `log_tp_hit()` :

```python
from aitb.logger import log_modify_trade
log_modify_trade(
    position_ticket=ticket,
    current_price=price,
    notes=f"TP{tp_level} hit: {closed_vol} lot @ +{pips} pips",
    context={"decision": f"TP{tp_level}"}
)
```

Conséquences :
- `tp1_hit` / `tp2_hit` / `tp3_hit` restent `"false"` en permanence
- la **coloration bleue des TP dans le dashboard ne se déclenche jamais**
- le champ `volume` n'est pas décrémenté du lot fermé

Le correctif est d'appeler `log_tp_hit(position_ticket=..., tp_level=..., lot_closed=..., price=..., pips=..., lot_remaining=...)` à la place. Pas encore fait, à valider avant de toucher.

### 10.8 ⚠️ OUVERT : chemins relatifs mal ancrés

- `CONFIG["ROOT"]` vaut `str(Path(__file__).resolve().parent)` dans `config/settings.py:6`, ce qui renvoie le dossier **`config`**, pas la racine du projet. Personne ne l'utilise aujourd'hui, mais toute nouvelle utilisation partira du mauvais dossier.
- `PERF_CSV = "perf.csv"` et `LOG_CONTEXT_JSONL = "context.jsonl"` sont des chemins relatifs nus. `monitor.attach_context()` crée bien `logs/` puis écrit dans `Path("context.jsonl")`, donc **à la racine du projet**, pas dans `logs/`. Idem pour `perf.csv`. Corriger revient soit à préfixer les valeurs par `logs/`, soit à faire `Path(CONFIG["REPORTS_FOLDER"]) / CONFIG["LOG_CONTEXT_JSONL"]` dans le code. Les deux fonctionnent, il faut choisir et s'y tenir.
- `scheduler.archive_screenshots()` contient un chemin absolu en dur (`scheduler.py:47`), ce qui casse la portabilité entre le fixe et le portable alors que le reste du projet a été pensé pour (cf. `macros_fixe` / `macros_portable`).

---

## 11. `progs_tests-cmds/clear_logs.py`

Attention, ce script n'est **pas** à la racine, il est dans `progs_tests-cmds/`.

Il vide **uniquement** le JSON du jour courant, sa copie dans `screenshots/EURUSD/`, puis régénère le HTML. Il ne supprime aucun fichier et n'en crée aucun autre.

Le fichier vidé conserve un bloc `dashboard_stats` remis à zéro, pas un `{}` nu, sinon le dashboard casse.

```bash
python progs_tests-cmds/clear_logs.py
```

Voisins utiles dans le même dossier : `regenerate_html.py` (reconstruit le dashboard sans toucher aux données), `test_buy.py` (ordre de test), `connection_test.py` (vérifie la connexion MT5), `test_filling-mode` (identifie le filling mode accepté par le broker).

---

## 12. Comportement broker IC Markets

- Compte **RAW** : le filling mode doit être **IOC**, pas FOK. Un FOK est rejeté silencieusement. `_get_filling_mode()` (`trade_manager.py:148`) lit `CONFIG["FILLING_MODE"]`.
- Les restrictions de volume dépendent du **solde du compte**, pas seulement du levier.
- `close_position()` envoie un ordre marché inverse (`ORDER_TYPE_SELL` pour fermer un BUY) au prix `bid` ou `ask` selon le sens.
- Le `MAGIC` `20251104` filtre les positions du bot : les positions ouvertes à la main dans MT5 ne sont pas suivies par le Multi-TP.

---

## 13. Conventions de travail avec Claude Code

### Code

1. **Ne jamais toucher aux `print()` et aux `log()` existants** lors d'un refactor. Ils sont calibrés.
2. **Lire le fichier avant de l'éditer.** Ne pas régénérer `logger.py` de mémoire : 1136 lignes dont ~640 de HTML/CSS/JS en f-string.
3. Attention aux **accolades doublées** `{{ }}` dans le HTML embarqué en f-string Python. Une accolade simple casse le rendu.
4. **Un correctif = un fichier livré en entier**, pas des fragments à recoller à la main.
5. Ne pas créer de fichiers annexes non demandés (scripts de test, guides, diagnostics). Weasel veut le fichier corrigé, point.
6. Les valeurs du JSON sont des **strings**. Le JS compare `=== 'true'`, pas `=== true`.
7. Les fonctions publiques du logger sont **keyword-only**. Toujours nommer les arguments.

### Rédaction

- **Pas de tirets cadratins** dans les textes et livrables. Reformuler avec un point, une virgule ou deux-points.
- Listes en français : **pas de virgule avant "et"**. Écrire `X, Y et Z`.
- Réponses concises, pas de préambule.

---

## 14. Checklist de démarrage de session

Avant de proposer une modification, vérifier :

- [ ] Le fichier concerné a été lu dans sa version actuelle
- [ ] Le changement n'ajoute pas de `print()` dans un chemin de code appelé en boucle (thread Multi-TP, jobs `schedule` à 2 s)
- [ ] Les flags `tpN_hit` / `sl_hit` restent cohérents avec la coloration du dashboard
- [ ] Les écritures fichier conservent leur retry
- [ ] La détection de fermeture vérifie le volume avant de chercher les deals
- [ ] Le contrat "tout en string" du JSON est respecté
- [ ] Les appels au logger sont keyword-only
- [ ] Si `settings.py` change, la section 5 de ce fichier est mise à jour

---

## 15. Chantiers ouverts

### Fait le 2026-09-10

- [x] Architecture headless : `market_data.py`, `indicators.py`, `strategies.py`, `backtest.py`, `live.py`
- [x] **Bug majeur corrigé** : `filters.py` avait un `from logger import log` inexistant. Comme `main.py` et `scheduler.py` enveloppent cet import dans un `try/except` silencieux, `validate_signal` valait **toujours** `None`. Le contrôle de spread, le filtre de session, l'ATR minimum et le cooldown **n'ont jamais tourné en production**. Import mort supprimé.
- [x] `open_trade()` accepte maintenant `symbol`, `sl_pips`, `tp_pips` et `risk_pct` en option. Sans arguments, comportement strictement identique à avant.

### À faire

- [ ] **Lancer MT5 et télécharger l'historique**, puis passer la config actuelle au backtester. Verdict attendu : profit factor sous 1.0
- [ ] **Réparer le R:R.** SL 3 pips avec TP 1-2-3 pips exige 82% de win rate juste pour l'équilibre, commissions incluses. Cible : gain moyen ≥ 1.5 × perte moyenne, frais inclus
- [ ] Corriger 10.7 : `_log_tp_hit()` doit appeler `log_tp_hit()`, sinon la colorisation TP du dashboard est morte
- [ ] Corriger 10.8 : ancrer `perf.csv` et `context.jsonl` dans `logs/`, réparer `CONFIG["ROOT"]`, retirer le chemin absolu de `archive_screenshots()`
- [ ] Compléter `requirements.txt` (`MetaTrader5`, `psutil`, `pygetwindow`, `numpy`)
- [ ] Une fois une stratégie validée : forward test 4 semaines sur démo avant tout euro réel
- [ ] Écrire noir sur blanc la définition chiffrée de ce qu'est un bon mois et un mauvais mois. Sans destination écrite, aucune amélioration n'est mesurable
- [ ] Quand le legacy sera confirmé mort : supprimer `read_chatgpt_output.py`, `macros_fixe/`, `macros_portable/`, la dépendance Tesseract et le profil Chrome
