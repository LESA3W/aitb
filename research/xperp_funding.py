# research/xperp_funding.py
"""
Le financement des X-PERPS europeens, les SEULS produits que Paul peut trader.

Pourquoi ce script existe
-------------------------
J'ai mesure 3,84 %/an d'arbitrage de financement sur les perpetuels GLOBAUX
d'OKX (BTC-USDT-SWAP et compagnie). Or ces produits ne sont pas accessibles a un
resident francais : depuis MiCA, les Europeens passent par OKX Europe Markets
Limited, sous licence MiFID II, qui propose des **X-Perps** — des futures a
echeance 2031, concus pour se comporter comme des perpetuels.

C'est exactement le piege qui m'a fait perdre du temps avec Binance : mesurer
sur un marche inaccessible. Il faut donc refaire la mesure sur le vrai produit.

Bonne nouvelle verifiee avant d'ecrire ce script :
  - les X-Perps ont bien un taux de financement (formulaType "withRate"),
    verse toutes les 8 heures
  - le contrat BTC vaut 0,0001 BTC, soit environ 7,70 USD, contre 773 USD sur
    le perp global. Cent fois plus petit, donc parfaitement dimensionne pour un
    compte de 500 EUR.

Ce que le script mesure
-----------------------
Pour chaque X-Perp : profondeur d'historique, taux annualise, part de periodes
positives, pire versement. Et surtout le NOTIONNEL MINIMUM, parce que c'est lui
qui decide de ce qui est reellement tradable avec 500 EUR.

Usage:
    python research/xperp_funding.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                          # noqa: E402

from aitb.okx import _get                                   # noqa: E402

CACHE = PROJECT_ROOT / "data" / "funding"
CAPITAL_EUR = 500.0
CAPITAL_USD = CAPITAL_EUR * 1.09


def xperps():
    """Tous les X-Perps vivants, avec leurs specifications."""
    data = _get("/api/v5/public/instruments?instType=FUTURES")
    out = []
    for d in data:
        if "XPERP" not in d.get("instId", "").upper():
            continue
        if d.get("state") != "live":
            continue
        out.append({
            "inst": d["instId"],
            "base": d["instId"].split("-")[0],
            "ctVal": float(d.get("ctVal") or 0),
            "ctValCcy": d.get("ctValCcy"),
            "minSz": float(d.get("minSz") or 1),
            "lever": d.get("lever"),
            "settle": d.get("settleCcy"),
        })
    return out


def financement(inst, pages=40):
    vus, curseur = [], None
    for _ in range(pages):
        c = f"/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if curseur:
            c += f"&after={curseur}"
        try:
            rows = _get(c)
        except Exception:
            break
        if not rows:
            break
        vus += rows
        curseur = rows[-1]["fundingTime"]
        time.sleep(0.06)
    return sorted((int(r["fundingTime"]), float(r["fundingRate"])) for r in vus)


def prix(inst):
    """None si l'instrument n'a jamais traite : OKX renvoie alors une chaine vide."""
    try:
        d = _get(f"/api/v5/market/ticker?instId={inst}")
    except Exception:
        return None
    if not d:
        return None
    v = d[0].get("last") or d[0].get("markPx") or ""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def spots_disponibles():
    """
    Paires SPOT vivantes, indexees par actif de base.

    C'est LE filtre qui decide. Un arbitrage de financement a besoin de DEUX
    jambes : on achete l'actif au comptant et on vend le contrat. Sans marche
    spot, la position n'est pas neutre, c'est un pari directionnel deguise.

    Consequence immediate : tous les X-Perps sur ACTIONS (SPY, GOOGL, SOFTBANK,
    SOXL, HOOD, EWY...) sont inarbitrables. OKX ne vend pas d'actions au
    comptant, seulement leur derive. Ils sortent tous, quel que soit leur
    financement.
    """
    out = {}
    for d in _get("/api/v5/public/instruments?instType=SPOT"):
        if d.get("state") != "live":
            continue
        out.setdefault(d.get("baseCcy"), []).append(d["instId"])
    return out


def main():
    lot = xperps()
    print("=" * 104)
    print(f"  FINANCEMENT DES X-PERPS EUROPEENS  ({len(lot)} instruments vivants)")
    print(f"  les seuls produits a effet de levier accessibles depuis la France")
    print("=" * 104)
    print("  %-26s %6s %6s %10s %11s %10s %11s %12s"
          % ("instrument", "pts", "jours", "annuel", "% positifs", "pire 8h",
             "1 contrat", "tenable 500E"))
    print("  " + "-" * 100)

    spots = spots_disponibles()
    print("  %d actifs disponibles au comptant chez OKX" % len(spots))
    print()

    res = []
    for x in lot:
        # Filtre eliminatoire : sans jambe spot, pas de couverture possible.
        if x["base"] not in spots:
            continue
        h = financement(x["inst"])
        if len(h) < 30:
            continue
        tx = np.array([t for _, t in h])
        jours = (h[-1][0] - h[0][0]) / 86400000.0
        if jours < 5:
            continue
        par_jour = len(tx) / jours
        ann = float(tx.mean() * par_jour * 365 * 100)
        p = prix(x["inst"])
        if not p:
            continue
        notionnel = x["ctVal"] * max(x["minSz"], 1) * p
        # une position neutre = spot + marge du short. On compte 2x, prudent,
        # parce que la marge n'est pas forcement fongible entre l'entite MiCA
        # (spot) et l'entite MiFID (derives) chez OKX Europe.
        requis = notionnel * 2
        tenable = requis <= CAPITAL_USD
        res.append({**x, "jours": jours, "annuel": ann, "prix": p,
                    "spot": spots[x["base"]][0],
                    "notionnel": notionnel, "requis": requis,
                    "pct_pos": float((tx > 0).mean() * 100),
                    "pire": float(tx.min() * 100), "pts": len(tx),
                    "tenable": tenable})
        print("  %-26s %6d %6.0f %9.2f%% %10.1f%% %9.3f%% %10.2f$ %11s"
              % (x["inst"][:26], len(tx), jours, ann,
                 (tx > 0).mean() * 100, tx.min() * 100, notionnel,
                 "oui" if tenable else "non"), flush=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "xperp.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    ok = [r for r in res if r["tenable"] and r["pct_pos"] >= 60 and r["annuel"] >= 2]
    print("\n" + "=" * 104)
    print(f"  {len(res)} instruments mesures, {len(ok)} tenables avec {CAPITAL_EUR:.0f} EUR "
          f"ET de qualite suffisante")
    print("=" * 104)
    if ok:
        ok.sort(key=lambda r: -r["annuel"])
        a = np.array([r["annuel"] for r in ok])
        print("  %-26s %10s %12s %11s" % ("instrument", "annuel", "% positifs", "1 contrat"))
        for r in ok[:15]:
            print("  %-26s %9.2f%% %11.1f%% %10.2f$"
                  % (r["inst"][:26], r["annuel"], r["pct_pos"], r["notionnel"]))
        print(f"\n  rendement moyen {a.mean():.2f}%/an brut")
        print(f"  historique disponible : {max(r['jours'] for r in ok):.0f} jours maximum")
    else:
        print("  AUCUN instrument tenable et de qualite. Design a revoir.")
    print(f"\n  Detail : {CACHE / 'xperp.json'}")


if __name__ == "__main__":
    main()
