# aitb/okx.py
"""
Client OKX. Partie publique uniquement pour l'instant.

Pourquoi OKX et pas Binance
---------------------------
Binance a coupe spot, marge et futures aux residents francais le 1er juillet
2026, faute de licence MiCA. OKX est autorise MiFID II via Malte et sert la
France, avec les perpetuels. C'est le seul exchange ou ce bot peut legalement
tourner depuis Bordeaux.

Toutes les routes utilisees ici sont PUBLIQUES : aucune cle API, aucun compte,
aucune limite de taux contraignante. Le bot peut donc tourner en mode papier
indefiniment sans que Paul ait ouvert quoi que ce soit.
"""

import json
import time
import urllib.request

BASE = "https://www.okx.com"


class OkxError(RuntimeError):
    pass


def _get(chemin: str, retries: int = 3):
    url = BASE + chemin
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aitb-bot"})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.loads(r.read().decode())
            if d.get("code") not in ("0", 0):
                raise OkxError(f"{chemin} -> code {d.get('code')} {d.get('msg')}")
            return d.get("data", [])
        except OkxError:
            raise
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(1.2 * (k + 1))


# ---------------------------------------------------------------- instruments

def perpetuels(quote: str = "USDT"):
    """Perpetuels lineaires disponibles, marges dans la devise 'quote'."""
    data = _get("/api/v5/public/instruments?instType=SWAP")
    return [d for d in data
            if d.get("settleCcy") == quote and d.get("state") == "live"]


def paires_spot(quote: str = "USDT"):
    data = _get("/api/v5/public/instruments?instType=SPOT")
    return {d["instId"] for d in data
            if d.get("quoteCcy") == quote and d.get("state") == "live"}


def arbitrables(quote: str = "USDT"):
    """
    Sous-jacents ou les DEUX jambes existent : le spot pour acheter, le perp
    pour vendre. Sans les deux, la position ne peut pas etre neutre, et une
    jambe seule serait un pari directionnel deguise.
    """
    spots = paires_spot(quote)
    out = []
    for p in perpetuels(quote):
        base = p["instId"].split("-")[0]
        spot = f"{base}-{quote}"
        if spot in spots:
            out.append({"base": base, "spot": spot, "perp": p["instId"],
                        "ctVal": float(p.get("ctVal") or 0),
                        "ctValCcy": p.get("ctValCcy"),
                        "lotSz": float(p.get("lotSz") or 0),
                        "minSz": float(p.get("minSz") or 0)})
    return out


# ---------------------------------------------------------------- financement

def financement_courant(inst_id: str):
    """Taux en cours et taux suivant, avec l'heure du prochain versement."""
    d = _get(f"/api/v5/public/funding-rate?instId={inst_id}")
    if not d:
        return None
    x = d[0]
    return {
        "inst": inst_id,
        "taux": float(x["fundingRate"]),
        "taux_suivant": float(x.get("nextFundingRate") or 0.0),
        "prochain_ms": int(x.get("nextFundingTime") or 0),
    }


def financement_historique(inst_id: str, pages: int = 60):
    """Historique complet. Chez OKX, 'after' remonte dans le temps."""
    vus, curseur = [], None
    for _ in range(pages):
        c = f"/api/v5/public/funding-rate-history?instId={inst_id}&limit=100"
        if curseur:
            c += f"&after={curseur}"
        rows = _get(c)
        if not rows:
            break
        vus += rows
        curseur = rows[-1]["fundingTime"]
        time.sleep(0.08)
    return sorted((int(r["fundingTime"]), float(r["fundingRate"])) for r in vus)


# ---------------------------------------------------------------- prix

def prix(inst_id: str):
    d = _get(f"/api/v5/market/ticker?instId={inst_id}")
    return float(d[0]["last"]) if d else None


def prix_multiples(inst_ids):
    """Un seul appel pour tous les tickers d'un type, puis filtrage local."""
    out = {}
    for typ in ("SPOT", "SWAP"):
        for d in _get(f"/api/v5/market/tickers?instType={typ}"):
            if d["instId"] in inst_ids:
                out[d["instId"]] = float(d["last"])
    return out


def carnet(inst_id: str, profondeur: int = 5):
    """Meilleures limites, pour estimer le slippage avant d'engager."""
    d = _get(f"/api/v5/market/books?instId={inst_id}&sz={profondeur}")
    if not d:
        return None
    b = d[0]
    return {"bids": [(float(p), float(q)) for p, q, *_ in b["bids"]],
            "asks": [(float(p), float(q)) for p, q, *_ in b["asks"]]}
