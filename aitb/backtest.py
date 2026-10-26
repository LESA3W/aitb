# aitb/backtest.py
"""
Moteur de backtest bougie par bougie.

Principes non negociables, chacun corrige une erreur qui tue les backtests
optimistes que l'on voit partout :

1. Execution differee. Le signal calcule sur la bougie i cloturee s'execute a
   l'ouverture de la bougie i+1. Jamais au prix de cloture de la bougie qui a
   genere le signal, sinon on achete a un prix qu'on ne connaissait pas encore.

2. Frais toujours actifs. Commission, spread et slippage sont appliques a chaque
   trade et ne peuvent pas etre desactives. Sur du scalping, ce sont eux qui
   decident si la strategie est rentable ou non.

3. Hypothese pessimiste sur les bougies ambigues. Si le stop et le take profit
   sont tous les deux dans le range d'une meme bougie, on considere que le stop
   a ete touche en premier. On ne connait pas l'ordre intrabougie, donc on prend
   toujours la version qui nous dessert.

4. Sizing par risque. Le volume est calcule pour que la distance jusqu'au stop
   represente exactement risk_pct % du capital courant.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


# ==================== PARAMETRES ====================

@dataclass
class Costs:
    """
    Couts de transaction.

    Deux modeles de commission, cumulables :
      - par lot, comme sur le forex chez un broker RAW. Defaut IC Markets :
        3.50 USD par lot et par sens, soit 7.00 USD aller-retour.
      - en pourcentage du notionnel, comme sur les exchanges crypto. Binance
        spot facture 0.10 % par sens, soit 0.20 % aller-retour.

    Sans ce second modele, un backtest crypto sous-estimerait massivement les
    frais et fabriquerait un edge qui n'existe pas.
    """
    commission_per_lot_round_turn: float = 7.0
    commission_pct_round_turn: float = 0.0   # en %, ex 0.2 pour Binance spot
    spread_points: float = None      # None = utilise le spread reel de chaque bougie
    slippage_points: float = 2.0     # applique a l'entree ET a la sortie


@dataclass
class BacktestConfig:
    """
    tp_r a None desactive le take profit : la position ne se ferme alors que
    sur stop, sur signal de sortie, ou en fin de donnees.

    C'est indispensable pour le suivi de tendance. Ces systemes tirent tout
    leur edge d'une poignee de trades a +10R ou +50R qui payent des dizaines
    de petits perdants. Un take profit a 2R coupe exactement cette queue de
    distribution et transforme une strategie gagnante en strategie perdante.
    Bug identifie le 2026-09-10 : toutes les regles universelles testees, y
    compris le systeme des Turtles, ressortaient perdantes uniquement a cause
    du plafond a 2R impose par le moteur.
    """
    initial_balance: float = 1000.0
    risk_pct: float = 1.0
    # Risque en MONTANT FIXE. A None, ce champ est inerte et risk_pct s'applique
    # exactement comme avant : aucun appelant existant n'est affecte.
    #
    # Pourquoi il existe. TheMacroTape, note "comprendre-et-maitriser-la-volatilite" :
    # risquer un pourcentage du capital courant fait COMPOSER les resultats, et la
    # composition transforme la volatilite en perte seche. Sur 200 000 comptes
    # simules a esperance identique, ceux a forte variance finissent a -13 % quand
    # ceux a faible variance font x2,4. "Si tu risques 100 EUR fixe, le phenomene
    # disparait. La volatilite n'est pas le probleme, c'est l'acte de composer."
    risk_eur: float = None
    tp_r: float = 2.0                # None = aucun take profit, on laisse courir
    use_breakeven: bool = True
    be_trigger_r: float = 1.0        # deplace le stop a l'entree apres +1R
    use_trailing: bool = False
    trail_r: float = 1.5             # distance du trailing en R
    trail_from_start: bool = False   # True = trailing des l'entree, sans attendre le BE
    max_bars_in_trade: int = 0       # 0 = pas de limite de duree
    allow_long: bool = True
    allow_short: bool = True
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01


@dataclass
class Trade:
    index_in: int
    index_out: int
    time_in: int
    time_out: int
    side: int                 # +1 long, -1 short
    volume: float
    entry: float
    exit: float
    sl: float
    tp: float
    reason: str               # sl, tp, trail, be, timeout, end
    gross_usd: float
    commission_usd: float
    net_usd: float
    pips: float
    bars_held: int
    risk_usd: float = 0.0     # montant risque a l'entree, sert a calculer le R
    r_multiple: float = 0.0   # resultat en multiples du risque, comparable entre marches


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    timeframe: str
    trades: list = field(default_factory=list)
    equity: np.ndarray = None
    metrics: dict = field(default_factory=dict)

    def passes(self, min_trades=100, max_dd_pct=20.0, min_profit_factor=1.3, must_beat_buy_hold=True) -> bool:
        """
        Grille d'acceptation. Les seuils viennent du retour d'experience de
        DaviddTech sur 2000 backtests dont plus de 1600 ont echoue en live.
        Une strategie qui ne passe pas cette grille ne merite pas un forward test.
        """
        m = self.metrics
        if m.get("trades", 0) < min_trades:
            return False
        if m.get("max_drawdown_pct", 100.0) > max_dd_pct:
            return False
        if m.get("profit_factor", 0.0) < min_profit_factor:
            return False
        if must_beat_buy_hold and m.get("return_pct", -1e9) <= m.get("buy_hold_pct", 0.0):
            return False
        return True

    def summary(self) -> str:
        m = self.metrics
        return (
            f"{self.strategy:46s} {self.symbol:8s} {self.timeframe:4s} "
            f"PF={m.get('profit_factor', 0):5.2f} "
            f"ret={m.get('return_pct', 0):8.1f}% "
            f"DD={m.get('max_drawdown_pct', 0):5.1f}% "
            f"n={m.get('trades', 0):5d} "
            f"win={m.get('win_rate', 0):5.1f}% "
            f"exp={m.get('expectancy_usd', 0):7.3f}$ "
            f"B&H={m.get('buy_hold_pct', 0):7.1f}%"
        )


# ==================== MOTEUR ====================

def run(rates: np.ndarray,
        signals: np.ndarray,
        sl_dist: np.ndarray,
        specs: dict,
        config: BacktestConfig = None,
        costs: Costs = None,
        default_sl_price: float = None,
        strategy_name: str = "",
        timeframe: str = "",
        exit_signals: np.ndarray = None) -> BacktestResult:
    """
    Rejoue la serie complete. Une seule position ouverte a la fois : un signal
    contraire pendant une position est ignore, il n'y a ni empilement ni hedging.
    """
    config = config or BacktestConfig()
    costs = costs or Costs()

    n = len(rates)
    point = float(specs["point"])
    pip = float(specs["pip"])
    tick_size = float(specs["tick_size"])
    tick_value = float(specs["tick_value"])

    vol_min = max(config.volume_min, float(specs.get("volume_min", 0.01)))
    vol_max = min(config.volume_max, float(specs.get("volume_max", 100.0)))
    vol_step = max(config.volume_step, float(specs.get("volume_step", 0.01)))

    opens = rates["open"].astype("f8")
    highs = rates["high"].astype("f8")
    lows = rates["low"].astype("f8")
    closes = rates["close"].astype("f8")
    times = rates["time"].astype("i8")
    bar_spreads = rates["spread"].astype("f8")

    slippage = costs.slippage_points * point

    def spread_price(i: int) -> float:
        if costs.spread_points is not None:
            return costs.spread_points * point
        s = bar_spreads[i]
        # Certains historiques ne portent pas le spread. On retombe alors sur
        # une valeur prudente plutot que sur zero, qui flatterait le resultat.
        if s <= 0:
            return 10.0 * point
        return s * point

    def money(price_diff: float, volume: float) -> float:
        return (price_diff / tick_size) * tick_value * volume

    # Nombre de decimales du pas de volume. Sur le forex c'est 2 (lot 0.01),
    # sur la crypto ca peut aller a 6 (0.000001 BTC). Arrondir en dur a 2
    # ramenerait toute quantite crypto a zero et le backtest ne prendrait
    # plus aucun trade.
    _vol_decimals = max(0, min(8, len(f"{vol_step:.8f}".rstrip("0").split(".")[1])))

    def round_volume(v: float) -> float:
        stepped = np.floor(v / vol_step) * vol_step
        return float(round(min(max(stepped, 0.0), vol_max), _vol_decimals))

    balance = config.initial_balance
    equity_points = [config.initial_balance]
    trades = []
    skipped_undersized = 0

    pos = None  # dict de la position ouverte

    for i in range(n - 1):
        # ---------- gestion d'une position ouverte sur la bougie i ----------
        if pos is not None:
            hi, lo = highs[i], lows[i]
            side = pos["side"]
            hit = None
            exit_price = None

            # Break-even : on le teste AVANT le stop courant, car il peut
            # transformer une perte en sortie a zero sur cette meme bougie.
            if config.use_breakeven and not pos["be_done"]:
                trigger = pos["entry"] + side * config.be_trigger_r * pos["risk_dist"]
                reached = hi >= trigger if side > 0 else lo <= trigger
                if reached:
                    pos["sl"] = pos["entry"]
                    pos["be_done"] = True

            if config.use_trailing and (pos["be_done"] or config.trail_from_start):
                trail_dist = config.trail_r * pos["risk_dist"]
                if side > 0:
                    pos["sl"] = max(pos["sl"], hi - trail_dist)
                else:
                    pos["sl"] = min(pos["sl"], lo + trail_dist)

            sl_touched = lo <= pos["sl"] if side > 0 else hi >= pos["sl"]
            tp_touched = False
            if pos["tp"] is not None:
                tp_touched = hi >= pos["tp"] if side > 0 else lo <= pos["tp"]

            # Hypothese pessimiste : si les deux sont dans la bougie, le stop gagne.
            if sl_touched:
                hit = "be" if abs(pos["sl"] - pos["entry"]) < point else ("trail" if pos["be_done"] and config.use_trailing else "sl")
                exit_price = pos["sl"]
            elif tp_touched:
                hit = "tp"
                exit_price = pos["tp"]
            elif exit_signals is not None and exit_signals[i] != 0 and i > pos["index_in"]:
                # Sortie sur regle de la strategie : cassure inverse, croisement,
                # retournement de momentum. C'est ainsi que sortent les systemes
                # de suivi de tendance, pas sur un objectif chiffre.
                hit = "signal"
                exit_price = closes[i]
            elif config.max_bars_in_trade and (i - pos["index_in"]) >= config.max_bars_in_trade:
                hit = "timeout"
                exit_price = closes[i]

            if hit is not None:
                exit_fill = exit_price - side * slippage
                gross = money((exit_fill - pos["entry"]) * side, pos["volume"])
                commission = (costs.commission_per_lot_round_turn * pos["volume"]
                              + costs.commission_pct_round_turn / 100.0
                              * pos["entry"] * pos["volume"])
                net = gross - commission
                balance += net
                trades.append(Trade(
                    index_in=pos["index_in"], index_out=i,
                    time_in=int(times[pos["index_in"]]), time_out=int(times[i]),
                    side=side, volume=pos["volume"],
                    entry=pos["entry"], exit=exit_fill,
                    sl=pos["sl"], tp=pos["tp"] if pos["tp"] is not None else 0.0, reason=hit,
                    gross_usd=gross, commission_usd=commission, net_usd=net,
                    pips=((exit_fill - pos["entry"]) * side) / pip,
                    bars_held=i - pos["index_in"],
                    risk_usd=pos["risk_usd"],
                    r_multiple=(net / pos["risk_usd"]) if pos["risk_usd"] > 0 else 0.0,
                ))
                equity_points.append(balance)
                pos = None

        # ---------- ouverture sur le signal de la bougie i, execute en i+1 ----------
        if pos is None and signals[i] != 0:
            side = int(signals[i])
            if (side > 0 and not config.allow_long) or (side < 0 and not config.allow_short):
                continue

            dist = sl_dist[i]
            if not np.isfinite(dist) or dist <= 0:
                dist = default_sl_price
            if dist is None or dist <= 0:
                continue

            j = i + 1
            entry = opens[j] + side * (spread_price(j) + slippage)

            # Montant fixe quand il est demande, sinon pourcentage du capital
            # courant. Le montant fixe ne compose pas, c'est tout l'interet.
            risk_amount = (config.risk_eur if config.risk_eur is not None
                           else balance * (config.risk_pct / 100.0))
            ticks_at_risk = dist / tick_size
            if ticks_at_risk <= 0:
                continue
            raw_volume = risk_amount / (ticks_at_risk * tick_value)
            volume = round_volume(raw_volume)

            if volume < vol_min:
                # Le lot minimum du broker imposerait un risque superieur a la
                # consigne. On refuse le trade au lieu de depasser le risque.
                skipped_undersized += 1
                continue

            pos = {
                "index_in": j,
                "side": side,
                "volume": volume,
                "entry": entry,
                "risk_dist": dist,
                "risk_usd": money(dist, volume),
                "sl": entry - side * dist,
                "tp": (entry + side * config.tp_r * dist) if config.tp_r else None,
                "be_done": False,
            }

    # Cloture forcee de la position encore ouverte a la fin des donnees.
    if pos is not None:
        last = n - 1
        side = pos["side"]
        exit_fill = closes[last] - side * slippage
        gross = money((exit_fill - pos["entry"]) * side, pos["volume"])
        commission = (costs.commission_per_lot_round_turn * pos["volume"]
                      + costs.commission_pct_round_turn / 100.0
                      * pos["entry"] * pos["volume"])
        net = gross - commission
        balance += net
        trades.append(Trade(
            index_in=pos["index_in"], index_out=last,
            time_in=int(times[pos["index_in"]]), time_out=int(times[last]),
            side=side, volume=pos["volume"], entry=pos["entry"], exit=exit_fill,
            sl=pos["sl"], tp=pos["tp"] if pos["tp"] is not None else 0.0, reason="end",
            gross_usd=gross, commission_usd=commission, net_usd=net,
            pips=((exit_fill - pos["entry"]) * side) / pip,
            bars_held=last - pos["index_in"],
            risk_usd=pos["risk_usd"],
            r_multiple=(net / pos["risk_usd"]) if pos["risk_usd"] > 0 else 0.0,
        ))
        equity_points.append(balance)

    equity = np.array(equity_points, dtype="f8")
    metrics = _metrics(trades, equity, closes, times, config)
    metrics["skipped_undersized"] = skipped_undersized

    return BacktestResult(
        strategy=strategy_name,
        symbol=specs.get("symbol", ""),
        timeframe=timeframe,
        trades=trades,
        equity=equity,
        metrics=metrics,
    )


# ==================== METRIQUES ====================

def _metrics(trades, equity, closes, times, config) -> dict:
    n_trades = len(trades)
    start_balance = float(equity[0])
    end_balance = float(equity[-1])

    buy_hold = (closes[-1] / closes[0] - 1.0) * 100.0 if closes[0] > 0 else 0.0
    span_days = max((int(times[-1]) - int(times[0])) / 86400.0, 1.0)

    base = {
        "trades": n_trades,
        "start_balance": round(start_balance, 2),
        "end_balance": round(end_balance, 2),
        "net_profit": round(end_balance - start_balance, 2),
        "return_pct": round((end_balance / start_balance - 1.0) * 100.0, 2),
        "buy_hold_pct": round(buy_hold, 2),
        "span_days": round(span_days, 1),
        "profit_factor": 0.0,
        "win_rate": 0.0,
        "max_drawdown_pct": 0.0,
        "expectancy_usd": 0.0,
        "expectancy_pips": 0.0,
        "avg_win_usd": 0.0,
        "avg_loss_usd": 0.0,
        "total_commission": 0.0,
        "sharpe": 0.0,
        "trades_per_month": 0.0,
    }
    if n_trades == 0:
        return base

    nets = np.array([t.net_usd for t in trades], dtype="f8")
    pips = np.array([t.pips for t in trades], dtype="f8")
    commissions = float(sum(t.commission_usd for t in trades))

    wins = nets[nets > 0]
    losses = nets[nets < 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0

    # Drawdown sur la courbe d'equity a trades clotures.
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd.max() * 100.0)

    # Sharpe approxime a partir des rendements par trade, annualise par la
    # frequence reelle observee. C'est une indication de regularite, pas une
    # mesure academique.
    returns = nets / start_balance
    if returns.std(ddof=0) > 0:
        trades_per_year = n_trades / (base["span_days"] / 365.25)
        sharpe = float(returns.mean() / returns.std(ddof=0) * np.sqrt(max(trades_per_year, 1.0)))
    else:
        sharpe = 0.0

    base.update({
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "win_rate": round(len(wins) / n_trades * 100.0, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "expectancy_usd": round(float(nets.mean()), 4),
        "expectancy_pips": round(float(pips.mean()), 3),
        "avg_win_usd": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss_usd": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "total_commission": round(commissions, 2),
        "commission_share_pct": round(commissions / max(gross_win, 1e-9) * 100.0, 1),
        "sharpe": round(sharpe, 2),
        "trades_per_month": round(n_trades / (base["span_days"] / 30.44), 1),
        "avg_bars_held": round(float(np.mean([t.bars_held for t in trades])), 1),
        "exit_reasons": _count_reasons(trades),
    })
    return base


def _count_reasons(trades) -> dict:
    counts = {}
    for t in trades:
        counts[t.reason] = counts.get(t.reason, 0) + 1
    return counts


# ==================== HELPER HAUT NIVEAU ====================

def backtest_strategy(strategy, rates, specs, timeframe,
                      config: BacktestConfig = None, costs: Costs = None) -> BacktestResult:
    """Raccourci : calcule les signaux puis rejoue la serie."""
    signals, sl_dist = strategy.signals(rates)
    return run(
        rates=rates,
        signals=signals,
        sl_dist=sl_dist,
        specs=specs,
        config=config,
        costs=costs,
        strategy_name=repr(strategy),
        timeframe=timeframe,
    )


def format_report(result: BacktestResult) -> str:
    """Rapport lisible d'un seul backtest."""
    m = result.metrics
    first = datetime.fromtimestamp(result.trades[0].time_in) if result.trades else None
    last = datetime.fromtimestamp(result.trades[-1].time_out) if result.trades else None

    lines = [
        "=" * 74,
        f"  {result.strategy}",
        f"  {result.symbol} {result.timeframe}"
        + (f"   {first:%Y-%m-%d} -> {last:%Y-%m-%d}" if first else ""),
        "=" * 74,
        f"  Capital        {m['start_balance']:>12.2f} -> {m['end_balance']:>12.2f}  ({m['return_pct']:+.2f}%)",
        f"  Buy and hold   {m['buy_hold_pct']:>12.2f}%",
        f"  Profit factor  {m['profit_factor']:>12.3f}",
        f"  Max drawdown   {m['max_drawdown_pct']:>12.2f}%",
        f"  Trades         {m['trades']:>12d}   ({m.get('trades_per_month', 0)}/mois)",
        f"  Win rate       {m['win_rate']:>12.2f}%",
        f"  Esperance      {m['expectancy_usd']:>12.4f}$   ({m['expectancy_pips']:+.2f} pips/trade)",
        f"  Gain moyen     {m['avg_win_usd']:>12.3f}$",
        f"  Perte moyenne  {m['avg_loss_usd']:>12.3f}$",
        f"  Commissions    {m['total_commission']:>12.2f}$   ({m.get('commission_share_pct', 0)}% du gain brut)",
        f"  Sharpe         {m['sharpe']:>12.2f}",
        f"  Sorties        {m.get('exit_reasons', {})}",
    ]
    if m.get("skipped_undersized"):
        lines.append(f"  Trades refuses {m['skipped_undersized']:>12d}   (lot minimum > risque autorise)")

    verdict = "ACCEPTEE" if result.passes() else "REJETEE"
    lines.append("-" * 74)
    lines.append(f"  Grille d'acceptation: {verdict}")
    if not result.passes():
        why = []
        if m["trades"] < 100:
            why.append(f"moins de 100 trades ({m['trades']})")
        if m["max_drawdown_pct"] > 20.0:
            why.append(f"drawdown {m['max_drawdown_pct']}% > 20%")
        if m["profit_factor"] < 1.3:
            why.append(f"profit factor {m['profit_factor']} < 1.3")
        if m["return_pct"] <= m["buy_hold_pct"]:
            why.append(f"ne bat pas le buy and hold ({m['return_pct']}% vs {m['buy_hold_pct']}%)")
        lines.append(f"  Motif: {', '.join(why)}")
    lines.append("=" * 74)
    return "\n".join(lines)
