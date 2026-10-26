"""Momentum transversal sur l'univers FX illiquide. Autre classe de modele.

Pourquoi cette piste apres l'echec des familles techniques
----------------------------------------------------------
211 combinaisons de 12 familles techniques, testees sur 53 instruments illiquides
et sur 10 majeures, donnent un profit factor hors echantillon median de 0,836 et
0,803. Rien ne fonctionne, et la selection sur l'entrainement ne bat pas un tirage
au hasard. La note "pourquoi-la-technique-seule-ne-suffit-pas" le dit :
"complexifier un dessin ne le transforme pas en avantage statistique".

Le transversal pose une question DIFFERENTE. La technique demande "cette paire
monte-t-elle", et repond avec des motifs sur une seule serie. Le transversal demande
"laquelle monte le plus par rapport aux autres", et ne compare que des instruments
entre eux. C'est une autre famille d'inefficience, documentee de longue date, et
elle reste investie des deux cotes du marche.

Deux protections contre ce qui a rate ce matin
----------------------------------------------
1. La grille est VOLONTAIREMENT minuscule : 3 horizons x 2 tailles x 2 frequences,
   soit 12 combinaisons. Sur 211 combinaisons on trouve un gagnant par hasard ; sur
   12 le risque reste chiffrable.
2. Le critere d'acceptation compare a un PORTEFEUILLE NEUTRE tire au hasard, pas a
   zero. Battre zero ne prouve rien quand le marche derive.

Couts
-----
Le spread reel de chaque instrument vient du recensement de la Phase 0, pas d'une
hypothese ronde. Il est preleve a chaque changement de position.
"""

import argparse
import json
import pathlib
import sys

import numpy as np

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from aitb import market_data as md   # noqa: E402

LIQUIDITE = RACINE / "data" / "research" / "liquidite.json"
PART_ENTRAINEMENT = 0.70


def charger():
    """Prix alignes sur un calendrier commun, plus le cout par instrument."""
    d = json.loads(LIQUIDITE.read_text(encoding="utf-8"))
    infos = {x["symbole"]: x for x in d["instruments"]
             if x["source_cout"] == "mesure" and x["ans"] >= 10
             and x["classe"] in ("forex exotique", "metaux", "forex majeur")}
    series = {}
    for s in infos:
        try:
            rates, _ = md.load_history(s, "D1")
        except Exception:
            continue
        t = np.asarray(rates["time"], dtype="int64")
        c = np.asarray(rates["close"], dtype="float64")
        ok = c > 0
        if ok.sum() < 2000:
            continue
        series[s] = (t[ok], c[ok])
    noms = sorted(series)
    dates = np.array(sorted({int(x) for t, _ in series.values() for x in t}))
    P = np.full((len(noms), len(dates)), np.nan)
    for i, s in enumerate(noms):
        t, c = series[s]
        pos = np.searchsorted(t, dates, side="right") - 1
        val = pos >= 0
        P[i, val] = c[pos[val]]
    # Cout aller-retour en fraction du prix, mesure en Phase 0.
    cout = np.array([(infos[s]["cout_aller_retour_pct"] or 0.0) / 100.0 for s in noms])
    return noms, dates, P, cout


def transversal(P, cout, lookback, k, pas):
    """Long les k meilleurs, short les k pires, rebalance tous les `pas` jours.

    Renvoie la serie des rendements quotidiens du portefeuille, couts deduits.
    Le portefeuille est neutre en nombre de lignes : autant de longs que de shorts,
    ce qui retire l'essentiel de la derive commune au panier.
    """
    n_act, n_j = P.shape
    poids = np.zeros(n_act)
    rends = np.full(n_j, np.nan)

    for j in range(1, n_j):
        # Rendement du jour avec les poids d'hier.
        r = (P[:, j] - P[:, j - 1]) / P[:, j - 1]
        vivant = np.isfinite(r) & (poids != 0)
        rends[j] = float(np.nansum(poids[vivant] * r[vivant])) if vivant.any() else 0.0

        if j < lookback or (j - lookback) % pas != 0:
            continue
        passe = P[:, j - lookback]
        perf = np.where((passe > 0) & (P[:, j] > 0), P[:, j] / passe - 1.0, np.nan)
        cand = np.where(np.isfinite(perf))[0]
        if len(cand) < 2 * k:
            continue
        ordre = cand[np.argsort(perf[cand])]
        bas, haut = ordre[:k], ordre[-k:]
        neuf = np.zeros(n_act)
        neuf[haut] = 1.0 / (2 * k)
        neuf[bas] = -1.0 / (2 * k)
        # Cout preleve sur la variation de position, pas sur la position entiere :
        # une ligne conservee d'une periode a l'autre ne repaie pas le spread.
        rends[j] -= float(np.sum(np.abs(neuf - poids) * cout))
        poids = neuf
    return rends


def bilan(r, dates):
    r = r[np.isfinite(r)]
    if len(r) < 200:
        return None
    eq = np.cumprod(1.0 + r)
    ans = len(r) / 252.0
    pic = np.maximum.accumulate(eq)
    dd = float(((pic - eq) / pic).max() * 100)
    cagr = float((eq[-1] ** (1 / ans) - 1) * 100) if eq[-1] > 0 else -100.0
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return {"jours": len(r), "cagr": cagr, "dd": dd,
            "ratio": cagr / dd if dd > 0 else 0.0, "sharpe": sharpe,
            "final": float(eq[-1])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tirages", type=int, default=2000)
    args = ap.parse_args()

    noms, dates, P, cout = charger()
    coupe = int(len(dates) * PART_ENTRAINEMENT)
    print("   MOMENTUM TRANSVERSAL SUR L'UNIVERS FX")
    print("   %d instruments, %d jours, cout reel mesure en Phase 0" % (len(noms), len(dates)))
    print("   entrainement jours 0 a %d, test scelle %d a %d"
          % (coupe, coupe, len(dates)))
    print()

    grille = [(lb, k, pas)
              for lb in (63, 126, 252)
              for k in (5, 10)
              for pas in (21, 5)]
    print("   grille volontairement minuscule : %d combinaisons" % len(grille))
    print()
    print("   %-8s %-4s %-9s %9s %8s %8s %9s %8s"
          % ("lookback", "K", "rebal", "cagr in", "DD in", "ratio in", "sharpe in", "jours"))
    print("   " + "-" * 72)
    res = []
    for lb, k, pas in grille:
        r = transversal(P[:, :coupe], cout, lb, k, pas)
        b = bilan(r, dates[:coupe])
        if b is None:
            continue
        res.append((b["ratio"], lb, k, pas, b))
        print("   %-8d %-4d %-9s %8.2f%% %7.1f%% %8.2f %9.2f %8d"
              % (lb, k, "mensuel" if pas == 21 else "hebdo",
                 b["cagr"], b["dd"], b["ratio"], b["sharpe"], b["jours"]))
    if not res:
        print("   aucune combinaison exploitable")
        return
    res.sort(reverse=True)
    _, lb, k, pas, bi = res[0]
    print()
    print("   MEILLEURE A L'ENTRAINEMENT : lookback %d, K=%d, %s"
          % (lb, k, "mensuel" if pas == 21 else "hebdo"))
    print()

    # Le test n'est lu qu'ici, pour cette seule combinaison.
    r_out = transversal(P[:, coupe:], cout, lb, k, pas)
    bo = bilan(r_out, dates[coupe:])
    print("   HORS ECHANTILLON, combinaison figee")
    if bo is None:
        print("   trop peu de jours")
        return
    print("   %.2f%%/an   DD %.1f%%   ratio %.2f   sharpe %.2f   sur %d jours"
          % (bo["cagr"], bo["dd"], bo["ratio"], bo["sharpe"], bo["jours"]))
    print()

    # Reference honnete : des paniers long/short tires AU HASARD, meme taille,
    # memes dates de rebalancement, memes couts. Battre zero ne prouve rien.
    print("   REFERENCE : %d portefeuilles long/short TIRES AU HASARD" % args.tirages)
    rng = np.random.default_rng(3)
    n_act, n_j = P[:, coupe:].shape
    Q = P[:, coupe:]
    hasard = []
    for _ in range(args.tirages):
        poids = np.zeros(n_act)
        rr = np.full(n_j, np.nan)
        for j in range(1, n_j):
            ret = (Q[:, j] - Q[:, j - 1]) / Q[:, j - 1]
            v = np.isfinite(ret) & (poids != 0)
            rr[j] = float(np.nansum(poids[v] * ret[v])) if v.any() else 0.0
            if j < lb or (j - lb) % pas != 0:
                continue
            cand = np.where(np.isfinite(Q[:, j]) & np.isfinite(Q[:, j - lb]))[0]
            if len(cand) < 2 * k:
                continue
            tire = rng.permutation(cand)
            neuf = np.zeros(n_act)
            neuf[tire[:k]] = 1.0 / (2 * k)
            neuf[tire[k:2 * k]] = -1.0 / (2 * k)
            rr[j] -= float(np.sum(np.abs(neuf - poids) * cout))
            poids = neuf
        b = bilan(rr, dates[coupe:])
        if b:
            hasard.append(b["cagr"])
    hasard = np.array(hasard)
    if len(hasard) > 10:
        pct = 100 * (hasard < bo["cagr"]).mean()
        z = (bo["cagr"] - hasard.mean()) / hasard.std() if hasard.std() > 0 else 0.0
        print("   hasard : p5 %.2f%%  median %.2f%%  p95 %.2f%%"
              % (np.percentile(hasard, 5), np.median(hasard), np.percentile(hasard, 95)))
        print("   la strategie est au percentile %.1f %% du hasard, ecart %+.2f sigma"
              % (pct, z))
        print()
        if pct > 95:
            print("   VERDICT : le classement porte de l'information.")
        else:
            print("   VERDICT : indistinguable d'un panier long/short tire au hasard.")
            print("   Le tri par momentum n'apporte rien sur cet univers.")


if __name__ == "__main__":
    main()
