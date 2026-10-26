# research/fetch_etf.py
"""
Collecteur d'historique ETF depuis Yahoo Finance, sans aucune dependance.

Pourquoi ce fichier
-------------------
market_data.py est entierement bati sur MetaTrader5 : chaque fonction appelle
mt5.copy_rates_*. Or les ETF UCITS ne sont pas chez un broker CFD, ils se
traitent en titres vifs. Il fallait donc une seconde source.

Choix techniques, et leurs raisons
----------------------------------
1. STDLIB UNIQUEMENT. urllib et json suffisent pour l'API chart de Yahoo.
   Ni yfinance, ni pandas, ni requests, qui sont tous absents du venv. Ajouter
   trois paquets pour telecharger du JSON serait de la surface d'attaque
   gratuite sur une machine qui tient un compte de trading.

2. FORMAT IDENTIQUE A MT5. On ecrit exactement les memes colonnes et le meme
   .meta.json que download_history(). Consequence : load_history(), le moteur
   de backtest, universal.py et montecarlo.py fonctionnent sur les ETF sans
   qu'une seule ligne soit modifiee ailleurs.

3. SPECIFICATIONS D'UNE ACTION, PAS D'UN CFD. Un ETF s'achete par PARTS
   ENTIERES, pas par lots :
       tick_size = tick_value = 0.01   un centime de variation sur une part
       volume_min = volume_step = 1    une part, indivisible
       contract_size = 1
   Mapping verifie le 2026-09-16 : le moteur sort bien des volumes entiers
   (2, 1, 4, 3, 10, 9 parts selon le capital et le risque), donc aucune
   fraction de part n'est simulee. La plupart des brokers europeens ne les
   proposent pas, les simuler fabriquerait un backtest inexecutable.

4. DIVIDENDES. L'API renvoie aussi adjclose, corrige des dividendes. On ecrit
   le prix BRUT dans close, parce que c'est lui qu'on paye et sur lequel les
   stops se declenchent. Le rendement du dividende est reel mais il n'appartient
   pas a la serie de prix, et le melanger fabriquerait un edge fantome sur les
   ETF distribuants.

Usage
-----
    python research/fetch_etf.py --univers
    python research/fetch_etf.py --tickers ESE.PA VUKE.L IUSA.MI
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CACHE = PROJECT_ROOT / "data" / "history"

# NE JAMAIS REVENIR A range=max, MEME AVEC interval=1d
# ----------------------------------------------------
# Premiere version de ce fichier : API avec range=max&interval=1d. Yahoo
# accepte la requete, renvoie 200, et RE-ECHANTILLONNE silencieusement en
# mensuel. Mesure du 2026-09-16 sur IUSA.MI :
#     range=max&interval=1d               226 barres,  12 par an, MENSUEL
#     period1=0&period2=now&interval=1d  4752 barres, 254 par an, journalier
# Meme ticker, meme jour, vingt fois moins de donnees, sans le moindre
# message d'erreur. Les 41 ETF avaient ete telecharges en mensuel et le
# defaut n'etait visible qu'en divisant les barres par les annees.
# Avec des bornes explicites, l'historique complet arrive en un seul appel,
# aucun fractionnement necessaire.
API = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=0&period2=%d&interval=1d"

# Univers criblé le 2026-09-16 : UCITS, au moins 10 ans d'historique, sur
# 11 classes d'actifs. 41 candidats sur 44 interroges.
#
# AUCUN FILTRE DE PRIX, ET C'EST UNE DECISION EXPLICITE
# -----------------------------------------------------
# Une premiere version de cette liste ecartait CSPX.L (820 USD la part),
# CNDX.L (1670 USD) et SXR8.DE (710 EUR) parce qu'une seule part depasse un
# capital de 500 EUR. C'etait une erreur de methode : cela revenait a laisser
# une contrainte de courtier decider du contenu de la strategie, alors que
# l'ordre voulu est l'inverse. La strategie se concoit sur ses merites, puis
# le courtier doit satisfaire ce qu'elle exige. Si elle a besoin de parts
# fractionnees, c'est une exigence imposee au courtier, pas un filtre.
# Consequence directe : les meilleurs historiques du marche reviennent dans
# l'univers, IEAC.L a 17,5 ans, IWDA a 17 ans, CSPX.L a 16 ans.
#
# DOUBLONS A TRAITER APRES TELECHARGEMENT, PAS ICI
# ------------------------------------------------
# Plusieurs lignes sont le MEME fonds cote sur plusieurs places :
# IWDA.AS = EUNL.DE = SWDA.MI, CSPX.L = SXR8.DE, IGLN.L = SGLN.L. D'autres
# sont des fonds differents avec une exposition identique : ISF.L et VUKE.L
# suivent tous deux le FTSE 100. Les garder fabriquerait une diversification
# fictive, le "portefeuille de clones" que build_book.py cherche justement a
# eviter. La deduplication se fera par CORRELATION MESUREE sur les series
# telechargees, pas en supposant ici lesquelles sont jumelles.
#
# PIEGE D'UNITE : ISF.L, SJPA.L, IEEM.L, IWDP.L et SGLN.L cotent en GBp
# (pence), donc cent fois la valeur en GBP. La devise est enregistree dans le
# meta, il faut la lire avant toute comparaison entre series.
UNIVERS = [
    ("CSPX.L",   "iShares Core S&P500"),
    ("SXR8.DE",  "iShares Core S&P500 Xetra"),
    ("IUSA.MI",  "iShares S&P500 Milan"),
    ("ESE.PA",   "BNP S&P500 EUR"),
    ("VUSA.L",   "Vanguard S&P500"),
    ("CNDX.L",   "iShares Nasdaq 100"),
    ("EXXT.DE",  "iShares Nasdaq 100 Xetra"),
    ("IWDA.AS",  "iShares Core MSCI World"),
    ("EUNL.DE",  "iShares Core MSCI World Xetra"),
    ("XDWD.DE",  "Xtrackers MSCI World"),
    ("SWDA.MI",  "iShares Core MSCI World Milan"),
    ("EXSA.DE",  "iShares STOXX Europe 600"),
    ("VERX.L",   "Vanguard FTSE Europe ex-UK"),
    ("CAC.PA",   "Amundi CAC 40"),
    ("EXS1.DE",  "iShares DAX"),
    ("VUKE.L",   "Vanguard FTSE 100"),
    ("ISF.L",    "iShares FTSE 100 (GBp)"),
    ("VMID.L",   "Vanguard FTSE 250"),
    ("IJPA.AS",  "iShares Japon"),
    ("SJPA.L",   "iShares Japon Londres (GBp)"),
    ("XDJP.DE",  "Xtrackers Japon"),
    ("EIMI.L",   "iShares emergents IMI"),
    ("VFEM.L",   "Vanguard emergents"),
    ("IEEM.L",   "iShares emergents (GBp)"),
    ("CSSMI.SW", "iShares Swiss Market Index"),
    ("IBTS.L",   "iShares Treasury 1-3 ans"),
    ("IBTM.L",   "iShares Treasury 7-10 ans"),
    ("IDTL.L",   "iShares Treasury 20 ans et plus"),
    ("SEGA.L",   "iShares gilts UK"),
    ("IEGA.L",   "iShares souverain euro"),
    ("EXHB.DE",  "iShares Bund"),
    ("IBCI.DE",  "iShares oblig indexees inflation"),
    ("INXG.L",   "iShares gilts indexes"),
    ("IEAC.L",   "iShares credit euro"),
    ("IHYG.L",   "iShares high yield euro"),
    ("SLXX.L",   "iShares credit sterling"),
    ("IGLN.L",   "iShares or physique"),
    ("SGLN.L",   "iShares or physique Londres (GBp)"),
    ("PHAU.L",   "WisdomTree or physique"),
    ("IWDP.L",   "iShares immobilier monde (GBp)"),
    ("IPRP.AS",  "iShares immobilier Europe"),
]


def _json(url, essais=3):
    for k in range(essais):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            time.sleep(1.5 * (k + 1))
        except Exception:
            time.sleep(1.5 * (k + 1))
    return None


def telecharger(ticker, libelle=""):
    """Ecrit <ticker>_D1.csv et son .meta.json. Renvoie (barres, annees) ou None."""
    # period2 = maintenant. period1 = 0 est fige dans le gabarit : Yahoo
    # renvoie alors tout l'historique disponible, en journalier.
    d = _json(API % (ticker, int(time.time())))
    if not d or not d.get("chart", {}).get("result"):
        print("   %-10s ECHEC : aucune donnee" % ticker)
        return None

    res = d["chart"]["result"][0]
    meta = res.get("meta", {})
    temps = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c = q.get("open"), q.get("high"), q.get("low"), q.get("close")
    v = q.get("volume") or [0] * len(temps)
    if not temps or not c:
        print("   %-10s ECHEC : serie vide" % ticker)
        return None

    CACHE.mkdir(parents=True, exist_ok=True)
    chemin = CACHE / ("%s_D1.csv" % ticker)
    n = 0
    premier = dernier = None
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close",
                    "tick_volume", "spread", "real_volume"])
        for i, t in enumerate(temps):
            # Une bougie incomplete se reconnait a un None dans OHLC. On la
            # jette : un backtest sur une bougie partielle voit un range faux.
            if None in (o[i], h[i], l[i], c[i]):
                continue
            w.writerow([int(t), "%.6f" % o[i], "%.6f" % h[i],
                        "%.6f" % l[i], "%.6f" % c[i],
                        int(v[i] or 0), 2, int(v[i] or 0)])
            premier = premier or int(t)
            dernier = int(t)
            n += 1

    # On controle la DENSITE, pas le nombre absolu de barres.
    #
    # La version precedente testait n < 200. Ce controle a laisse passer 25
    # series mensuelles en les declarant valides, et a rejete 16 autres avec
    # le message "pas assez de barres exploitables", ce qui ressemblait a une
    # indisponibilite de donnees alors que c'etait un probleme de granularite.
    # Un mauvais critere ne se contente pas de rater le defaut, il fabrique un
    # diagnostic faux et envoie sur une mauvaise piste.
    #
    # Une serie journaliere compte environ 250 seances par an. Sous 200, c'est
    # de l'hebdomadaire ou du mensuel, quoi qu'ait demande la requete.
    duree_ans = (dernier - premier) / 86400 / 365.25 if (premier and dernier) else 0
    densite = n / duree_ans if duree_ans > 0.5 else 0
    if densite < 200:
        chemin.unlink(missing_ok=True)
        print("   %-10s ECARTE : %d barres sur %.1f ans, soit %.0f par an. "
              "Ce n'est pas du journalier." % (ticker, n, duree_ans, densite))
        return None
    if n < 500:
        chemin.unlink(missing_ok=True)
        print("   %-10s ECARTE : %d barres seulement, historique trop court"
              % (ticker, n))
        return None

    (CACHE / ("%s_D1.meta.json" % ticker)).write_text(json.dumps({
        "symbol": ticker,
        "libelle": libelle,
        "digits": 2,
        "point": 0.01, "pip": 0.01,
        "tick_size": 0.01, "tick_value": 0.01,
        "contract_size": 1.0,
        # Une part, indivisible. Voir l'entete pour le detail.
        "volume_min": 1.0, "volume_max": 100000.0, "volume_step": 1.0,
        "spread_points": 2,
        "devise": meta.get("currency", "?"),
        "place": meta.get("fullExchangeName", "?"),
        "timeframe": "D1",
        "bars": n,
        "first_time": premier, "last_time": dernier,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "yahoo_etf",
    }, indent=2), encoding="utf-8")

    ans = (dernier - premier) / 86400 / 365.25
    print("   %-10s %-30s %6d barres  %5.1f ans  %s"
          % (ticker, libelle[:30], n, ans, meta.get("currency", "?")))
    return n, ans


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--univers", action="store_true", help="telecharge l'univers criblé")
    p.add_argument("--tickers", nargs="*", default=None)
    args = p.parse_args()

    cibles = ([(t, "") for t in args.tickers] if args.tickers
              else UNIVERS if args.univers else None)
    if not cibles:
        print("  python research/fetch_etf.py --univers")
        print("  python research/fetch_etf.py --tickers ESE.PA VUKE.L")
        return

    print("  TELECHARGEMENT ETF, source Yahoo, stdlib uniquement")
    print("  destination : %s" % CACHE)
    print()
    ok = 0
    total_ans = []
    for t, lib in cibles:
        r = telecharger(t, lib)
        if r:
            ok += 1
            total_ans.append(r[1])
        time.sleep(0.3)
    print()
    print("  %d/%d telecharges" % (ok, len(cibles)))
    if total_ans:
        total_ans.sort()
        print("  profondeur mediane %.1f ans, min %.1f, max %.1f"
              % (total_ans[len(total_ans) // 2], total_ans[0], total_ans[-1]))
        print()
        print("  Rappel de la grille TheMacroTape : 10 ans ET plus de 1000 trades.")
        print("  La profondeur est acquise, le nombre de trades reste a mesurer.")


if __name__ == "__main__":
    main()
