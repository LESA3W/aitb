"""apercu_demo.py - rend le tableau de bord hebdomadaire avec des donnees FICTIVES.

Aucun terminal MetaTrader 5 n'est necessaire : un faux module `MetaTrader5` est
injecte dans sys.modules avant l'import de dashboard.py. Sert a produire les
captures d'ecran de la documentation sans jamais exposer un compte reel.

    python outils/apercu_demo.py            ecrit docs/exemples/apercu_demo.html
"""
import sys, types, time, random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

random.seed(11)


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _faux_deals():
    now = datetime.now()
    lundi = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    deals = []
    for jour in range(min(now.weekday() + 1, 5)):
        for n in range(random.randint(2, 5)):
            t = lundi + timedelta(days=jour, hours=9 + n * 2, minutes=random.randint(0, 50))
            if t > now:
                continue
            profit = round(random.choice([14.2, 9.8, 22.5, -8.4, 17.1, -11.2, 6.9]), 2)
            deals.append(Obj(entry=1, profit=profit, commission=-0.7, swap=0.0,
                             time=t.timestamp(), symbol="EURUSD"))
    return deals


DEALS = _faux_deals()

mt5 = types.ModuleType("MetaTrader5")
mt5.initialize = lambda *a, **k: True
mt5.shutdown = lambda *a, **k: None
mt5.account_info = lambda: Obj(login="DEMO", server="DEMO-Server", currency="USD",
                               balance=1000.0, equity=1012.40)
mt5.history_deals_get = lambda *a, **k: DEALS
mt5.positions_get = lambda *a, **k: [
    Obj(symbol="EURUSD", type=1, volume=0.04, price_open=1.16089,
        price_current=1.16042, profit=1.88, sl=1.16149, tp=1.15949),
]
mt5.symbol_info = lambda s: Obj(point=0.00001, digits=5)
sys.modules["MetaTrader5"] = mt5

import dashboard  # noqa: E402

dashboard.control.mode = lambda: "ON"
dashboard.ledger.bilan = lambda: {
    "net": 0.0, "depenses": 180.0, "recettes": 180.0,
    "jours": 42, "debut": "2026-08-01", "track_record_pct": 46.0,
}
dashboard.etat_propfirm = lambda equity, balance: {
    "statut": "OK", "qui_mord": "journalière",
    "marge_jour": 3850.0, "conso_jour": 23.0, "plancher_jour": 76150.0,
    "marge_total": 7210.0, "conso_total": 11.5, "plancher_total": 72000.0,
    "solde_initial": 80000.0, "seuil_souple": 70.0, "seuil_dur": 90.0,
    "reference_officielle": True,
}
dashboard.logs = lambda n=40: [
    "[09:02:11] Cycle 14 — signal SELL retenu (ADX 27.4, ATR 0.00041)",
    "[09:02:12] Ordre envoye : EURUSD 0.04 lot @ 1.16089, SL 1.16149",
    "[09:28:03] TP1 touche, volume reduit de moitie, SL deplace au point mort",
    "[10:15:44] Cycle 15 — aucun signal, filtre de spread actif (1.8 pip)",
]

sortie = ROOT / "docs" / "exemples" / "apercu_demo.html"
sortie.parent.mkdir(parents=True, exist_ok=True)
html = dashboard.page(0).replace('<meta http-equiv="refresh" content="8">', "")
sortie.write_text(html, encoding="utf-8")
print("ecrit", sortie, len(html))
