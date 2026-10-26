# research/wf_search.py
"""
Recherche de lignes de book VALIDEES EN WALK-FORWARD.

Pourquoi ce script remplace sweep.py
------------------------------------
Le book actuel a ete selectionne par balayage in-sample : on a garde les 5
meilleures combinaisons sur 10 750 essais. Le walk-forward a ensuite montre que
cet edge ne survit pas (1 fenetre positive sur 8, PF out-of-sample 0.96). Cinq
rescapees sur dix mille tirages, c'est le residu statistique attendu quand on
cherche assez longtemps dans du bruit.

Ce script inverse la selection : les parametres sont choisis UNIQUEMENT sur le
passe de chaque fenetre, puis appliques a la periode suivante, jamais vue. Une
combinaison n'est retenue que si elle gagne out-of-sample, de facon REGULIERE.

Objectif concret
----------------
Faire baisser le drawdown du portefeuille sous la limite de 10% d'une prop
firm. Le rendement d'un portefeuille croit en N quand on ajoute des lignes
decorrelees, mais son drawdown ne croit qu'en racine de N. Ajouter des lignes
independantes est donc le SEUL levier connu qui ameliore le ratio sans monter
le risque. Encore faut-il que ces lignes aient un edge reel, sinon on empile du
bruit decorrele, ce qui ne produit rien.

Criteres de retenue, volontairement severes :
  - au moins 60% de fenetres out-of-sample positives
  - profit factor out-of-sample >= 1.10
  - degradation in-sample -> out-of-sample < 60%
  - au moins 40 trades out-of-sample cumules

Usage:
    python research/wf_search.py                 # tout le cache forex/indices
    python research/wf_search.py --windows 5
"""

import argparse
import json
from functools import lru_cache
from multiprocessing import Pool, cpu_count
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                          # noqa: E402

from aitb import market_data as md                          # noqa: E402
from aitb import strategies as st                           # noqa: E402
from aitb.backtest import BacktestConfig, Costs, backtest_strategy   # noqa: E402

COSTS = Costs(commission_per_lot_round_turn=7.0, slippage_points=2.0)
SORTIE = PROJECT_ROOT / "research" / "wf_survivors.json"
JOURNAL = PROJECT_ROOT / "research" / "wf_search.log"
# Resultats ecrits au fil de l'eau : une interruption ne coute plus que la
# combinaison en cours au lieu de tout le run.
PARTIEL = PROJECT_ROOT / "research" / "wf_partiel.jsonl"

# Crypto exclu : chez FTMO en compte Swing le levier crypto est de 1:1, donc
# une ligne crypto immobiliserait tout le capital pour une fraction du risque.
# La recherche precedente avait de plus montre que le resultat crypto tenait a
# un seul trade DOGE a +1052 R, donc a une loterie, pas a un edge.
EXCLUS = ("USDT", "BTC", "ETH", "SOL", "DOGE", "XRP", "BNB", "ADA")


# Timeframes explores. H2 a H12 ont ete ajoutes le 2026-09-12 sur une
# hypothese dirigee : sur 1296 combinaisons, les 4 seules rescapees etaient
# TOUTES en H4 et aucune en H1. Si l'edge existe vraiment a cet horizon, les
# timeframes voisins devraient en garder une partie. On teste une plage autour
# d'un resultat observe, on n'elargit pas au hasard.
# D1 ajoute le 2026-09-12 : les rescapees se concentrent sur les horizons les
# plus longs testes, donc le daily doit l'etre aussi. Bonus decisif, le cache
# D1 remonte a 2006 sur certaines paires contre 6 ans en intraday : beaucoup
# plus de fenetres walk-forward, donc une validation nettement plus solide.
# H1 et H2 RETIRES le 2026-09-13 : zero rescapee sur 4577 combinaisons
# evaluees, alors que D1/H12/H8/H6 en fournissaient 48. Les balayer coute du
# calcul pour un rendement mesure a zero.
TIMEFRAMES = ("H3", "H4", "H6", "H8", "H12", "D1")


def instruments():
    vus = []
    # glob sur *.csv et pas *_H*.csv : ce dernier ne matchait pas les fichiers
    # D1, qui etaient donc silencieusement absents de la recherche.
    for f in sorted((PROJECT_ROOT / "data" / "history").glob("*.csv")):
        nom = f.stem
        if any(x in nom for x in EXCLUS):
            continue
        sym, tf = nom.rsplit("_", 1)
        if tf in TIMEFRAMES:
            vus.append((sym, tf))
    return vus


@lru_cache(maxsize=3)
def _charger(sym, tf):
    """Cache par processus : chaque worker ne relit un historique qu'une fois."""
    rates, meta = md.load_history(sym, tf)
    return rates, {"symbol": sym,
                   **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                           "volume_min", "volume_max", "volume_step")}}


# Seuil de memoire libre sous lequel un worker attend au lieu de continuer.
# En dessous, l'OS commence a tuer des processus, et c'est toujours le plus
# jeune qui saute : le notre.
RAM_MINI_GO = 1.2


def _attendre_memoire(timeout=300):
    """Patiente tant que la memoire libre est sous le seuil."""
    try:
        import psutil
    except Exception:
        return
    import time as _t
    debut = _t.time()
    while psutil.virtual_memory().available / 1e9 < RAM_MINI_GO:
        if _t.time() - debut > timeout:
            return          # on tente quand meme plutot que de bloquer a l'infini
        _t.sleep(5)


def _tache(args):
    """Une combinaison (marche, timeframe, famille). Execute dans un worker."""
    sym, tf, fam, n_fen, risque = args
    _attendre_memoire()
    try:
        rates, specs = _charger(sym, tf)
        cfg = BacktestConfig(initial_balance=100000.0, risk_pct=risque, tp_r=2.0)
        return walkforward(sym, tf, fam, rates, specs, n_fen, cfg)
    except Exception:
        return None


def specs_from(meta, symbol):
    return {"symbol": symbol,
            **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                    "volume_min", "volume_max", "volume_step")}}


def meilleur_sur(rates, specs, tf, famille, config, mini_trades=12):
    """
    Meilleure variante sur CETTE tranche uniquement.

    Le score privilegie la regularite (PF x rendement / drawdown) et non le
    rendement brut : sans ca on retient la variante qui a eu un coup de chance.
    """
    best = None
    for v in st.expand_grid(famille):
        try:
            r = backtest_strategy(v, rates, specs, tf, config, COSTS)
        except Exception:
            continue
        m = r.metrics
        if m["trades"] < mini_trades:
            continue
        pf = min(m["profit_factor"], 5.0)
        dd = max(m["max_drawdown_pct"], 1.0)
        score = (pf - 1.0) * m["return_pct"] / dd
        if best is None or score > best[0]:
            best = (score, v, m)
    return best


def walkforward(sym, tf, famille, rates, specs, n_fenetres, config):
    """Retourne le bilan out-of-sample cumule, ou None si non evaluable."""
    n = len(rates)
    if n < 2500:
        return None
    pas = n // (n_fenetres + 1)
    if pas < 400:
        return None

    positives = 0
    evaluees = 0
    trades_out = 0
    gains_out = 0.0
    pertes_out = 0.0
    pf_in = []
    r_total = []
    r_longs = []
    r_shorts = []

    for k in range(n_fenetres):
        fin_train = pas * (k + 1)
        fin_test = min(fin_train + pas, n)
        if fin_test - fin_train < 200:
            break
        train = rates[:fin_train]
        test = rates[fin_train:fin_test]

        choix = meilleur_sur(train, specs, tf, famille, config)
        if choix is None:
            continue
        _, variante, m_in = choix
        try:
            r = backtest_strategy(variante, test, specs, tf, config, COSTS)
        except Exception:
            continue
        m = r.metrics
        if m["trades"] < 3:
            continue

        evaluees += 1
        pf_in.append(min(m_in["profit_factor"], 5.0))
        trades_out += m["trades"]
        for t in r.trades:
            rr = t.r_multiple
            r_total.append(rr)
            sens = (getattr(t, "direction", None) or getattr(t, "side", None)
                    or getattr(t, "type", None))
            if str(sens).lower() in ("1", "buy", "achat", "long"):
                r_longs.append(rr)
            else:
                r_shorts.append(rr)
            if rr > 0:
                gains_out += rr
            else:
                pertes_out -= rr
        if m["return_pct"] > 0:
            positives += 1

    if evaluees < 4 or trades_out < 40:
        return None

    def _pf(xs):
        g = sum(x for x in xs if x > 0)
        p = -sum(x for x in xs if x < 0)
        return g / p if p > 0 else 0.0

    # Drawdown reellement subi sur la sequence out-of-sample, en % du capital.
    # C'est LA contrainte qui mord chez une prop firm : la limite est de 10%,
    # et le book actuel tourne a 23-31%. Une ligne peut etre rentable et
    # totalement intradable pour cette seule raison.
    eq = np.cumsum(np.array(r_total, dtype=float)) * (config.risk_pct / 100.0)
    eq = np.concatenate(([0.0], eq))
    pic = np.maximum.accumulate(eq)
    dd_out = float(np.max(pic - eq)) * 100.0

    # Sans les 5 meilleurs trades : detecte les strategies-loterie dont tout le
    # resultat tient a une poignee de coups.
    sans_top5 = sorted(r_total)[:-5] if len(r_total) > 12 else r_total

    pf_out = gains_out / pertes_out if pertes_out > 0 else 0.0
    pf_in_moy = float(np.mean(pf_in)) if pf_in else 0.0
    degradation = ((pf_in_moy - pf_out) / pf_in_moy * 100) if pf_in_moy > 0 else 999.0

    return {
        "symbol": sym, "timeframe": tf, "famille": famille,
        "fenetres": evaluees, "positives": positives,
        "pct_positives": positives / evaluees * 100,
        "trades_out": trades_out,
        "pf_out": pf_out, "pf_in": pf_in_moy,
        "degradation_pct": degradation,
        "esperance_r": float(np.mean(r_total)) if r_total else 0.0,
        "r_total": float(np.sum(r_total)),
        "dd_out_pct": dd_out,
        "pf_long": _pf(r_longs), "pf_short": _pf(r_shorts),
        "n_long": len(r_longs), "n_short": len(r_shorts),
        "pf_sans_top5": _pf(sans_top5),
    }


# Limite de drawdown d'une prop firm : 10%. On s'arrete a 8% pour garder de la
# marge : le drawdown mesure sur 6 ans d'historique sera depasse un jour.
DD_MAX = 8.0


def retenu(b):
    """
    Tous les criteres qui ont tue mes candidates precedentes, appliques DES LE
    DEPART au lieu d'etre verifies apres coup.

    Historique de ces quatre regles :
      - pf_short : XAUUSD H4 ema_trend faisait 2.27 en achat et 0.56 en vente.
        C'etait de l'exposition a la hausse de l'or, pas un edge.
      - pf_sans_top5 : la meme tombait a 0.60 en retirant ses 5 meilleurs
        trades sur 32. Loterie.
      - n_short : ESP35 H4 bpr n'avait que 5 ventes. Echantillon inexploitable.
      - dd_out : le book live fait 23 a 31% de drawdown contre une limite
        FTMO de 10%. Rentable ou pas, il est intradable.

    CE QUE CE FILTRE NE PEUT PAS VOIR, ET POURQUOI (2026-09-14)
    -----------------------------------------------------------
    Tous ces criteres portent sur la performance HORS ECHANTILLON. Or
    selectionner sur pf_out seul recompense MECANIQUEMENT la chance : le hasard
    produit des pf_out flatteurs, et plus on teste de combinaisons, plus on en
    ramasse. Les trois lignes les plus fragiles du book v3 ont un pf_out
    MEILLEUR que la mediane des saines. Elles n'ont pas passe le filtre malgre
    leur faiblesse, elles l'ont passe A CAUSE d'elle.

    Le signal qui les demasque est pf_in, le PF sur les donnees d'ENTRAINEMENT.
    Une combinaison que l'optimiseur n'a pas reussi a rendre rentable sur le
    train, et qui gagne quand meme sur le test, n'a rien demontre :

        GBPUSD D1 rsi_reversion  pf_in 0.40  pf_out 1.76  degradation -343%
        XAUUSD H8 squeeze        pf_in 0.57  pf_out 1.68  degradation -192%
        EURCHF H6 squeeze        pf_in 0.96  pf_out 1.43  degradation  -49%

    pf_in n'est PAS dans raisons_echec(), et c'est un choix mesure, pas un
    oubli. Mais attention a ne pas lire le resultat trop vite, ces trois
    lignes ne sont PAS dans le meme cas :

        ligne        PF 20 ans  PF periode commune  trades communs  verdict
        XAUUSD H8         0.98                1.06              88  faible mais positive
        EURCHF H6         1.19                1.26              90  faible mais positive
        GBPUSD D1         0.88                2.65              18  INEVALUABLE

    XAUUSD et EURCHF ont un echantillon suffisant et des fenetres qui
    coincident presque, leur verdict est lisible : faibles, positives, ce que
    pf_in annoncait exactement.

    GBPUSD est autre chose. Un estimateur qui passe de 0.88 a 2.65 selon la
    fenetre, sur 18 observations, ne dit pas que la ligne est rentable : il dit
    qu'il n'y a plus assez de donnees pour en dire quoi que ce soit. L'amplitude
    du basculement EST la preuve de l'instabilite. "Non prouvee perdante" et
    "rentable" ne sont pas la meme phrase.

    Un seuil dur a 1.0 aurait donc ecarte deux lignes positives et une ligne
    inevaluable, sans distinguer les deux cas.

    Et les retirer DEGRADE le portefeuille : ratio rendement/drawdown 2.37
    avec elles, 2.12 sans. Elles portent de la decorrelation.

    pf_in est donc un detecteur de FAIBLESSE, pas de non-rentabilite. Regle
    retenue : pf_in < 1.0 declenche une ALERTE qui impose un test a parametres
    figes SUR LA PERIODE COMMUNE avant d'accepter la ligne dans un book. Voir
    fragile() ci-dessous.
    """
    return not raisons_echec(b)


def fragile(b):
    """
    La combinaison merite-t-elle un test a parametres figes avant d'entrer
    dans un book ? Ce n'est PAS un motif de rejet, voir retenu().
    """
    return b.get("pf_in", 0.0) < 1.0


def raisons_echec(b):
    """
    Liste des criteres que cette combinaison ECHOUE, vide si elle passe tout.

    Renvoyer les raisons plutot qu'un booleen n'est pas cosmetique. Le
    2026-09-12, le passage de 4 a 9 criteres a fait tomber 24 candidates a 0
    sans qu'on puisse savoir laquelle des 5 nouvelles regles mordait. Un
    booleen n'apprend rien quand la recherche sort zero resultat ; les raisons
    agregees disent s'il faut relacher un seuil ou conclure qu'il n'y a rien.
    """
    r = []
    if b["pct_positives"] < 60.0:
        r.append("fenetres+ >= 60%")
    if b["pf_out"] < 1.15:
        r.append("PF_out >= 1.15")
    if b["degradation_pct"] >= 60.0:
        r.append("degradation < 60%")
    if b["trades_out"] < 40:
        r.append("trades >= 40")
    # .get() car les resultats sauvegardes avant le 2026-09-12 n'ont pas ces
    # champs. Sans valeur, on considere le critere echoue plutot que reussi.
    if b.get("dd_out_pct", 999.0) > DD_MAX:
        r.append("drawdown <= %.0f%%" % DD_MAX)
    if b.get("pf_long", 0.0) < 1.0:
        r.append("PF achats >= 1.0")
    if b.get("pf_short", 0.0) < 1.0:
        r.append("PF ventes >= 1.0")
    if b.get("n_short", 0) < 12:
        r.append("ventes >= 12")
    if b.get("pf_sans_top5", 0.0) < 1.0:
        r.append("PF sans top5 >= 1.0")
    return r


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--windows", type=int, default=5)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--jobs", type=int, default=0, help="processus, 0 = auto")
    p.add_argument("--rezero", action="store_true",
                   help="ignore les resultats partiels et repart de zero")
    args = p.parse_args()

    config = BacktestConfig(initial_balance=100000.0, risk_pct=args.risk, tp_r=2.0)
    paires = instruments()
    familles = list(st.REGISTRY)
    total = len(paires) * len(familles)

    entete = (f"RECHERCHE WALK-FORWARD : {len(paires)} marches x {len(familles)} familles "
              f"= {total} combinaisons, {args.windows} fenetres chacune")
    print("=" * 96); print("  " + entete); print("=" * 96, flush=True)
    JOURNAL.write_text(entete + "\n", encoding="utf-8")

    # Reprise : on saute ce qui a deja ete calcule lors d'un run precedent.
    deja = {}
    if PARTIEL.exists() and not args.rezero:
        for ligne in PARTIEL.read_text(encoding="utf-8").splitlines():
            try:
                b = json.loads(ligne)
                deja[(b["symbol"], b["timeframe"], b["famille"])] = b
            except Exception:
                continue
        print(f"  reprise : {len(deja)} combinaisons deja calculees", flush=True)
    elif args.rezero and PARTIEL.exists():
        PARTIEL.unlink()

    taches = [(sym, tf, fam, args.windows, args.risk)
              for sym, tf in paires for fam in familles
              if (sym, tf, fam) not in deja]
    workers = max(1, min(args.jobs or (cpu_count() - 4), len(taches)))
    print(f"  {workers} processus en parallele sur {cpu_count()} coeurs logiques", flush=True)

    resultats = list(deja.values())
    survivants = [b for b in resultats if retenu(b)]
    echecs = {}
    for b in resultats:
        for motif in raisons_echec(b):
            echecs[motif] = echecs.get(motif, 0) + 1          # critere -> nombre de combinaisons qu'il elimine
    fait = 0
    t0 = time.time()

    # imap_unordered : on recupere chaque resultat des qu'il tombe, ce qui
    # permet d'afficher l'avancement sans attendre la fin du lot.
    with Pool(processes=workers) as pool:
        for b in pool.imap_unordered(_tache, taches, chunksize=1):
            fait += 1
            if b is not None:
                resultats.append(b)
                with open(PARTIEL, "a", encoding="utf-8") as h:
                    h.write(json.dumps(b) + chr(10))
                for motif in raisons_echec(b):
                    echecs[motif] = echecs.get(motif, 0) + 1
                if retenu(b):
                    survivants.append(b)
                    ligne = ("  RETENU  %-9s %-3s %-17s fen+ %3.0f%%  PF %.2f  "
                             "long %.2f short %.2f  sansTop5 %.2f  DD %.1f%%  n=%d"
                             % (b["symbol"], b["timeframe"], b["famille"],
                                b["pct_positives"], b["pf_out"], b["pf_long"],
                                b["pf_short"], b["pf_sans_top5"],
                                b["dd_out_pct"], b["trades_out"]))
                    print(ligne, flush=True)
                    with open(JOURNAL, "a", encoding="utf-8") as h:
                        h.write(ligne + chr(10))
            if fait % 20 == 0 or fait == len(taches):
                ecoule = time.time() - t0
                reste = ecoule / fait * (len(taches) - fait)
                print("  ... %d/%d  (%.0f min restantes, %d evaluables, %d retenus)"
                      % (fait, len(taches), reste / 60, len(resultats), len(survivants)),
                      flush=True)

    resultats.sort(key=lambda x: (-x["pct_positives"], -x["pf_out"]))
    SORTIE.write_text(json.dumps(
        {"survivants": survivants, "tous": resultats,
         "parametres": {"fenetres": args.windows, "risque": args.risk}},
        indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print("  QUEL CRITERE ELIMINE QUOI  (sur %d combinaisons evaluables)" % len(resultats))
    print("=" * 96)
    print("  %-26s %9s %9s" % ("critere", "elimine", "part"))
    print("  " + "-" * 48)
    for motif, n in sorted(echecs.items(), key=lambda x: -x[1]):
        print("  %-26s %9d %8.0f%%" % (motif, n, n / max(len(resultats), 1) * 100))
    if not echecs:
        print("  aucun critere n'elimine quoi que ce soit")

    print("\n" + "=" * 96)
    print(f"  {len(resultats)} combinaisons evaluables, {len(survivants)} retenues")
    print("=" * 96)
    if not survivants:
        print("  AUCUNE ligne ne passe le walk-forward.")
        print("  Conclusion : il n'y a pas d'edge a extraire de ce cache de donnees,")
        print("  et empiler des lignes ne fera pas baisser le drawdown utilement.")
    else:
        print("  %-9s %-3s %-17s %8s %8s %9s %6s %8s"
              % ("marche", "tf", "famille", "fen.+", "PF out", "degrad", "n", "R tot"))
        for b in sorted(survivants, key=lambda x: -x["r_total"]):
            print("  %-9s %-3s %-17s %7.0f%% %8.2f %8.1f%% %6d %8.1f"
                  % (b["symbol"], b["timeframe"], b["famille"], b["pct_positives"],
                     b["pf_out"], b["degradation_pct"], b["trades_out"], b["r_total"]))
    print(f"\n  Detail complet : {SORTIE}")


if __name__ == "__main__":
    main()
