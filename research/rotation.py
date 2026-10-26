"""Momentum transversal sur l'univers ETF valide.

Mecanique volontairement differente du suivi de tendance, qui a echoue sur
0 symbole sur 23. Ici on ne demande pas "cet actif monte-t-il", on demande
"lequel monte le plus", et on detient toujours les K meilleurs. La strategie
reste investie en permanence, donc elle conserve la prime de risque au lieu
de la rater.

Protocole impose avant de lancer :
  - la combinaison est choisie sur la periode d'entrainement SEULE
  - la periode de test n'est lue qu'une fois, pour la combinaison retenue
  - les criteres d'acceptation sont ecrits dans ACCEPTATION ci-dessous
  - parts entieres, spread et commission toujours comptes

Donnees : les series *.TR2 produites par la reconstruction stricte, qui
valide la geometrie de chaque bougie. Ne jamais pointer ce module vers les
anciennes series .EUR ou .TREUR, elles contiennent des barres a open=0.
"""

import json
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aitb import market_data as md

RACINE = pathlib.Path(__file__).resolve().parents[1]
UNIVERS = RACINE / "data" / "research" / "univers_tr2.json"

# Criteres poses AVANT de mesurer. Ne pas les deplacer apres coup.
ACCEPTATION = {
    "ratio_mini": 0.24,        # ratio rendement/drawdown de l'equipondere
    "ic95_bas_sur_capital": 1.0,
    "part_entrainement": 0.70,
}

SPREAD_PCT = 0.001    # 0,1 % aller-retour
COMMISSION = 1.0      # euros par ordre


def charger(exclure=("SEGA",)):
    """Charge les series validees et les aligne sur un calendrier commun."""
    univers = json.loads(UNIVERS.read_text(encoding="utf-8"))
    symboles = [s for s in univers["tickers_tr2"]
                if not s.startswith(tuple(exclure))]
    series = {}
    for s in symboles:
        try:
            rates, _ = md.load_history(s, "D1")
        except Exception:
            continue
        t = np.asarray(rates["time"], dtype="int64")
        c = np.asarray(rates["close"], dtype="float64")
        bon = c > 0
        series[s] = (t[bon], c[bon])
    if not series:
        raise SystemExit("aucune serie chargee")

    dates = np.array(sorted({d for t, _ in series.values() for d in t}))
    noms = sorted(series)
    prix = np.full((len(noms), len(dates)), np.nan)
    for i, s in enumerate(noms):
        t, c = series[s]
        pos = np.searchsorted(t, dates, side="right") - 1
        valide = pos >= 0
        prix[i, valide] = c[pos[valide]]
        # Avant la premiere cotation le prix reste NaN, donc l'actif est
        # simplement absent du classement. Pas de prix invente.
    return noms, dates, prix


def rotation(prix, dates, lookback, k, pas, capital):
    """Detient les k meilleurs sur `lookback` jours, rebalance tous les `pas`.

    Parts entieres. Une position non financable est reduite, pas empruntee.
    Retourne la courbe d'equite et le nombre d'ordres passes.
    """
    n_act, n_j = prix.shape
    cash = float(capital)
    parts = np.zeros(n_act)
    equite = np.full(n_j, np.nan)
    ordres = 0

    for j in range(n_j):
        p = prix[:, j]
        detenu = parts > 0
        valeur = float(np.nansum(np.where(detenu, parts * p, 0.0)))
        equite[j] = cash + valeur

        if j < lookback or (j - lookback) % pas != 0:
            continue

        passe = prix[:, j - lookback]
        perf = np.where((passe > 0) & (p > 0), p / passe - 1.0, np.nan)
        candidats = np.where(~np.isnan(perf))[0]
        if len(candidats) < k:
            continue
        # Un momentum negatif reste un signal transversal valide : on veut
        # le classement, pas un filtre de tendance qui nous sortirait du
        # marche et reproduirait l'echec du suivi de tendance.
        cible = candidats[np.argsort(perf[candidats])[-k:]]

        for i in np.where(parts > 0)[0]:
            if i in cible or not (p[i] > 0):
                continue
            brut = parts[i] * p[i]
            cash += brut * (1.0 - SPREAD_PCT / 2.0) - COMMISSION
            parts[i] = 0.0
            ordres += 1

        equite_courante = cash + float(np.nansum(
            np.where(parts > 0, parts * p, 0.0)))
        vise = equite_courante / k
        for i in cible:
            if not (p[i] > 0):
                continue
            actuel = parts[i] * p[i]
            if actuel >= vise:
                continue
            cout_unitaire = p[i] * (1.0 + SPREAD_PCT / 2.0)
            budget = min(vise - actuel, cash - COMMISSION)
            if budget <= cout_unitaire:
                continue
            q = math.floor(budget / cout_unitaire)
            if q < 1:
                continue
            cash -= q * cout_unitaire + COMMISSION
            parts[i] += q
            ordres += 1

    return equite, ordres


def mesures(equite, dates):
    """CAGR, drawdown maximal et ratio des deux."""
    bon = ~np.isnan(equite)
    e = equite[bon]
    d = dates[bon]
    if len(e) < 50 or e[0] <= 0:
        return None
    ans = (d[-1] - d[0]) / 86400 / 365.25
    if ans <= 0 or e[-1] <= 0:
        return {"cagr": -100.0, "dd": 100.0, "ratio": -1.0, "ans": ans,
                "final": float(e[-1])}
    cagr = ((e[-1] / e[0]) ** (1 / ans) - 1) * 100
    pic = np.maximum.accumulate(e)
    dd = float(((pic - e) / pic).max() * 100)
    return {"cagr": cagr, "dd": dd, "ratio": cagr / dd if dd > 0 else 0.0,
            "ans": ans, "final": float(e[-1])}


def equipondere(prix, dates, capital):
    """Achat-conservation equipondere, la reference a battre."""
    n_act, n_j = prix.shape
    debut = None
    for j in range(n_j):
        if (~np.isnan(prix[:, j])).sum() >= 5:
            debut = j
            break
    if debut is None:
        return None, None
    p0 = prix[:, debut]
    actifs = np.where(~np.isnan(p0) & (p0 > 0))[0]
    poids = capital / len(actifs)
    parts = poids / p0[actifs]
    eq = np.full(n_j, np.nan)
    for j in range(debut, n_j):
        p = prix[actifs, j]
        eq[j] = float(np.nansum(parts * np.where(np.isnan(p), p0[actifs], p)))
    return eq, dates
