# aitb/indicators.py
"""
Indicateurs techniques en numpy pur, sans dependance externe.

Regle absolue respectee partout dans ce fichier : la valeur a l'index i
n'utilise QUE des donnees d'index <= i. Aucune fonction ne regarde vers l'avant.
C'est ce qui garantit qu'un backtest et le live produisent le meme signal.

Les periodes de chauffe renvoient np.nan, jamais une valeur approchee.
"""

import numpy as np


def sma(values: np.ndarray, period: int) -> np.ndarray:
    values = np.asarray(values, dtype="f8")
    out = np.full(len(values), np.nan)
    if period <= 0 or len(values) < period:
        return out
    cumsum = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    values = np.asarray(values, dtype="f8")
    out = np.full(len(values), np.nan)
    if period <= 0 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def rolling_std(values: np.ndarray, period: int) -> np.ndarray:
    values = np.asarray(values, dtype="f8")
    out = np.full(len(values), np.nan)
    if period <= 1 or len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        out[i] = values[i - period + 1:i + 1].std(ddof=0)
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    high = np.asarray(high, dtype="f8")
    low = np.asarray(low, dtype="f8")
    close = np.asarray(close, dtype="f8")
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return tr


def atr(high, low, close, period: int = 14) -> np.ndarray:
    """ATR lisse facon Wilder, la version utilisee par MT5."""
    tr = true_range(high, low, close)
    out = np.full(len(tr), np.nan)
    if len(tr) < period + 1:
        return out
    out[period] = tr[1:period + 1].mean()
    for i in range(period + 1, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    close = np.asarray(close, dtype="f8")
    out = np.full(len(close), np.nan)
    if len(close) < period + 1:
        return out
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def donchian(high: np.ndarray, low: np.ndarray, period: int):
    """
    Plus haut et plus bas des 'period' bougies PRECEDENTES, bougie courante exclue.
    L'exclusion est volontaire : inclure la bougie courante rendrait la cassure
    detectable au moment meme ou elle se produit, ce qui est du look-ahead.
    """
    high = np.asarray(high, dtype="f8")
    low = np.asarray(low, dtype="f8")
    n = len(high)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    if n < period + 1:
        return upper, lower
    for i in range(period, n):
        window_h = high[i - period:i]
        window_l = low[i - period:i]
        upper[i] = window_h.max()
        lower[i] = window_l.min()
    return upper, lower


def bollinger(close: np.ndarray, period: int = 20, mult: float = 2.0):
    mid = sma(close, period)
    sd = rolling_std(close, period)
    return mid + mult * sd, mid, mid - mult * sd


def keltner(high, low, close, period: int = 20, atr_period: int = 10, mult: float = 1.5):
    mid = ema(close, period)
    rng = atr(high, low, close, atr_period)
    return mid + mult * rng, mid, mid - mult * rng


def macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(np.nan_to_num(line, nan=0.0), signal)
    sig[np.isnan(line)] = np.nan
    return line, sig, line - sig


def adx(high, low, close, period: int = 14) -> np.ndarray:
    """
    Force de tendance. Sert de filtre : sous 20 le marche est plat,
    et les strategies de cassure y perdent leur edge en frais.
    """
    high = np.asarray(high, dtype="f8")
    low = np.asarray(low, dtype="f8")
    n = len(high)
    out = np.full(n, np.nan)
    if n < 2 * period + 1:
        return out

    up_move = np.diff(high)
    down_move = -np.diff(low)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(high, low, close)[1:]

    atr_s = tr[:period].sum()
    plus_s = plus_dm[:period].sum()
    minus_s = minus_dm[:period].sum()

    dx_values = []
    for i in range(period, len(tr)):
        atr_s = atr_s - atr_s / period + tr[i]
        plus_s = plus_s - plus_s / period + plus_dm[i]
        minus_s = minus_s - minus_s / period + minus_dm[i]
        if atr_s == 0:
            dx_values.append(0.0)
            continue
        plus_di = 100.0 * plus_s / atr_s
        minus_di = 100.0 * minus_s / atr_s
        denom = plus_di + minus_di
        dx_values.append(0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom)

        if len(dx_values) == period:
            out[i + 1] = float(np.mean(dx_values))
        elif len(dx_values) > period:
            out[i + 1] = (out[i] * (period - 1) + dx_values[-1]) / period
    return out


def slope(values: np.ndarray, period: int = 5) -> np.ndarray:
    """Pente simple sur 'period' bougies, en unites de prix par bougie."""
    values = np.asarray(values, dtype="f8")
    out = np.full(len(values), np.nan)
    if len(values) <= period:
        return out
    out[period:] = (values[period:] - values[:-period]) / period
    return out


def session_hour(times: np.ndarray) -> np.ndarray:
    """Heure UTC de chaque bougie, pour filtrer les sessions mortes."""
    times = np.asarray(times, dtype="i8")
    return ((times // 3600) % 24).astype("i4")
