"""Validation finale d'une combinaison. Etape 4 de la grille TheMacroTape.

Ce que fait ce module, et dans quel ordre
-----------------------------------------
Une combinaison qui sort en tete d'une recherche n'a rien prouve. Elle a seulement
gagne un concours ou 215 candidats concouraient. Ce module lui applique les trois
epreuves qui separent un edge d'une chance :

  1. MONTE CARLO, via research/montecarlo.py, dans ses deux modes. Le critere de la
     note : "si la ligne basse de ton IC 95 touche le 0 ou passe en negatif, ta
     strategie est mathematiquement indistinguable du hasard".

  2. TEST NUL. Memes trades, memes dates, memes tailles, mais le resultat de chaque
     trade est multiplie par un signe aleatoire. L'esperance devient exactement nulle
     par construction. Si le resultat reel ne sort pas de cette distribution, il n'y
     a rien a exploiter.

  3. ROBUSTESSE AUX EXTREMES. On retire les cinq meilleurs trades. Une strategie dont
     le resultat tient a une poignee de coups est une loterie, pas un systeme. Ce
     test avait deja tue une piste crypto du projet : un seul trade DOGEUSDT valait
     90 % du R total.

Et la sensibilite aux couts, parce que la note "exploiter-un-petit-capital" dit que
l'avantage ne survit que si les couts restent en dessous de l'edge : on mesure a quel
niveau de cout la combinaison cesse d'etre rentable.

Usage
-----
    python research/valider_illiquide.py --cle "ema_trend|ema_trend(fast=8,...)"
    python research/valider_illiquide.py --meilleure     prend la 1ere du classement
"""

import argparse
import json
import pathlib
import sys

import numpy as np

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from aitb import market_data as md                      # noqa: E402
from aitb import strategies as st                       # noqa: E402
from aitb.backtest import BacktestConfig, Costs, run     # noqa: E402
from research.montecarlo import simuler, afficher        # noqa: E402
from research.recherche_illiquide import (               # noqa: E402
    univers, charger_tout, CAPITAL, RISQUE_EUR, TP_R, BREAK_EVEN, PART_ENTRAINEMENT)

RESULTATS = RACINE / "data" / "research" / "illiquide_resultats.jsonl"


def trades_de(cle, data, cout):
    """Rejoue une combinaison sur tout l'univers et renvoie ses trades utiles."""
    fam, params = cle.split("|", 1)
    strat = None
    for s in st.expand_grid(fam):
        if str(s) == params:
            strat = s
            break
    if strat is None:
        raise SystemExit("combinaison introuvable : %s" % cle)

    out = []
    for sym, (rates, specs, meta) in data.items():
        try:
            sg = strat.signals(rates)
            sig, sl = sg[0], sg[1]
            ex = sg[2] if len(sg) > 2 else None
            res = run(rates, sig, sl, specs,
                      BacktestConfig(initial_balance=CAPITAL, risk_eur=RISQUE_EUR,
                                     tp_r=TP_R, use_breakeven=BREAK_EVEN,
                                     volume_min=meta["volume_min"], volume_max=1e9,
                                     volume_step=meta["volume_step"]),
                      cout, strategy_name=fam, timeframe="D1", exit_signals=ex)
        except Exception:
            continue
        coupe = rates["time"][int(len(rates) * PART_ENTRAINEMENT)]
        for t in res.trades:
            if t.reason == "end":
                continue
            out.append({"sym": sym, "r": float(t.r_multiple),
                        "t_in": int(t.time_in), "t_out": int(t.time_out),
                        "dans_test": bool(t.time_in > coupe)})
    out.sort(key=lambda x: x["t_out"])
    return fam, strat, out


def pf(a):
    a = np.asarray(a, dtype=float)
    g = a[a > 0].sum()
    p = -a[a < 0].sum()
    return float(g / p) if p > 0 else float("inf")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cle", default=None)
    ap.add_argument("--meilleure", action="store_true")
    ap.add_argument("--capital", type=float, default=500.0)
    ap.add_argument("--risque", type=float, default=2.0)
    args = ap.parse_args()

    lignes = [json.loads(x) for x in RESULTATS.read_text(encoding="utf-8").splitlines()
              if x.strip()]
    lignes = [x for x in lignes
              if x["entrainement"]["pf"] is not None and x["test"]["pf"] is not None]
    if args.meilleure:
        lignes.sort(key=lambda x: -x["entrainement"]["pf"])
        cle = lignes[0]["cle"]
    elif args.cle:
        cle = args.cle
    else:
        raise SystemExit("donner --cle ou --meilleure")

    print("   VALIDATION FINALE")
    print("   combinaison : %s" % cle)
    print("   Rappel : elle a gagne un concours a %d candidats. Cela ne prouve rien."
          % len(lignes))
    print()

    data = charger_tout(univers())
    base = Costs(0.0, 0.0, None, 2.0)
    fam, strat, tr = trades_de(cle, data, base)
    R = np.array([x["r"] for x in tr])
    Rin = np.array([x["r"] for x in tr if not x["dans_test"]])
    Rout = np.array([x["r"] for x in tr if x["dans_test"]])
    print("   %d trades utiles sur %d instruments" % (len(R), len({x["sym"] for x in tr})))
    print("   entrainement %d trades pf %.3f | hors echantillon %d trades pf %.3f"
          % (len(Rin), pf(Rin), len(Rout), pf(Rout)))
    print("   critere plus de 1000 trades : %s" % ("TENU" if len(R) > 1000 else "ECHOUE"))
    print()

    print("   1. MONTE CARLO, sur les trades hors echantillon uniquement")
    print("   (les utiliser tous melangerait la periode ayant servi a choisir)")
    if len(Rout) >= 30:
        afficher(simuler(Rout.tolist(), capital=args.capital,
                         risque_pct=args.risque, tirages=5000))
    else:
        print("   trop peu de trades hors echantillon")
    print()

    print("   2. TEST NUL : memes trades, signe aleatoire, esperance nulle")
    f = args.risque / 100.0
    reel = float(np.prod(np.maximum(1.0 + f * Rout, 0.0)) * args.capital)
    nuls = []
    for k in range(200):
        g = np.random.default_rng(900 + k)
        s = Rout * g.choice([-1.0, 1.0], size=len(Rout))
        nuls.append(float(np.prod(np.maximum(1.0 + f * s, 0.0)) * args.capital))
    nuls = np.array(nuls)
    pct = 100 * (nuls < reel).mean()
    print("   reel %10.0f | nuls : p5 %8.0f  median %8.0f  p95 %8.0f  max %8.0f"
          % (reel, np.percentile(nuls, 5), np.median(nuls),
             np.percentile(nuls, 95), nuls.max()))
    print("   le reel est au percentile %.1f %% de la distribution nulle" % pct)
    print("   verdict : %s" % ("SORT du hasard" if pct > 95 else "INDISTINGUABLE du hasard"))
    print()

    print("   3. ROBUSTESSE : et si on retire les 5 meilleurs trades ?")
    o = np.sort(Rout)[::-1]
    print("   R total hors echantillon %+.1f | sans les 5 meilleurs %+.1f | pf %.3f -> %.3f"
          % (Rout.sum(), Rout.sum() - o[:5].sum(), pf(Rout), pf(np.sort(Rout)[:-5])))
    part = 100 * o[:5].sum() / Rout.sum() if Rout.sum() > 0 else float("nan")
    print("   les 5 meilleurs representent %.1f %% du R total" % part)
    if part > 50:
        print("   Plus de la moitie du resultat tient a cinq trades : loterie.")
    print()

    print("   4. A QUEL NIVEAU DE COUT LA COMBINAISON MEURT-ELLE ?")
    print("   %-24s %8s %9s %9s" % ("cout ajoute", "trades", "pf out", "R total"))
    for lbl, c in (("aucun (spread reel)", Costs(0.0, 0.0, None, 2.0)),
                   ("+ 7 EUR par lot", Costs(7.0, 0.0, None, 2.0)),
                   ("+ 0.05 % notionnel", Costs(0.0, 0.05, None, 2.0)),
                   ("+ 0.10 % notionnel", Costs(0.0, 0.10, None, 2.0)),
                   ("slippage x3", Costs(0.0, 0.0, None, 6.0))):
        try:
            _, _, t2 = trades_de(cle, data, c)
        except Exception:
            continue
        r2 = np.array([x["r"] for x in t2 if x["dans_test"]])
        if len(r2) == 0:
            continue
        print("   %-24s %8d %9.3f %9.1f" % (lbl, len(r2), pf(r2), r2.sum()))
    print()
    print("   Une combinaison qui ne survit qu'au cout le plus favorable n'est pas")
    print("   exploitable : la note dit que l'edge doit rester au-dessus des couts.")


if __name__ == "__main__":
    main()
