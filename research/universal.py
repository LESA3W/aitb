# research/universal.py
"""
Recherche d'edge par regle UNIVERSELLE, sans optimisation par marche.

Pourquoi tout a echoue jusqu'ici
--------------------------------
Le walk-forward du 2026-09-10 a rendu un verdict sans appel : 1 fenetre sur 8
positive, profit factor out of sample 0.96, degradation 125%. Autrement dit,
choisir les meilleurs parametres marche par marche ne produit aucun edge
reproductible. On mesurait le residu de 10 750 tirages, pas un phenomene.

Le changement de methode
------------------------
Les fonds trend-following ne cherchent pas le meilleur reglage par marche. Ils
appliquent UNE regle, avec des parametres de manuel jamais optimises, a des
dizaines de marches en parallele. L'edge ne vient pas de la finesse du reglage,
il vient de la diversification et de la persistance d'un phenomene reel :
les marches qui montent ont tendance a continuer un moment.

Consequences methodologiques :
  - aucun parametre n'est choisi sur les donnees, donc rien a sur-apprendre
  - un seul jeu de parametres pour TOUS les marches
  - on juge le PORTEFEUILLE, jamais un marche isole
  - le walk-forward devient presque inutile puisqu'il n'y a pas de selection,
    mais on le fait quand meme pour verifier la stabilite dans le temps

Les regles testees sont publiques et documentees depuis des decennies :
  - Donchian 20/55 : systeme des Turtles, Richard Dennis, 1983
  - Momentum temporel 12 mois : Moskowitz, Ooi, Pedersen (2012), teste sur
    58 instruments et 25 ans
  - Croisement 50/200 : la moyenne mobile la plus utilisee au monde

Usage:
    python research/universal.py
    python research/universal.py --risk 0.5 --capital 100000
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                    # noqa: E402

from aitb import indicators as ind                    # noqa: E402
from aitb import market_data as md                    # noqa: E402
from aitb.backtest import BacktestConfig, Costs, run  # noqa: E402


# ==================== UNIVERS DE MARCHES ====================
# Le plus large possible. C'est la diversification qui fait l'edge,
# pas le choix des marches.
FOREX = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
         "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURAUD"]
METAUX = ["XAUUSD", "XAGUSD"]
INDICES = ["DE40", "US500", "USTEC", "US30", "JPN225"]
CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]


def specs_from(meta, symbol):
    return {"symbol": symbol,
            **{k: meta[k] for k in ("point", "pip", "tick_size", "tick_value",
                                    "volume_min", "volume_max", "volume_step")}}


def costs_for(meta, rates, specs):
    """Frais reels selon la source des donnees."""
    if meta.get("source") == "binance_spot":
        mp = float(np.median(rates["close"]))
        return Costs(commission_per_lot_round_turn=0.0,
                     commission_pct_round_turn=float(meta.get("commission_pct_round_turn", 0.20)),
                     spread_points=max(1.0, (0.0002 * mp) / specs["point"]),
                     slippage_points=max(1.0, (0.0001 * mp) / specs["point"]))
    return Costs(commission_per_lot_round_turn=7.0, slippage_points=2.0)


# ==================== REGLES UNIVERSELLES ====================
# Aucun de ces parametres n'a ete choisi en regardant nos donnees.
# Ce sont les valeurs publiees d'origine.

def turtle(rates, entry=20, exit_len=10, atr_stop=2.0):
    """
    Systeme des Turtles, version fidele.

    Entree : cassure du canal de Donchian sur 'entry' bougies.
    Stop   : 2 ATR.
    SORTIE : cassure du canal INVERSE sur 'exit_len' bougies. Pas de take profit.
             C'est ce qui laisse courir les gros mouvements, et c'est la source
             de tout l'edge du systeme.
    """
    high, low, close = rates["high"], rates["low"], rates["close"]
    up, lo = ind.donchian(high, low, entry)
    xu, xl = ind.donchian(high, low, exit_len)
    rng = ind.atr(high, low, close, 20)
    n = len(rates)
    sig = np.zeros(n, dtype="i1")
    sl = np.full(n, np.nan)
    ex = np.zeros(n, dtype="i1")
    for i in range(n):
        if np.isnan(up[i]) or np.isnan(rng[i]) or rng[i] <= 0:
            continue
        if close[i] > up[i]:
            sig[i], sl[i] = 1, atr_stop * rng[i]
        elif close[i] < lo[i]:
            sig[i], sl[i] = -1, atr_stop * rng[i]
        # Sortie : le prix casse le canal court dans le sens contraire.
        if not np.isnan(xl[i]) and (close[i] < xl[i] or close[i] > xu[i]):
            ex[i] = 1
    return sig, sl, ex


def tsmom(rates, lookback=252, atr_stop=2.0):
    """
    Momentum temporel. Si le rendement sur lookback bougies est positif, long.
    Moskowitz Ooi Pedersen 2012 : teste sur 58 instruments, 25 ans.
    On n'entre que sur un CHANGEMENT de signe pour eviter d'empiler.
    """
    close = rates["close"]
    high, low = rates["high"], rates["low"]
    rng = ind.atr(high, low, close, 20)
    n = len(rates)
    sig = np.zeros(n, dtype="i1")
    sl = np.full(n, np.nan)
    ex = np.zeros(n, dtype="i1")
    for i in range(lookback + 1, n):
        if np.isnan(rng[i]) or rng[i] <= 0:
            continue
        prev = np.sign(close[i - 1] - close[i - 1 - lookback])
        cur = np.sign(close[i] - close[i - lookback])
        if cur != 0 and cur != prev:
            sig[i], sl[i] = int(cur), atr_stop * rng[i]
            ex[i] = 1   # tout retournement de momentum ferme la position en cours
    return sig, sl, ex


def golden_cross(rates, fast=50, slow=200, atr_stop=2.0):
    """Croisement 50/200, la moyenne mobile la plus utilisee au monde."""
    close = rates["close"]
    ef, es = ind.sma(close, fast), ind.sma(close, slow)
    rng = ind.atr(rates["high"], rates["low"], close, 20)
    n = len(rates)
    sig = np.zeros(n, dtype="i1")
    sl = np.full(n, np.nan)
    ex = np.zeros(n, dtype="i1")
    for i in range(1, n):
        if np.isnan(ef[i]) or np.isnan(es[i]) or np.isnan(ef[i-1]) or np.isnan(es[i-1]):
            continue
        if np.isnan(rng[i]) or rng[i] <= 0:
            continue
        if ef[i-1] <= es[i-1] and ef[i] > es[i]:
            sig[i], sl[i] = 1, atr_stop * rng[i]
            ex[i] = 1
        elif ef[i-1] >= es[i-1] and ef[i] < es[i]:
            sig[i], sl[i] = -1, atr_stop * rng[i]
            ex[i] = 1
    return sig, sl, ex


RULES = {
    "turtle_20_10": lambda r: turtle(r, 20, 10, 2.0),
    "turtle_55_20": lambda r: turtle(r, 55, 20, 2.0),
    "tsmom_252": lambda r: tsmom(r, 252, 2.0),
    "tsmom_120": lambda r: tsmom(r, 120, 2.0),
    "golden_50_200": lambda r: golden_cross(r, 50, 200, 2.0),
}


# ==================== EVALUATION PORTEFEUILLE ====================

def portfolio_of(rule_name, universe, timeframe, tp_r, capital, risk):
    """
    Applique la MEME regle a tous les marches, agrege les trades dans l'ordre
    chronologique sur un compte partage. C'est le portefeuille qui est juge.
    """
    cfg = BacktestConfig(initial_balance=capital, risk_pct=risk, tp_r=tp_r)
    fn = RULES[rule_name]
    trades = []
    used = []

    for sym in universe:
        try:
            rates, meta = md.load_history(sym, timeframe)
        except Exception:
            continue
        if len(rates) < 600:
            continue
        specs = specs_from(meta, sym)
        try:
            sig, sl, ex = fn(rates)
            res = run(rates, sig, sl, specs, cfg, costs_for(meta, rates, specs),
                      strategy_name=rule_name, timeframe=timeframe, exit_signals=ex)
        except Exception:
            continue
        if res.metrics["trades"] == 0:
            continue
        used.append(sym)
        trades.extend((t.time_out, sym, t.r_multiple) for t in res.trades)

    if len(trades) < 50:
        return None

    trades.sort()
    bal = capital
    eq = [bal]
    for _, _, r in trades:
        bal += bal * (risk / 100.0) * r
        eq.append(bal)
    eq = np.array(eq)
    peak = np.maximum.accumulate(eq)
    dd = float(((peak - eq) / peak).max() * 100)
    yrs = max((trades[-1][0] - trades[0][0]) / 86400 / 365.25, 0.1)
    ann = ((eq[-1] / capital) ** (1 / yrs) - 1) * 100

    rs = np.array([r for _, _, r in trades])
    wins = rs[rs > 0].sum()
    losses = -rs[rs < 0].sum()

    # Stabilite : proportion d'annees positives.
    per_year = defaultdict(float)
    for ts, _, r in trades:
        per_year[datetime.fromtimestamp(ts).year] += r
    years_pos = sum(1 for v in per_year.values() if v > 0)

    return {
        "rule": rule_name, "tf": timeframe, "markets": len(used),
        "trades": len(trades), "ann": ann, "dd": dd,
        "pf": wins / losses if losses > 0 else 999.0,
        "expectancy_r": float(rs.mean()),
        "years_pos": years_pos, "years_tot": len(per_year),
        "ratio": ann / max(dd, 0.01),
        "week": capital * ann / 100 / 52,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--capital", type=float, default=100000.0)
    p.add_argument("--risk", type=float, default=0.5)
    args = p.parse_args()

    universes = {
        "FOREX(12)": FOREX,
        "FOREX+METAUX(14)": FOREX + METAUX,
        "TOUT SAUF CRYPTO(19)": FOREX + METAUX + INDICES,
        "CRYPTO(6)": CRYPTO,
        "TOUT(25)": FOREX + METAUX + INDICES + CRYPTO,
    }

    print("=" * 108)
    print(f"  REGLES UNIVERSELLES, parametres de manuel, ZERO optimisation")
    print(f"  compte {args.capital:,.0f}$  |  risque {args.risk}% par trade  |  objectif 250$/semaine"
          .replace(",", " "))
    print("=" * 108)
    print(f"  {'regle':14s} {'univers':22s} {'tf':3s} {'tp':4s} {'mkts':>5s} {'trades':>7s} "
          f"{'PF':>5s} {'ann%':>8s} {'DD%':>6s} {'ratio':>6s} {'$/sem':>8s} {'ans+':>6s}")
    print("  " + "-" * 104)

    rows = []
    for rule in RULES:
        for uname, uni in universes.items():
            for tf in ("H4", "H1"):
                for tp_r in (None, 3.0):
                    r = portfolio_of(rule, uni, tf, tp_r, args.capital, args.risk)
                    if not r:
                        continue
                    r["universe"] = uname
                    r["tp_r"] = tp_r
                    rows.append(r)
                    tplab = f'{int(tp_r)}R' if tp_r else 'run'
                    print(f"  {rule:14s} {uname:22s} {tf:3s} {tplab:>4s} {r['markets']:>5d} "
                          f"{r['trades']:>7d} {r['pf']:>5.2f} {r['ann']:>7.2f}% {r['dd']:>5.1f}% "
                          f"{r['ratio']:>6.2f} {r['week']:>7.0f}$ {r['years_pos']}/{r['years_tot']:>3d}")

    if not rows:
        print("  Aucun resultat.")
        return

    print("\n" + "=" * 108)
    print("  CLASSEMENT PAR RATIO RENDEMENT / DRAWDOWN")
    print("=" * 108)
    for r in sorted(rows, key=lambda x: -x["ratio"])[:10]:
        tplab2 = f"{int(r['tp_r'])}R" if r['tp_r'] else 'run'
        flag = "  <-- objectif 250$/sem atteint" if r["week"] >= 250 else ""
        print(f"  {r['rule']:14s} {r['universe']:22s} {r['tf']:3s} {tplab2:>4s}  "
              f"ann={r['ann']:7.2f}%  DD={r['dd']:5.1f}%  ratio={r['ratio']:5.2f}  "
              f"{r['week']:>7.0f}$/sem  {r['years_pos']}/{r['years_tot']} ans positifs{flag}")


if __name__ == "__main__":
    main()
