"""demo_preview.py - renders the weekly dashboard with FICTIONAL data.

No MetaTrader 5 terminal is required: a fake `MetaTrader5` module is injected
into sys.modules before dashboard.py is imported. Used to produce the
screenshots in the documentation without ever exposing a real account.

The dashboard itself is written in French. For the public screenshots the
rendered HTML is passed through a small label map so the page reads in English.

    python tools/demo_preview.py      writes docs/examples/weekly_demo.html
"""
import sys, types, random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

random.seed(11)


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_deals():
    """Closed deals for the current week, a few hundred dollars each."""
    now = datetime.now()
    monday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    deals = []
    for day in range(min(now.weekday() + 1, 5)):
        for n in range(random.randint(3, 6)):
            t = monday + timedelta(days=day, hours=9 + n * 2, minutes=random.randint(0, 50))
            if t > now:
                continue
            profit = random.choice([412.50, 268.00, 735.20, -184.40, 523.10,
                                    -296.80, 318.60, 604.90])
            deals.append(Obj(entry=1, profit=profit, commission=-12.40, swap=0.0,
                             time=t.timestamp(), symbol=random.choice(["EURUSD", "XAUUSD"])))
    return deals


DEALS = _fake_deals()

mt5 = types.ModuleType("MetaTrader5")
mt5.initialize = lambda *a, **k: True
mt5.shutdown = lambda *a, **k: None
mt5.account_info = lambda: Obj(login="DEMO", server="DEMO-Server", currency="USD",
                               balance=52480.00, equity=54876.30)
mt5.history_deals_get = lambda *a, **k: DEALS
mt5.positions_get = lambda *a, **k: [
    Obj(symbol="EURUSD", type=0, volume=1.50, price_open=1.15802,
        price_current=1.16021, profit=1642.50, sl=1.15742, tp=1.15942),
    Obj(symbol="XAUUSD", type=0, volume=0.40, price_open=3412.65,
        price_current=3410.12, profit=-101.20, sl=3404.90, tp=3428.40),
]
mt5.symbol_info = lambda s: Obj(point=0.00001 if s == "EURUSD" else 0.01,
                                digits=5 if s == "EURUSD" else 2)
sys.modules["MetaTrader5"] = mt5

import dashboard  # noqa: E402

dashboard.OBJECTIF_HEBDO = 5000.0
dashboard.OBJECTIF_JOUR = 1000.0
dashboard.control.mode = lambda: "ON"
dashboard.ledger.bilan = lambda: {
    "net": 0.0, "depenses": 1840.0, "recettes": 1840.0,
    "jours": 42, "debut": "2026-08-01", "track_record_pct": 35.0,
}
dashboard.etat_propfirm = lambda equity, balance: {
    "statut": "OK", "qui_mord": "journaliere",
    "marge_jour": 3850.0, "conso_jour": 23.0, "plancher_jour": 76150.0,
    "marge_total": 7210.0, "conso_total": 11.5, "plancher_total": 72000.0,
    "solde_initial": 80000.0, "seuil_souple": 70.0, "seuil_dur": 90.0,
    "reference_officielle": True,
}
dashboard.logs = lambda n=40: [
    "[09:02:11] Cycle 14 - SELL signal accepted (ADX 27.4, ATR 0.00041)",
    "[09:02:12] Order sent: EURUSD 1.50 lots @ 1.16089, SL 1.16149",
    "[09:28:03] TP1 hit, volume halved, stop moved to break even",
    "[10:15:44] Cycle 15 - no signal, spread filter active (1.8 pip)",
    "[11:33:12] Cycle 16 - BUY signal accepted (ADX 31.2, ATR 0.00047)",
    "[12:15:40] TP3 hit, position fully closed, +735.20 USD",
]

LABELS = [
    ("Objectif du jour", "Daily target"), ("Objectif de la semaine", "Weekly target"),
    ("AUJOURD'HUI", "TODAY"), ("LUNDI &rarr; VENDREDI", "MONDAY &rarr; FRIDAY"),
    ("ouverts non comptes", "floating, not counted"),
    ("ouverts non comptés", "floating, not counted"),
    ("reste", "left"), ("trades clotures", "closed trades"),
    ("trades clôturés", "closed trades"), ("seuil mini", "minimum bar"),
    ("TRADES FERMÉS", "CLOSED TRADES"), ("POSITIONS OUVERTES", "OPEN POSITIONS"),
    ("SOLDE", "BALANCE"), ("TOTAL", "EQUITY"),
    ("COÛT RÉEL DU PROJET", "TRUE COST OF THE PROJECT"),
    ("tout ce qui sort, tout ce qui rentre", "everything out, everything in"),
    ("pas seulement les payouts", "not just the payouts"),
    ("SORTIES", "MONEY OUT"), ("ENTRÉES", "MONEY IN"), ("NET RÉEL", "TRUE NET"),
    ("MARGE PROP FIRM", "PROP FIRM HEADROOM"),
    ("ce qui tue un compte n'est pas de rater une cible, c'est de toucher une limite",
     "what kills an account is not missing a target, it is hitting a limit"),
    ("DANS LES CLOUS", "WITHIN LIMITS"), ("PLUS AUCUNE ENTRÉE", "NO NEW ENTRIES"),
    ("FERMETURE DE TOUT", "CLOSE EVERYTHING"),
    ("la limite journaliere est celle qui mord", "the daily limit is the binding one"),
    ("la limite totale est celle qui mord", "the overall limit is the binding one"),
    ("MARGE DU JOUR", "DAILY HEADROOM"), ("MARGE TOTALE", "OVERALL HEADROOM"),
    ("consomme", "used"), ("consommé", "used"), ("plancher", "floor"),
    ("ARRÊT DES ENTRÉES À", "STOP NEW ENTRIES AT"),
    ("FERMETURE FORCÉE À", "FORCED CLOSE AT"),
    ("de la limite consommee", "of the limit used"),
    ("de la limite consommée", "of the limit used"),
    ("Précédente", "Previous"), ("Suivante", "Next"), ("EN COURS", "CURRENT"),
    ("ouverte(s)", "open"), ("VENTE", "SELL"), ("ACHAT", "BUY"),
    ("cible", "target"), ("PAR JOUR", "BY DAY"), ("clique un jour", "click a day"),
    ("LUNDI", "MONDAY"), ("MARDI", "TUESDAY"), ("MERCREDI", "WEDNESDAY"),
    ("JEUDI", "THURSDAY"), ("VENDREDI", "FRIDAY"),
    ("LUN", "MON"), ("MAR", "TUE"), ("MER", "WED"), ("JEU", "THU"), ("VEN", "FRI"),
    ("clôturés", "closed"), ("gagnants", "winners"), ("perdants", "losers"),
    ("perdant", "loser"), ("gagnant", "winner"), ("objectif", "target"),
    ("jour à venir", "upcoming day"),
    ("Aucun trade clôturé cette semaine", "No closed trade this week"),
    ("TRADES CLÔTURÉS", "CLOSED TRADES"), ("semaine affichée", "week shown"),
    ("JOURNAL TEMPS RÉEL", "LIVE LOG"), ("RELANCER", "RESTART"),
    ("COMPTE", "ACCOUNT"),
    ("Le bot prend de nouveaux trades.", "The bot is taking new trades."),
    ("Le bot gere les positions mais n'en ouvre plus.",
     "The bot manages open positions but opens no new ones."),
    ("Le bot est arrete.", "The bot is stopped."),
    ("lot", "lots"), ("MAJ", "UPDATED"), ("7J", "7D"),
    ("42 j / 120", "42 d / 120"),
]


def to_english(html):
    import re
    # longest first, so "MARGE TOTALE" is translated before "TOTAL"
    for fr, en in sorted(LABELS, key=lambda p: -len(p[0])):
        if len(fr) <= 4:
            html = re.sub(r"(?<![A-Za-zÀ-ÿ])%s(?![A-Za-zÀ-ÿ])" % re.escape(fr), en, html)
        else:
            html = html.replace(fr, en)
    return html


out = ROOT / "docs" / "examples" / "weekly_demo.html"
out.parent.mkdir(parents=True, exist_ok=True)
html = dashboard.page(0).replace('<meta http-equiv="refresh" content="8">', "")
html = to_english(html).replace('<html lang="fr"', '<html lang="en"')
out.write_text(html, encoding="utf-8")
print("written", out, len(html))
