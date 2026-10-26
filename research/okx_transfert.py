# research/okx_transfert.py
"""
Le signal trouve sur Binance existe-t-il aussi chez OKX ?

Le probleme
-----------
Le signal du financement sur les matieres premieres tokenisees a ete mesure sur
Binance : 4 marches sur 8 significatifs a 8h, tous de meme signe, sur 2 ans.
Mais **Binance a ferme tous ses services aux residents francais le 1er juillet
2026**, faute de licence MiCA. Ce marche est donc juridiquement hors d'atteinte.

OKX, lui, est autorise (MiFID II Malte) et sert la France. Et il liste
exactement les memes instruments : XAU, XAG, CL, NG, XPT, XPD en perpetuel.

Mais un signal ne se transporte pas d'un exchange a l'autre par decret. Le taux
de financement depend de la formule de l'exchange, de ses participants, de sa
liquidite. Il faut le verifier.

La contrainte
-------------
OKX n'a lance ces perps que le 08/06/2026, soit 96 jours d'historique. A 8h de
pas, ca fait 289 points. C'est trop peu pour valider une strategie, mais assez
pour un premier verdict a l'horizon 8h, celui ou le signal Binance etait le plus
net.

On teste donc UNIQUEMENT 8h, avec fenetres disjointes, et on compare le groupe
matieres au groupe crypto sur le meme exchange et la meme periode. Le groupe
crypto sert de temoin : sur Binance il ne montrait rien, il ne doit rien montrer
ici non plus.

Usage:
    python research/okx_transfert.py
"""

import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                          # noqa: E402

OKX = "https://www.okx.com"
CACHE = PROJECT_ROOT / "data" / "funding"

MATIERES = ["XAU-USDT-SWAP", "XAG-USDT-SWAP", "CL-USDT-SWAP",
            "NG-USDT-SWAP", "XPT-USDT-SWAP", "XPD-USDT-SWAP"]
CRYPTO = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
          "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "BNB-USDT-SWAP"]


def _get(url, retries=3):
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aitb-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(1.2 * (k + 1))


def funding(inst):
    """Historique complet. Chez OKX, 'after' remonte dans le temps."""
    vus, curseur, pages = [], None, 0
    while pages < 60:
        u = f"{OKX}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if curseur:
            u += f"&after={curseur}"
        d = _get(u)
        rows = d.get("data", [])
        if not rows:
            break
        vus += rows
        curseur = rows[-1]["fundingTime"]
        pages += 1
        time.sleep(0.1)
    out = sorted((int(r["fundingTime"]), float(r["fundingRate"])) for r in vus)
    return out


def klines_1h(inst, depuis_ms):
    """Bougies 1h. history-candles remonte plus loin que candles."""
    vus, curseur, pages = {}, None, 0
    while pages < 60:
        u = f"{OKX}/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if curseur:
            u += f"&after={curseur}"
        d = _get(u)
        rows = d.get("data", [])
        if not rows:
            break
        for r in rows:
            vus[int(r[0]) // 3600000] = float(r[4])
        curseur = rows[-1][0]
        if int(curseur) < depuis_ms:
            break
        pages += 1
        time.sleep(0.1)
    return vus


def regression(y, X):
    n = len(y)
    A = np.column_stack([np.ones(n)] + list(X))
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    ddl = n - A.shape[1]
    if ddl <= 0:
        return beta, np.zeros_like(beta)
    s2 = float(resid @ resid) / ddl
    try:
        se = np.sqrt(np.clip(np.diag(s2 * np.linalg.inv(A.T @ A)), 1e-30, None))
        return beta, beta / se
    except np.linalg.LinAlgError:
        return beta, np.zeros_like(beta)


def analyser(inst, H=8):
    f = funding(inst)
    if len(f) < 100:
        return None
    prix = klines_1h(inst, f[0][0])
    if len(prix) < 500:
        return None
    pas_h = float(np.median(np.diff([t for t, _ in f])) / 3600000.0)
    k = max(1, int(round(H / pas_h)))       # fenetres disjointes

    y, xp, xf = [], [], []
    for i in range(0, len(f), k):
        ts, taux = f[i]
        h0 = ts // 3600000
        if h0 in prix and (h0 + H) in prix and (h0 - 24) in prix:
            y.append((prix[h0 + H] / prix[h0] - 1.0) * 100.0)
            xp.append((prix[h0] / prix[h0 - 24] - 1.0) * 100.0)
            xf.append(taux * 100.0)
    if len(y) < 60:
        return None
    y = np.array(y)
    _, t2 = regression(y, [np.array(xp), np.array(xf)])
    return {"inst": inst, "n": len(y), "pas_h": round(pas_h, 1),
            "t_fund": float(t2[2]), "t_prix": float(t2[1]),
            "debut": datetime.fromtimestamp(f[0][0] / 1000).strftime("%d/%m/%Y")}


def main():
    print("=" * 92)
    print("  LE SIGNAL BINANCE SE RETROUVE-T-IL CHEZ OKX ?")
    print("  horizon 8h, fenetres disjointes, financement controle par le prix")
    print("  OKX est accessible depuis la France ; Binance ne l'est plus depuis le 01/07/2026")
    print("=" * 92, flush=True)

    res = {"matiere": [], "crypto": []}
    for groupe, lot in (("matiere", MATIERES), ("crypto", CRYPTO)):
        for inst in lot:
            try:
                r = analyser(inst)
            except Exception as e:
                print("  %-18s %-8s echec %s" % (inst, groupe, str(e)[:34]), flush=True)
                continue
            if r is None:
                print("  %-18s %-8s historique insuffisant" % (inst, groupe), flush=True)
                continue
            r["groupe"] = groupe
            res[groupe].append(r)
            print("  %-18s %-8s n=%3d  pas %.1fh  depuis %s  t financement %+6.2f"
                  % (inst, groupe, r["n"], r["pas_h"], r["debut"], r["t_fund"]),
                  flush=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "okx_transfert.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    print("\n" + "=" * 92)
    print("  COMPARAISON DES GROUPES")
    print("=" * 92)
    print("  %-10s %9s %11s %10s %14s" % ("groupe", "marches", "t moyen", "|t|>2", "signe +"))
    print("  " + "-" * 60)
    for groupe in ("matiere", "crypto"):
        lot = res[groupe]
        if not lot:
            continue
        ts = [r["t_fund"] for r in lot]
        forts = [t for t in ts if abs(t) > 2]
        print("  %-10s %9d %11.2f %10d %14s"
              % (groupe, len(ts), float(np.mean(ts)), len(forts),
                 "%d/%d" % (sum(1 for t in forts if t > 0), len(forts)) if forts else "-"))

    print("\n  Rappel du resultat Binance a 8h : matieres t moyen 2.00 (4/8 significatifs,")
    print("  4/4 positifs), crypto t moyen 0.40 (0/6). Si OKX reproduit ce contraste,")
    print("  le signal se transporte et le marche est accessible legalement.")
    print("\n  ATTENTION : 96 jours d'historique seulement. Ce test est un premier")
    print("  indice, pas une validation. Il faudra plusieurs mois de donnees OKX.")


if __name__ == "__main__":
    main()
