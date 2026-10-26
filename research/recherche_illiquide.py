"""Recherche de strategie sur l'univers ILLIQUIDE. Phase 2 et 3 du plan TRADING BOT.

Protocole, et pourquoi il est ecrit avant de mesurer
---------------------------------------------------
TheMacroTape, note "backtest-d-une-strategie-sans-avantage" : l'auteur teste onze
variantes d'une strategie, en trouve deux positives, et refuse de les presenter comme
un resultat. "Quand on teste 11 variantes et qu'on en garde 2 positives, c'est
exactement comme ca qu'on fabrique une strategie qui n'existe que dans le passe."

Ici la grille complete compte 215 combinaisons sur 53 instruments. Le risque de
fabriquer un faux gagnant est donc bien plus grand que dans son exemple, et la seule
protection est le protocole :

  1. La periode de TEST est scellee. Aucune selection ne la regarde.
  2. Le classement se fait sur l'ENTRAINEMENT seul.
  3. Le nombre de combinaisons testees est journalise, pour que le biais de
     selection soit chiffrable au lieu d'etre nie.
  4. La DISTRIBUTION complete des resultats hors echantillon est rapportee, pas
     seulement celle des gagnantes. Si la mediane des combinaisons perd, alors une
     gagnante n'est qu'un tirage dans une queue, et il faut le dire.

Le point 4 est celui qui manquait partout ailleurs. Sur le book forex existant, la
mediane de 5246 combinaisons avait un profit factor hors echantillon de 0,696 : le
classement portait de l'information, mais toute la distribution etait perdante.

Risque
------
Montant FIXE par trade, conformement a la note sur le drag de volatilite. Le capital
de reference est volontairement eleve pendant la recherche : a 500 EUR le lot minimum
ecarte silencieusement la majorite des instruments (mesure : 3 sur 22 seulement
passent), ce qui biaiserait la recherche vers les rares instruments finançables.
La question du capital reel est traitee APRES, en choisissant le type de compte.

Usage
-----
    python research/recherche_illiquide.py                 grille complete
    python research/recherche_illiquide.py --familles ema_trend,donchian
    python research/recherche_illiquide.py --reprendre     reprend un run interrompu
"""

import argparse
import json
import pathlib
import sys
import time

import numpy as np

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from aitb import market_data as md            # noqa: E402
from aitb import strategies as st             # noqa: E402
from aitb.backtest import BacktestConfig, Costs, run   # noqa: E402

LIQUIDITE = RACINE / "data" / "research" / "liquidite.json"
SORTIE = RACINE / "data" / "research" / "illiquide_resultats.jsonl"

# Capital de recherche. Volontairement au-dessus du mur du lot minimum pour que la
# mesure porte sur l'edge et non sur la taille de compte. Voir le docstring.
CAPITAL = 100000.0
RISQUE_EUR = 1000.0

PART_ENTRAINEMENT = 0.70

# AUCUNE famille de aitb/strategies.py n'emet de signal de sortie : signals() y
# renvoie toujours (signaux, distance_de_stop), jamais un troisieme tableau. Avec
# tp_r=None une position ne peut donc se fermer que sur stop ou A LA DERNIERE BARRE.
# Mesure du 2026-09-16 : 42 trades clotures en "end", dont USDTRY entre a 1,775 et
# sorti d'office a 48,405, soit +3038 R. Ces faux trades portent tous la meme date
# de sortie, donc ils tombaient TOUS dans la tranche de test et lui donnaient une
# esperance de +10 R par trade.
#
# Le take profit et le break-even sont donc fixes ici, et fixes aux valeurs DEJA
# en production sur l'autre projet, mesurees sur d'autres donnees. Les faire varier
# sur cet univers reviendrait a les choisir sur l'echantillon qu'on cherche a
# valider, et a gonfler l'espace de selection que la note nous demande de contenir.
TP_R = 2.0
BREAK_EVEN = True

# Couts : spread reel de chaque bougie, plus le slippage. La commission par lot du
# forex est portee par le spread chez un courtier a spread seul ; on ne la compte
# donc pas deux fois. Le slippage reste, il est reel.
COUTS = Costs(commission_per_lot_round_turn=0.0,
              commission_pct_round_turn=0.0,
              spread_points=None,
              slippage_points=2.0)


def univers():
    """Les instruments retenus par le recensement de la Phase 0."""
    d = json.loads(LIQUIDITE.read_text(encoding="utf-8"))
    return sorted(x["symbole"] for x in d["instruments"]
                  if x["source_cout"] == "mesure" and x["ans"] >= 10
                  and x["classe"] in ("forex exotique", "metaux"))


def charger_tout(symboles):
    """Charge une fois pour toutes. Relire a chaque combinaison serait absurde."""
    data = {}
    for s in symboles:
        try:
            rates, meta = md.load_history(s, "D1")
        except Exception:
            continue
        if len(rates) < 600:
            continue
        specs = {"symbol": s, **{k: meta[k] for k in
                 ("point", "pip", "tick_size", "tick_value",
                  "volume_min", "volume_max", "volume_step")}}
        data[s] = (rates, specs, meta)
    return data


def bilan(rs):
    """Profit factor, esperance et pire serie, a partir des R-multiples seuls."""
    if len(rs) == 0:
        return None
    a = np.asarray(rs, dtype=float)
    gains = a[a > 0].sum()
    pertes = -a[a < 0].sum()
    serie = pire = 0
    for r in a:
        serie = serie + 1 if r < 0 else 0
        pire = max(pire, serie)
    return {
        "trades": int(len(a)),
        "pf": float(gains / pertes) if pertes > 0 else None,
        "esperance_r": float(a.mean()),
        "r_total": float(a.sum()),
        "win_rate": float((a > 0).mean() * 100),
        "pire_serie": int(pire),
    }


def evaluer(strat, data, nom_famille):
    """Un passage de la combinaison sur tout l'univers.

    Le decoupage entrainement/test se fait PAR INSTRUMENT, sur sa propre histoire,
    pour qu'un instrument court ne soit pas entierement dans une seule tranche.
    """
    r_in, r_out = [], []
    par_sym = {}
    for s, (rates, specs, meta) in data.items():
        try:
            sg = strat.signals(rates)
            sig, sl = sg[0], sg[1]
            ex = sg[2] if len(sg) > 2 else None
        except Exception:
            continue
        cfg = BacktestConfig(initial_balance=CAPITAL, risk_eur=RISQUE_EUR,
                             tp_r=TP_R, use_breakeven=BREAK_EVEN,
                             volume_min=meta["volume_min"], volume_max=1e9,
                             volume_step=meta["volume_step"])
        try:
            res = run(rates, sig, sl, specs, cfg, COUTS,
                      strategy_name=nom_famille, timeframe="D1", exit_signals=ex)
        except Exception:
            continue
        if not res.trades:
            continue
        # Un trade clos en "end" n'est pas un trade : c'est une position que le
        # backtest a coupee faute de donnees. Il est exclu des deux tranches.
        vrais = [t for t in res.trades if t.reason != "end"]
        if not vrais:
            continue
        # Decoupage sur la date d'ENTREE. Un trade appartient a la periode ou la
        # decision a ete prise. Decouper sur la sortie envoie mecaniquement les
        # trades les plus longs, donc les gros gagnants, du cote du test.
        coupe = rates["time"][int(len(rates) * PART_ENTRAINEMENT)]
        a = [t.r_multiple for t in vrais if t.time_in <= coupe]
        b = [t.r_multiple for t in vrais if t.time_in > coupe]
        r_in.extend(a)
        r_out.extend(b)
        par_sym[s] = len(vrais)
    return r_in, r_out, par_sym


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--familles", default=None)
    ap.add_argument("--reprendre", action="store_true")
    args = ap.parse_args()

    syms = univers()
    data = charger_tout(syms)
    print("   RECHERCHE SUR L'UNIVERS ILLIQUIDE")
    print("   %d instruments charges sur %d retenus" % (len(data), len(syms)))
    print("   capital %.0f, risque FIXE %.0f EUR, entrainement %.0f%%"
          % (CAPITAL, RISQUE_EUR, PART_ENTRAINEMENT * 100))
    print()

    familles = list(st.REGISTRY)
    if args.familles:
        familles = [f.strip() for f in args.familles.split(",") if f.strip() in st.REGISTRY]

    combos = []
    for fam in familles:
        try:
            for strat in st.expand_grid(fam):
                combos.append((fam, strat))
        except Exception:
            continue
    print("   %d combinaisons a tester sur %d familles" % (len(combos), len(familles)))
    print("   Ce nombre est le denominateur du biais de selection. Il est journalise.")
    print()

    deja = set()
    if args.reprendre and SORTIE.exists():
        for ligne in SORTIE.read_text(encoding="utf-8").splitlines():
            try:
                deja.add(json.loads(ligne)["cle"])
            except Exception:
                pass
        print("   reprise : %d combinaisons deja faites" % len(deja))
    elif SORTIE.exists():
        SORTIE.unlink()

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    fait = 0
    with SORTIE.open("a", encoding="utf-8") as f:
        for i, (fam, strat) in enumerate(combos, 1):
            cle = "%s|%s" % (fam, strat)
            if cle in deja:
                continue
            r_in, r_out, par_sym = evaluer(strat, data, fam)
            bi, bo = bilan(r_in), bilan(r_out)
            if bi is None or bo is None:
                continue
            enr = {"cle": cle, "famille": fam, "params": str(strat),
                   "symboles": len(par_sym),
                   "entrainement": bi, "test": bo}
            f.write(json.dumps(enr) + "\n")
            f.flush()
            fait += 1
            if fait % 10 == 0:
                ecoule = time.time() - t0
                print("   %4d/%4d  %-16s  in pf %-6s  (%.0f s, reste ~%.0f s)"
                      % (i, len(combos), fam,
                         ("%.3f" % bi["pf"]) if bi["pf"] else "n/a",
                         ecoule, ecoule / max(fait, 1) * (len(combos) - i)))
    print()
    print("   %d combinaisons ecrites dans %s" % (fait, SORTIE.name))
    print("   Analyse : python research/analyse_illiquide.py")


if __name__ == "__main__":
    main()
