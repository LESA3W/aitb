# research/montecarlo.py
"""
Simulation de Monte Carlo sur la SEQUENCE des trades.

Pourquoi ce fichier existe
--------------------------
La methodologie de TheMacroTape compte quatre etapes minimales pour passer
"du trading base sur l'espoir au trading base sur la preuve" :
    1. codification stricte des regles
    2. 10 ans de donnees et plus de 1000 trades
    3. validation out of sample
    4. simulation de Monte Carlo

Le projet couvrait deja 1 a 3 (backtest.py, walkforward.py, wf_null.py).
L'etape 4 etait absente : une recherche dans aitb/ et research/ ne remonte
qu'un rng.permutation dans wf_null.py, qui melange les RENDEMENTS DU MARCHE
pour fabriquer un marche synthetique. C'est le test de l'hypothese nulle, une
question differente.

Ici on rebat la SEQUENCE DES TRADES DEJA OBTENUS. La question n'est plus
"cet edge existe-t-il" mais "avec cet edge, ou mon compte peut-il finir si le
hasard distribue les trades autrement".

PIEGE MATHEMATIQUE CORRIGE DANS CETTE VERSION
---------------------------------------------
Premiere version de ce fichier : le critere d'elimination portait sur
l'equite FINALE, dans les deux modes. En permutation, les trois percentiles
sortaient rigoureusement egaux, au chiffre pres.

Ce n'etait pas un bug de tirage, c'est une propriete. Avec un risque en
pourcentage du capital courant, l'equite finale vaut

    capital x produit de (1 + fraction x r_i)

et un PRODUIT EST COMMUTATIF. Rebattre l'ordre ne peut donc pas changer le
resultat final : toutes les permutations finissent exactement au meme endroit.
Tester "l'IC 95 de l'equite finale" en permutation ne teste rien du tout, ca
constate seulement que le backtest etait positif.

Ce qui depend VRAIMENT de l'ordre, c'est le CHEMIN :
  - le creux le plus bas atteint en route
  - le drawdown maximal
  - le fait de toucher ou non le seuil de mort avant la fin
Chaque mode est donc juge sur la statistique qui a un sens pour lui.

Les deux modes, et ce que chacun repond
---------------------------------------
  permutation : memes trades, ordre different. Le point d'arrivee est fixe,
                seul le chemin change. Repond a "une serie de pertes au
                mauvais moment peut-elle me tuer avant l'arrivee".
                Juge sur le CREUX et le drawdown.

  bootstrap   : tirage AVEC REMISE. Certains trades sortent plusieurs fois,
                d'autres jamais. Le point d'arrivee change. Repond a "et si
                j'avais tire un autre echantillon de la meme strategie".
                Juge sur l'EQUITE FINALE, comme la note le decrit.

Un ecart important entre les deux signale une strategie qui doit son resultat
a une poignee de trades exceptionnels.

Le seuil de mort fonctionnelle
------------------------------
Sur 500 EUR, un compte n'a pas besoin d'atteindre zero pour etre fini. Sous un
certain niveau le lot minimum impose un risque monetaire incompressible et le
bot ne peut plus prendre les trades prevus. RUINE_PCT fixe ce plancher.

Usage
-----
    from research.montecarlo import simuler, afficher
    afficher(simuler([t.r_multiple for t in res.trades], capital=500, risque_pct=2.0))

    python research/montecarlo.py --demo
"""

import argparse
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np   # noqa: E402


# Plancher sous lequel le compte ne peut plus trader normalement.
RUINE_PCT = 0.30


def _chemins(rs, capital, risque_pct, tirages, avec_remise, graine):
    """
    Renvoie (finales, creux, drawdowns, nb_ruines).

    `creux` est le point le plus bas atteint en cours de route, exprime en
    monnaie. C'est la statistique qui porte l'information en permutation,
    puisque l'equite finale y est invariante par construction.
    """
    rng = np.random.default_rng(graine)
    a = np.asarray(rs, dtype=float)
    n = len(a)
    frac = risque_pct / 100.0
    seuil = capital * RUINE_PCT

    finales = np.empty(tirages)
    creux = np.empty(tirages)
    dds = np.empty(tirages)
    ruines = 0

    for k in range(tirages):
        ordre = rng.integers(0, n, n) if avec_remise else rng.permutation(n)
        # Facteurs multiplicatifs, bornes pour qu'un R tres negatif ne rende
        # pas le solde negatif : un compte ne passe pas sous zero, il est clos.
        f = np.maximum(1.0 + frac * a[ordre], 0.0)
        eq = capital * np.concatenate(([1.0], np.cumprod(f)))

        bas = float(eq.min())
        pic = np.maximum.accumulate(eq)
        dd = float(((pic - eq) / np.where(pic > 0, pic, 1.0)).max() * 100)

        if bas <= seuil:
            ruines += 1
        finales[k] = eq[-1]
        creux[k] = bas
        dds[k] = dd

    return finales, creux, dds, ruines


def simuler(r_multiples, capital=500.0, risque_pct=2.0, tirages=5000, graine=42):
    rs = [float(r) for r in r_multiples
          if r is not None and not math.isnan(float(r))]
    n = len(rs)
    if n < 30:
        return {"erreur": "seulement %d trades, la simulation n'a pas de sens" % n,
                "trades": n}

    a = np.asarray(rs)
    gains = a[a > 0].sum()
    pertes = -a[a < 0].sum()
    serie = pire = 0
    for r in a:
        serie = serie + 1 if r < 0 else 0
        pire = max(pire, serie)

    rapport = {
        "trades": n, "capital": capital, "risque_pct": risque_pct,
        "tirages": tirages,
        "esperance_r": float(a.mean()),
        "profit_factor": (gains / pertes) if pertes > 0 else float("inf"),
        "win_rate": float((a > 0).mean() * 100),
        "pire_serie_observee": pire,
        "modes": {},
    }

    for nom, remise in (("permutation", False), ("bootstrap", True)):
        fin, creux, dds, ruines = _chemins(rs, capital, risque_pct,
                                           tirages, remise, graine)
        bloc = {
            "final_p5": float(np.percentile(fin, 5)),
            "final_median": float(np.percentile(fin, 50)),
            "final_p95": float(np.percentile(fin, 95)),
            "creux_p5": float(np.percentile(creux, 5)),
            "dd_median": float(np.percentile(dds, 50)),
            "dd_p95": float(np.percentile(dds, 95)),
            "taux_ruine_pct": ruines / tirages * 100,
        }
        if remise:
            # L'arrivee varie : critere sur l'equite finale, comme la note.
            bloc["critere"] = "equite finale, IC 95 bas > capital"
            bloc["survit"] = bloc["final_p5"] > capital
        else:
            # L'arrivee est invariante : seul le chemin informe.
            bloc["critere"] = "creux du chemin, IC 95 bas > seuil de ruine"
            bloc["survit"] = bloc["creux_p5"] > capital * RUINE_PCT
        rapport["modes"][nom] = bloc

    rapport["verdict"] = all(m["survit"] for m in rapport["modes"].values())
    return rapport


def afficher(rapport):
    if "erreur" in rapport:
        print("  Monte Carlo impossible : %s" % rapport["erreur"])
        return
    cap = rapport["capital"]
    print("  " + "=" * 72)
    print("  SIMULATION DE MONTE CARLO")
    print("  " + "=" * 72)
    print("  %d trades  |  capital %.0f  |  risque %.1f%% par trade  |  %d tirages"
          % (rapport["trades"], cap, rapport["risque_pct"], rapport["tirages"]))
    print("  esperance %+.4f R  |  profit factor %.2f  |  win rate %.1f%%"
          % (rapport["esperance_r"], rapport["profit_factor"], rapport["win_rate"]))
    print("  pire serie de pertes consecutives observee : %d"
          % rapport["pire_serie_observee"])
    print()
    print("  %-12s %10s %10s %10s %9s %9s %8s"
          % ("mode", "final p5", "median", "creux p5", "DD med", "DD p95", "ruine"))
    print("  " + "-" * 72)
    for nom, m in rapport["modes"].items():
        print("  %-12s %10.0f %10.0f %10.0f %8.1f%% %8.1f%% %7.1f%%"
              % (nom, m["final_p5"], m["final_median"], m["creux_p5"],
                 m["dd_median"], m["dd_p95"], m["taux_ruine_pct"]))
    print()
    for nom, m in rapport["modes"].items():
        print("    %-12s critere : %-42s %s"
              % (nom, m["critere"], "PASSE" if m["survit"] else "ECHOUE"))
    print()
    if rapport["verdict"]:
        print("  VERDICT : la strategie resiste au rebattage dans les deux modes.")
    else:
        rates = [n for n, m in rapport["modes"].items() if not m["survit"]]
        print("  VERDICT : ELIMINEE en mode %s." % " et ".join(rates))
        print("            Reduire le risque par trade, ou abandonner.")
    print()
    print("  Ce test ne prouve pas qu'un edge existe. Il mesure ce que devient")
    print("  un edge suppose quand le hasard rebat l'ordre. L'existence se")
    print("  teste ailleurs : walkforward.py et wf_null.py.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--capital", type=float, default=500.0)
    p.add_argument("--risque", type=float, default=2.0)
    p.add_argument("--tirages", type=int, default=5000)
    args = p.parse_args()

    if not args.demo:
        print("  Module a importer depuis un script de recherche :")
        print("    from research.montecarlo import simuler, afficher")
        print("    afficher(simuler([t.r_multiple for t in res.trades], capital=500))")
        print()
        print("  Demonstration : python research/montecarlo.py --demo")
        return

    # Reproduction de la note de TheMacroTape sur le ratio risque-reward.
    # Les trois profils ont la MEME esperance de +0.4 R. Le win rate est
    # calcule pour que ce soit exact, et non choisi a la main :
    #     w x RR - (1 - w) = 0.4   donc   w = 1.4 / (RR + 1)
    # Premiere version de ce fichier : j'avais ecrit 2.7% pour le RR 10, ce
    # qui donne -0.70 R. La demo testait donc une strategie PERDANTE en
    # annoncant une esperance positive.
    rng = np.random.default_rng(7)
    for rr in (1.0, 3.0, 10.0):
        w = 1.4 / (rr + 1.0)
        n = 400
        rs = np.where(rng.random(n) < w, rr, -1.0)
        print()
        print("  ### RR %.0f, gagne %.1f%% du temps, esperance theorique %+.2f R ###"
              % (rr, w * 100, w * rr - (1 - w)))
        afficher(simuler(rs.tolist(), capital=args.capital,
                         risque_pct=args.risque, tirages=args.tirages))


if __name__ == "__main__":
    main()
