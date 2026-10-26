"""Recensement de cout et de liquidite. Phase 0 du plan TRADING BOT.

Pourquoi ce fichier existe
--------------------------
TheMacroTape, note "exploiter-un-petit-capital" : avec un petit capital l'avantage
est sur les marches PEU liquides, la ou les fonds n'ont aucun interet a venir. Mais
la contrepartie est mecanique : le spread et le slippage y sont bien plus eleves, et
"l'avantage ne survit que si tes couts restent en dessous de ton edge".

La note dit donc de mesurer le cout AVANT de chercher un edge. C'est ce que fait ce
module, et c'est une PORTE : un instrument dont le cout depasse le seuil est ecarte
avant qu'une seule strategie ne soit testee sur lui.

Ce qu'on mesure, et avec quoi
-----------------------------
1. SPREAD REEL, quand le cache le porte. Les fichiers issus de MT5 ont une colonne
   `spread` en points, renseignee sur 28 a 100 % des barres selon l'instrument.
   Une mesure vaut mieux que n'importe quel estimateur, donc elle est prioritaire.

2. SPREAD ESTIME par Corwin-Schultz, a partir des seuls high/low journaliers.
   Necessaire pour la crypto et les ETF, dont le cache ne porte aucun spread.

3. CALIBRATION. L'estimateur est compare au spread reel sur les instruments ou les
   DEUX existent. Sans ce controle on transporterait une erreur inconnue sur tout le
   reste du recensement. Si l'ecart est grossier, l'estimateur est a jeter.

4. VOLUME en monnaie et ILLIQUIDITE D'AMIHUD, le filtre de volume que la note reclame.

5. COUT EN R. C'est la seule unite qui compte : un spread de 0,3 % n'a aucun sens dans
   l'absolu, il a un sens rapporte a la distance du stop. Un cout de 0,5 R veut dire
   que la moitie du risque part en frais avant meme que le trade commence.

Usage
-----
    python research/liquidite.py                 tout le cache
    python research/liquidite.py --classe etf    un seul perimetre
    python research/liquidite.py --seuil 0.3     autre seuil d'acceptation
"""

import argparse
import csv
import json
import math
import pathlib
import sys

import numpy as np

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

HISTORIQUE = RACINE / "data" / "history"
SORTIE = RACINE / "data" / "research" / "liquidite.json"

# Distance de stop de reference, en multiples d'ATR. 2.5 est la valeur qui revient
# le plus souvent dans le book existant, donc le cout en R est comparable a ce que
# le projet a deja mesure ailleurs.
SL_ATR = 2.5
PERIODE_ATR = 14

# Au-dela de ce cout, l'instrument est ecarte : il faudrait un edge enorme rien que
# pour rembourser les frais. Seuil pose AVANT de mesurer, conformement au plan.
SEUIL_R = 0.5

# Contexte du compte vise. Le risque est un MONTANT FIXE et non un pourcentage,
# conformement a la note "comprendre-et-maitriser-la-volatilite" : le drag vient de
# l'acte de composer, et risquer une somme constante le fait disparaitre.
CAPITAL = 500.0
RISQUE_EUR = 10.0
COMMISSION_ORDRE = 1.0

# Mesure du 2026-09-16 : sur un compte de 500 EUR le spread n'est PAS le cout qui
# mord. Avec un stop a 2,5 x ATR, le spread d'une paire exotique pese 0,02 R, alors
# qu'une commission de 1 EUR par ordre sur une position de 400 EUR pese 0,20 R, dix
# fois plus. Un recensement qui ne regarde que le spread rate donc le vrai mur.
# Les comptes forex a spread seul ne paient pas cette commission, les courtiers
# actions et ETF la facturent presque toujours : les deux colonnes sont donc
# rapportees separement au lieu d'etre melangees.
CLASSES_COMMISSION = ("etf", "indice")

CRYPTO = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "NEAR", "LINK", "DOT",
          "MATIC", "LTC", "BNB", "TRX", "ATOM", "ALGO", "FIL", "APT", "ARB", "OP",
          "PEPE", "SHIB", "UNI", "ICP", "ETC", "XLM", "HBAR", "VET", "INJ", "SUI",
          "SEI", "TIA", "RUNE", "AAVE", "GRT", "SAND", "MANA", "EOS", "FTM", "THETA",
          "AXS", "CRV", "LDO", "IMX", "STX", "KAS", "MKR", "ENA", "BLUR", "DASH",
          "BCH", "FET", "ASTER", "CRCLB", "ETHFI")

DEVISES = ("EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK",
           "SGD", "HKD", "TRY", "ZAR", "MXN", "PLN", "HUF", "CZK", "DKK", "CNH")


def classer(symbole):
    """Range un symbole dans un perimetre. Sert a comparer les trois familles."""
    base = symbole.split(".")[0].upper()
    if any(base.startswith(c) and "USD" in base for c in CRYPTO):
        return "crypto"
    if base.startswith(("XAU", "XAG", "XPT", "XPD")):
        return "metaux"
    if len(base) == 6 and base[:3] in DEVISES and base[3:] in DEVISES:
        # Une majeure n'est PAS un marche peu liquide. On les separe pour que la
        # comparaison porte sur le bon terrain.
        majeures = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
                    "NZDUSD", "EURJPY", "EURGBP", "GBPJPY"}
        return "forex majeur" if base in majeures else "forex exotique"
    if "." in symbole:
        return "etf"
    return "indice"


def charger(chemin):
    """Lit un CSV du cache. Renvoie None si la serie est inexploitable."""
    o = h = l = c = v = s = None
    try:
        with chemin.open(encoding="utf-8") as f:
            lignes = list(csv.DictReader(f))
    except Exception:
        return None
    if len(lignes) < 250:
        return None

    def col(nom):
        out = np.empty(len(lignes))
        for i, r in enumerate(lignes):
            try:
                out[i] = float(r.get(nom) or 0.0)
            except Exception:
                out[i] = 0.0
        return out

    t = col("time")
    o, h, l, c = col("open"), col("high"), col("low"), col("close")
    tick = col("tick_volume")
    reel = col("real_volume")
    spread = col("spread")
    # real_volume quand il existe, sinon tick_volume. Le premier est un vrai volume
    # echange, le second un simple compte de ticks : ils ne sont pas comparables
    # entre eux, donc on note lequel a servi.
    if (reel > 0).mean() > 0.5:
        volume, origine_vol = reel, "real"
    else:
        volume, origine_vol = tick, "tick"

    bon = (o > 0) & (h > 0) & (l > 0) & (c > 0) & (h >= l)
    if bon.sum() < 250:
        return None
    return {"t": t[bon], "o": o[bon], "h": h[bon], "l": l[bon], "c": c[bon],
            "vol": volume[bon], "spread": spread[bon], "origine_vol": origine_vol}


def corwin_schultz(h, l):
    """Spread effectif estime a partir des seuls high/low de deux jours consecutifs.

    Estimateur publie par Corwin et Schultz (2012). L'idee : sur deux jours, la
    partie du range due a la volatilite croit avec le temps, celle due au spread non.
    Comparer le range d'un jour a celui de deux jours separe donc les deux.

    PIEGE MESURE LE 2026-09-16, ET LA RAISON DE CETTE SIGNATURE A TROIS VALEURS
    --------------------------------------------------------------------------
    La premiere version ecretait les estimations negatives a zero puis prenait la
    mediane. Sur les ETF, 56 a 60 % des estimations sont negatives : la mediane
    tombait donc a exactement 0,0000 % et le recensement annoncait des ETF GRATUITS.

    Une estimation negative ne veut pas dire "spread nul", elle veut dire que le
    modele ne tient pas sur cette fenetre. Quand elles sont majoritaires,
    l'estimateur ne dit pas zero, il ne dit RIEN, et le module doit repondre
    "inconnu" au lieu de fabriquer un chiffre flatteur.

    Renvoie (estimation, part_negative, borne_haute) en fraction du prix.
      estimation   : mediane des estimations valides, ou nan si trop de negatives
      part_negative: fraction d'estimations negatives, l'indicateur de fiabilite
      borne_haute  : mediane des seules estimations positives, utilisable comme
                     majorant pessimiste quand aucune mesure reelle n'existe
    """
    rate = (float("nan"), float("nan"), float("nan"))
    if len(h) < 3:
        return rate
    h1, l1 = h[:-1], l[:-1]
    h2, l2 = h[1:], l[1:]
    ok = (h1 > 0) & (l1 > 0) & (h2 > 0) & (l2 > 0)
    if ok.sum() < 50:
        return rate
    h1, l1, h2, l2 = h1[ok], l1[ok], h2[ok], l2[ok]

    beta = np.log(h1 / l1) ** 2 + np.log(h2 / l2) ** 2
    hh = np.maximum(h1, h2)
    ll = np.minimum(l1, l2)
    gamma = np.log(hh / ll) ** 2

    k = 3.0 - 2.0 * math.sqrt(2.0)
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    s = s[np.isfinite(s)]
    if len(s) < 50:
        return rate

    part_neg = float((s < 0).mean())
    positives = s[s > 0]
    borne = float(np.median(positives)) if len(positives) >= 20 else float("nan")
    # Au-dela de la moitie d'estimations negatives, le modele ne tient pas sur cette
    # serie. On refuse de produire un chiffre plutot que d'en produire un faux.
    estimation = float(np.median(np.where(s < 0, 0.0, s))) if part_neg <= 0.50 else float("nan")
    return estimation, part_neg, borne


def atr_relatif(h, l, c):
    """ATR moyen exprime en fraction du prix. Sert a convertir un cout en R."""
    if len(c) < PERIODE_ATR + 2:
        return float("nan")
    hl = h[1:] - l[1:]
    hc = np.abs(h[1:] - c[:-1])
    lc = np.abs(l[1:] - c[:-1])
    tr = np.maximum(hl, np.maximum(hc, lc))
    # Moyenne glissante simple, suffisante pour une mesure de cout.
    if len(tr) < PERIODE_ATR:
        return float("nan")
    noyau = np.ones(PERIODE_ATR) / PERIODE_ATR
    atr = np.convolve(tr, noyau, mode="valid")
    prix = c[PERIODE_ATR:]
    n = min(len(atr), len(prix))
    if n < 50:
        return float("nan")
    rel = atr[:n] / prix[:n]
    return float(np.nanmedian(rel))


def amihud(c, vol, prix_med):
    """Illiquidite d'Amihud : impact prix par unite de volume echange.

    Plus le chiffre est grand, plus le prix bouge pour un volume donne, donc moins
    le marche est liquide. L'echelle depend de l'unite de volume, donc ce chiffre ne
    se compare qu'entre instruments de meme origine de volume.
    """
    if len(c) < 60:
        return float("nan"), float("nan")
    r = np.abs(np.diff(c) / c[:-1])
    v_mon = vol[1:] * c[1:]
    ok = (v_mon > 0) & np.isfinite(r)
    if ok.sum() < 50:
        return float("nan"), float("nan")
    a = float(np.nanmedian(r[ok] / v_mon[ok]) * 1e6)
    return a, float(np.nanmedian(v_mon[ok]))


def recenser(chemin, point_par_defaut=None):
    """Mesure un instrument. Renvoie un dictionnaire, ou None si inexploitable."""
    tige = chemin.stem
    if "_" not in tige:
        return None
    symbole, tf = tige.rsplit("_", 1)
    if tf != "D1":
        return None
    d = charger(chemin)
    if d is None:
        return None

    h, l, c, t = d["h"], d["l"], d["c"], d["t"]
    ans = (t[-1] - t[0]) / 86400 / 365.25
    prix_med = float(np.median(c))

    # Spread reel : la colonne est en POINTS. On lit le point dans le meta quand il
    # existe, sinon on le deduit du nombre de decimales du prix, ce qui est fragile
    # et doit etre signale.
    meta = chemin.with_suffix("").with_suffix("")
    meta = chemin.parent / (chemin.stem + ".meta.json")
    point = None
    if meta.exists():
        try:
            point = float(json.loads(meta.read_text(encoding="utf-8")).get("point") or 0) or None
        except Exception:
            point = None
    devine_point = point is None
    if point is None:
        point = point_par_defaut or (10 ** -(len(str(c[0]).split(".")[-1])))

    sp = d["spread"]
    nz = sp[sp > 0]
    couv = float((sp > 0).mean())
    # Une colonne qui ne porte qu'UNE valeur distincte sur des milliers de barres
    # n'est pas une mesure, c'est une constante ecrite par le collecteur. Mesure du
    # 2026-09-16 : tous les fichiers ETF portent spread=2 sur 100 % des barres,
    # quand EURUSD en a 18 valeurs distinctes et AUDZAR 58. Sans ce controle le
    # recensement annonçait un cout ETF median de 0,0374 R entierement invente.
    valeurs = int(len(np.unique(nz))) if len(nz) else 0
    fabrique = bool(valeurs <= 2 and len(nz) > 200)
    spread_reel = (float(np.median(nz) * point / prix_med)
                   if (couv > 0.05 and not fabrique) else float("nan"))

    spread_est, part_neg, borne_haute = corwin_schultz(h, l)
    atr_rel = atr_relatif(h, l, c)
    ami, vol_mon = amihud(c, d["vol"], prix_med)

    # Cout aller-retour : on paie le spread a l'entree et a la sortie.
    # Ordre de preference : mesure reelle, puis estimation si elle tient, puis
    # majorant pessimiste, puis aveu d'ignorance. Jamais zero par defaut.
    if not math.isnan(spread_reel):
        retenu, source = spread_reel, "mesure"
    elif not math.isnan(spread_est):
        retenu, source = spread_est, "estime"
    elif not math.isnan(borne_haute):
        retenu, source = borne_haute, "majorant"
    else:
        retenu, source = float("nan"), "inconnu"
    cout_pct = retenu * 2.0 if not math.isnan(retenu) else float("nan")
    stop_pct = SL_ATR * atr_rel if not math.isnan(atr_rel) else float("nan")
    cout_r = cout_pct / stop_pct if (stop_pct and not math.isnan(stop_pct)
                                     and not math.isnan(cout_pct)) else float("nan")

    # Notionnel implique par un risque monetaire FIXE : si on accepte de perdre
    # RISQUE_EUR et que le stop est a stop_pct du prix, la position vaut
    # RISQUE_EUR / stop_pct. C'est ce montant qui decide du poids d'une commission
    # forfaitaire, et c'est lui qui a tue le test ETF de ce matin.
    classe = classer(symbole)
    notionnel = (RISQUE_EUR / stop_pct) if (stop_pct and not math.isnan(stop_pct)) else float("nan")
    paie_commission = classe in CLASSES_COMMISSION
    cout_comm_r = (2.0 * COMMISSION_ORDRE / RISQUE_EUR) if paie_commission else 0.0
    cout_total_r = (cout_r + cout_comm_r) if not math.isnan(cout_r) else float("nan")

    return {
        "symbole": symbole, "classe": classer(symbole), "barres": int(len(c)),
        "ans": round(ans, 2), "prix_median": round(prix_med, 4),
        "spread_reel_pct": None if math.isnan(spread_reel) else round(spread_reel * 100, 4),
        "spread_estime_pct": None if math.isnan(spread_est) else round(spread_est * 100, 4),
        "borne_haute_pct": None if math.isnan(borne_haute) else round(borne_haute * 100, 4),
        "part_negative_cs": None if math.isnan(part_neg) else round(part_neg, 3),
        "couverture_spread": round(couv, 3),
        "point_devine": devine_point,
        "source_cout": source,
        "atr_pct": None if math.isnan(atr_rel) else round(atr_rel * 100, 3),
        "stop_pct": None if math.isnan(stop_pct) else round(stop_pct * 100, 3),
        "cout_aller_retour_pct": None if math.isnan(cout_pct) else round(cout_pct * 100, 4),
        "cout_en_R": None if math.isnan(cout_r) else round(cout_r, 4),
        "valeurs_spread": valeurs,
        "spread_fabrique": fabrique,
        "notionnel_eur": None if math.isnan(notionnel) else round(notionnel, 1),
        "cout_commission_R": round(cout_comm_r, 4),
        "cout_total_R": None if math.isnan(cout_total_r) else round(cout_total_r, 4),
        "volume_monnaie_median": None if math.isnan(vol_mon) else round(vol_mon, 1),
        "origine_volume": d["origine_vol"],
        "amihud": None if math.isnan(ami) else round(ami, 6),
    }


def calibrer(lignes):
    """Compare l'estimateur au spread reel la ou les deux existent.

    Sans ce controle, on transporterait une erreur inconnue sur tous les instruments
    qui n'ont pas de spread mesure, c'est-a-dire la crypto et les ETF.
    """
    paires = [(x["spread_reel_pct"], x["spread_estime_pct"], x["symbole"])
              for x in lignes
              if x["spread_reel_pct"] and x["spread_estime_pct"]
              and not x["point_devine"]]
    if len(paires) < 10:
        return None
    reel = np.array([p[0] for p in paires])
    est = np.array([p[1] for p in paires])
    rapport = est / np.where(reel > 0, reel, np.nan)
    return {
        "n": len(paires),
        "correlation": float(np.corrcoef(reel, est)[0, 1]),
        "rapport_median": float(np.nanmedian(rapport)),
        "rapport_p10": float(np.nanpercentile(rapport, 10)),
        "rapport_p90": float(np.nanpercentile(rapport, 90)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classe", default=None, help="limiter a un perimetre")
    ap.add_argument("--seuil", type=float, default=SEUIL_R)
    ap.add_argument("--min-ans", type=float, default=0.0)
    args = ap.parse_args()

    fichiers = sorted(p for p in HISTORIQUE.glob("*_D1.csv"))
    print("   RECENSEMENT DE COUT ET DE LIQUIDITE")
    print("   %d fichiers D1 dans le cache" % len(fichiers))
    print("   stop de reference %.1f x ATR%d, seuil d'acceptation %.2f R"
          % (SL_ATR, PERIODE_ATR, args.seuil))
    print()

    lignes = []
    for p in fichiers:
        try:
            r = recenser(p)
        except Exception:
            r = None
        if r:
            lignes.append(r)
    print("   %d series exploitables" % len(lignes))

    cal = calibrer(lignes)
    print()
    print("   CALIBRATION DE L'ESTIMATEUR CORWIN-SCHULTZ")
    if cal is None:
        print("   impossible : pas assez d'instruments avec spread mesure ET estime")
    else:
        print("   %d instruments ont les deux mesures" % cal["n"])
        print("   correlation reel/estime : %.3f" % cal["correlation"])
        print("   rapport estime/reel : median %.2f  (p10 %.2f, p90 %.2f)"
              % (cal["rapport_median"], cal["rapport_p10"], cal["rapport_p90"]))
        if cal["correlation"] < 0.3:
            print("   ATTENTION : correlation faible, l'estimateur ne suit pas le reel.")
            print("   Ne pas l'utiliser pour ecarter un instrument sans spread mesure.")
    print()

    par_classe = {}
    for x in lignes:
        par_classe.setdefault(x["classe"], []).append(x)

    print("   SYNTHESE PAR PERIMETRE")
    print("   Le cout n'est retenu que s'il est MESURE. Un cout estime ou inconnu")
    print("   ne peut pas servir a retenir un instrument, seulement a l'ecarter.")
    print()
    print("   %-16s %7s %7s %8s %8s %11s %11s %9s %9s"
          % ("perimetre", "series", "mesure", "fabrique", "inconnu",
             "spread R med", "total R med", "retenus", ">=10 ans"))
    print("   " + "-" * 98)
    for cl in sorted(par_classe):
        v = par_classe[cl]
        nmes = sum(1 for x in v if x["source_cout"] == "mesure")
        nfab = sum(1 for x in v if x.get("spread_fabrique"))
        ninc = sum(1 for x in v if x["source_cout"] in ("inconnu", "estime", "majorant"))
        sp = [x["cout_en_R"] for x in v
              if x["source_cout"] == "mesure" and x["cout_en_R"] is not None]
        tot = [x["cout_total_R"] for x in v
               if x["source_cout"] == "mesure" and x["cout_total_R"] is not None]
        anns = [x["ans"] for x in v]
        ok = [x for x in v
              if x["source_cout"] == "mesure" and x["cout_total_R"] is not None
              and x["cout_total_R"] <= args.seuil and x["ans"] >= 10]
        print("   %-16s %7d %7d %8d %8d %11s %11s %9d %9d"
              % (cl, len(v), nmes, nfab, ninc,
                 ("%.4f" % float(np.median(sp))) if sp else "n/a",
                 ("%.4f" % float(np.median(tot))) if tot else "n/a",
                 len(ok), sum(1 for x in v if x["ans"] >= 10)))
    print()
    print("   spread R = cout du spread seul.  total R = spread + commission par ordre")
    print("   sur les classes qui en paient une (%s), a %.0f EUR de risque fixe."
          % (", ".join(CLASSES_COMMISSION), RISQUE_EUR))
    print()
    sans = [x for x in lignes if x["source_cout"] != "mesure"]
    if sans:
        print("   %d instruments n'ont AUCUN spread mesure dans le cache." % len(sans))
        print("   Leur cout reel devra venir du carnet d'un courtier, pas d'un estimateur.")
    print()

    # Ce qu'on cherche n'est pas le moins cher, c'est le PLUS ILLIQUIDE parmi ceux
    # dont le cout reste finançable. C'est exactement la these de la note
    # "exploiter-un-petit-capital" : aller ou les fonds ne viennent pas, mais
    # seulement tant que les couts restent sous l'edge.
    # L'illiquidite se lit dans le SPREAD RELATIF, pas dans Amihud. Amihud divise par
    # volume x prix, or le cache ne porte qu'un compte de ticks : multiplie par le
    # prix, il mesure surtout le niveau de cotation. Premiere version de ce tableau :
    # il classait EURUSD et AUDUSD en tete des "plus illiquides", ce qui est absurde.
    eligibles = [x for x in lignes
                 if x["source_cout"] == "mesure" and x["cout_total_R"] is not None
                 and x["cout_total_R"] <= args.seuil
                 and x["ans"] >= max(args.min_ans, 10.0)]
    eligibles.sort(key=lambda x: -x["cout_aller_retour_pct"])
    print("   CIBLE : les plus ILLIQUIDES parmi ceux dont le cout reste finançable")
    print("   (spread mesure, cout total <= %.2f R, au moins 10 ans)" % args.seuil)
    print("   %-14s %-15s %6s %10s %9s %9s %11s"
          % ("symbole", "perimetre", "ans", "spread%", "spread R", "total R", "notionnel"))
    print("   " + "-" * 84)
    for x in eligibles[:20]:
        print("   %-14s %-15s %6.1f %9.4f%% %9.4f %9.4f %10.0f E"
              % (x["symbole"], x["classe"], x["ans"], x["cout_aller_retour_pct"],
                 x["cout_en_R"], x["cout_total_R"], x["notionnel_eur"] or 0))
    print()
    chers = [x for x in lignes
             if x["source_cout"] == "mesure" and x["cout_total_R"] is not None
             and x["cout_total_R"] > args.seuil]
    chers.sort(key=lambda x: -x["cout_total_R"])
    if chers:
        print("   ECARTES PAR LA PORTE : cout mesure superieur a %.2f R" % args.seuil)
        print("   %-14s %-15s %6s %8s %10s" % ("symbole", "perimetre", "ans", "cout R", "spread%"))
        print("   " + "-" * 58)
        for x in chers[:12]:
            print("   %-14s %-15s %6.1f %8.4f %10.4f"
                  % (x["symbole"], x["classe"], x["ans"], x["cout_en_R"],
                     x["cout_aller_retour_pct"]))
        print("   total ecarte : %d instruments" % len(chers))
    print()

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps(
        {"seuil_r": args.seuil, "sl_atr": SL_ATR, "calibration": cal,
         "instruments": lignes}, indent=1), encoding="utf-8")
    print("   detail ecrit dans data/research/liquidite.json")


if __name__ == "__main__":
    main()
