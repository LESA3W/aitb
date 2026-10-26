# research/funding_orthogonal.py
"""
Le financement apporte-t-il une information que le PRIX ne donne pas deja ?

Le piege a eliminer
-------------------
funding_signal.py a montre que le financement eleve precede des rendements
positifs (ecart D1-D10 de -1,34% a 72h, spearman +0,04 croissant avec
l'horizon). Tentant. Mais c'est peut-etre la meme chose mesuree deux fois.

Le financement monte quand le marche monte : les longs se pressent, ils paient.
Donc « financement eleve -> le prix monte » peut n'etre qu'une reformulation de
« le prix montait -> il continue de monter ». Dans ce cas le financement
n'apporte RIEN : un simple momentum de prix ferait aussi bien, et on retombe
exactement sur les familles qui ont echoue 4 500 fois en forex.

C'est le meme piege que l'alpha contre le beta qui a tue toutes les candidates
forex, sous une autre forme.

Le test
-------
Regression du rendement futur sur DEUX variables :
    rendement futur  ~  rendement passe  +  financement

Si le coefficient du financement reste significatif une fois le rendement passe
pris en compte, alors le financement porte une information propre. Sinon, il
est redondant et l'idee est morte.

On mesure aussi la correlation entre financement et rendement passe : si elle
est tres elevee, les deux variables disent litteralement la meme chose.

Restriction : seuls les marches avec au moins 1500 points (environ 500 jours)
sont retenus. Les nouvelles cotations a 350 points produisaient des ecarts de
5% qui polluaient la moyenne.

Usage:
    python research/funding_orthogonal.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                          # noqa: E402

from research.funding_signal import funding_history, klines_8h, perp_symbols  # noqa: E402

MIN_POINTS = 1500          # ~500 jours d'historique 8h
HORIZONS = (("8h", 1), ("24h", 3), ("72h", 9))
LOOKBACK = 3               # rendement passe sur 24h, meme echelle que le signal


def regression(y, X):
    """
    Moindres carres avec constante. Retourne (coefficients, t-stats, R2).
    Ecrit a la main pour ne dependre que de numpy.
    """
    n = len(y)
    A = np.column_stack([np.ones(n)] + [x for x in X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    ddl = n - A.shape[1]
    if ddl <= 0:
        return beta, np.zeros_like(beta), 0.0
    s2 = float(resid @ resid) / ddl
    try:
        cov = s2 * np.linalg.inv(A.T @ A)
        se = np.sqrt(np.clip(np.diag(cov), 1e-30, None))
        t = beta / se
    except np.linalg.LinAlgError:
        t = np.zeros_like(beta)
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / sst if sst > 0 else 0.0
    return beta, t, r2


def preparer(symbol, months=24):
    f = funding_history(symbol, months)
    k = klines_8h(symbol, months)
    if len(f) < MIN_POINTS or len(k) < MIN_POINTS:
        return None
    prix = {t: c for t, c in k}
    temps = sorted(prix)
    closes = np.array([prix[t] for t in temps], dtype=float)

    pos, taux = [], []
    for ts, tx in f:
        p = np.searchsorted(temps, ts) - 1
        if 0 <= p < len(temps):
            pos.append(p)
            taux.append(tx)
    if len(pos) < MIN_POINTS:
        return None
    return np.array(pos), np.array(taux, dtype=float), closes


def main():
    syms = perp_symbols(30)
    print("=" * 96)
    print("  LE FINANCEMENT APPORTE-T-IL PLUS QUE LE PRIX ?")
    print("  regression : rendement futur ~ rendement passe (24h) + financement")
    print(f"  marches retenus : au moins {MIN_POINTS} points de financement")
    print("=" * 96, flush=True)

    lignes = []
    for s in syms:
        try:
            prep = preparer(s)
        except Exception:
            continue
        if prep is None:
            continue
        pos, taux, closes = prep

        for nom, nb in HORIZONS:
            y, x_pass, x_fund = [], [], []
            for i, p in enumerate(pos):
                if p - LOOKBACK < 0 or p + nb >= len(closes):
                    continue
                y.append((closes[p + nb] / closes[p] - 1.0) * 100.0)
                x_pass.append((closes[p] / closes[p - LOOKBACK] - 1.0) * 100.0)
                x_fund.append(taux[i] * 100.0)      # en %
            if len(y) < 500:
                continue
            y = np.array(y); xp = np.array(x_pass); xf = np.array(x_fund)

            _, t_seul, r2_seul = regression(y, [xf])
            beta, t_duo, r2_duo = regression(y, [xp, xf])
            corr = float(np.corrcoef(xp, xf)[0, 1])
            lignes.append({
                "symbol": s, "horizon": nom, "n": len(y),
                "t_fund_seul": float(t_seul[1]),
                "t_fund_avec_prix": float(t_duo[2]),
                "t_prix": float(t_duo[1]),
                "corr_prix_fund": corr,
                "r2_duo": r2_duo,
            })
        print("  %-14s traite" % s, flush=True)

    print("\n" + "=" * 96)
    print("  RESULTATS  (t-stat : au-dela de |2| le coefficient est significatif)")
    print("=" * 96)
    print("  %-8s %10s %12s %16s %12s %12s"
          % ("horizon", "marches", "t fund seul", "t fund + prix", "t prix", "corr px/fund"))
    print("  " + "-" * 84)
    for nom, _ in HORIZONS:
        lot = [l for l in lignes if l["horizon"] == nom]
        if not lot:
            continue
        print("  %-8s %10d %12.2f %16.2f %12.2f %12.2f"
              % (nom, len(lot),
                 float(np.mean([l["t_fund_seul"] for l in lot])),
                 float(np.mean([l["t_fund_avec_prix"] for l in lot])),
                 float(np.mean([l["t_prix"] for l in lot])),
                 float(np.mean([l["corr_prix_fund"] for l in lot]))))

    print("\n  DETAIL PAR MARCHE, horizon 72h")
    print("  %-14s %8s %14s %16s %14s"
          % ("marche", "n", "t fund seul", "t fund + prix", "corr px/fund"))
    print("  " + "-" * 74)
    for l in sorted([x for x in lignes if x["horizon"] == "72h"],
                    key=lambda x: -abs(x["t_fund_avec_prix"])):
        print("  %-14s %8d %14.2f %16.2f %14.2f"
              % (l["symbol"], l["n"], l["t_fund_seul"],
                 l["t_fund_avec_prix"], l["corr_prix_fund"]))

    (PROJECT_ROOT / "data" / "funding" / "orthogonal.json").write_text(
        json.dumps(lignes, indent=2), encoding="utf-8")

    print("\n  Lecture :")
    print("   - si t fund + prix reste au-dela de |2|, le financement porte une")
    print("     information PROPRE, que le prix seul ne donne pas -> piste vivante")
    print("   - s'il s'effondre vers 0 alors que t fund seul etait significatif,")
    print("     le financement n'etait qu'un reflet du prix -> piste morte")


if __name__ == "__main__":
    main()
