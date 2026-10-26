# aitb/strategies.py
"""
Strategies deterministes. Aucune IA dans la boucle de decision.

Contrat d'une strategie
-----------------------
Une strategie recoit les bougies et retourne deux tableaux de meme longueur :
  - signal[i] : +1 (ouvrir long), -1 (ouvrir short), 0 (ne rien faire)
  - sl_dist[i]: distance du stop en unites de prix, ou np.nan pour utiliser
                la valeur fixe des parametres

Le signal a l'index i est calcule sur la bougie i CLOTUREE, et le backtester
l'execute a l'ouverture de la bougie i+1. Meme regle en live via fetch_rates(shift=1).
C'est ce decalage qui empeche le look-ahead bias, la cause n°1 des backtests
magnifiques qui meurent en reel.

Pourquoi que du trend following et une seule mean reversion
-----------------------------------------------------------
Retour d'experience de DaviddTech sur 2000 backtests : le suivi de tendance
passe le forward test, la mean reversion est tres difficile a tenir en live
parce qu'on ne sait pas ou placer le take profit. rsi_reversion est gardee
comme temoin negatif, pas comme candidate serieuse.
"""

import numpy as np

from aitb import indicators as ind


class Strategy:
    """Classe de base. Une strategie = un nom, des params, une methode signals()."""

    name = "base"
    # Grille de recherche exploree par le sweep. Chaque cle est un param de __init__.
    param_grid = {}

    def __init__(self, **params):
        self.params = params

    def __repr__(self):
        if not self.params:
            return self.name
        inner = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({inner})"

    def signals(self, rates: np.ndarray):
        raise NotImplementedError

    @staticmethod
    def _empty(n: int):
        return np.zeros(n, dtype="i1"), np.full(n, np.nan)


class DonchianBreakout(Strategy):
    """
    Cassure de canal. On entre quand la cloture depasse le plus haut des N
    bougies precedentes, filtre par ADX pour eviter les faux signaux en range.
    Stop calibre en multiple d'ATR, donc il s'adapte tout seul a la volatilite
    du moment. C'est la reponse au probleme souleve par Lucaas : un parametre
    fixe en pips devient obsolete des que la volatilite change.
    """

    name = "donchian"
    param_grid = {
        "channel": [20, 30, 55],
        "atr_period": [14],
        "sl_atr": [1.5, 2.0, 3.0],
        "adx_min": [0, 20, 25],
    }

    def signals(self, rates):
        channel = int(self.params.get("channel", 20))
        atr_period = int(self.params.get("atr_period", 14))
        sl_atr = float(self.params.get("sl_atr", 2.0))
        adx_min = float(self.params.get("adx_min", 20))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < channel + atr_period + 5:
            return sig, sl

        close = rates["close"]
        upper, lower = ind.donchian(rates["high"], rates["low"], channel)
        rng = ind.atr(rates["high"], rates["low"], close, atr_period)
        trend = ind.adx(rates["high"], rates["low"], close, 14) if adx_min > 0 else None

        for i in range(n):
            if np.isnan(upper[i]) or np.isnan(rng[i]) or rng[i] <= 0:
                continue
            if trend is not None and (np.isnan(trend[i]) or trend[i] < adx_min):
                continue
            if close[i] > upper[i]:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
            elif close[i] < lower[i]:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
        return sig, sl


class EmaTrend(Strategy):
    """
    Croisement de moyennes avec filtre de tendance long terme.
    On ne prend un long que si le prix est au-dessus de l'EMA lente longue,
    ce qui elimine la moitie des signaux et la quasi-totalite des pires.
    """

    name = "ema_trend"
    param_grid = {
        "fast": [8, 12, 21],
        "slow": [26, 50, 100],
        "filter_len": [0, 200],
        "sl_atr": [1.5, 2.0, 3.0],
    }

    def signals(self, rates):
        fast = int(self.params.get("fast", 12))
        slow = int(self.params.get("slow", 50))
        filter_len = int(self.params.get("filter_len", 200))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if fast >= slow or n < max(slow, filter_len) + 20:
            return sig, sl

        close = rates["close"]
        ef = ind.ema(close, fast)
        es = ind.ema(close, slow)
        trend = ind.ema(close, filter_len) if filter_len > 0 else None
        rng = ind.atr(rates["high"], rates["low"], close, 14)

        for i in range(1, n):
            if np.isnan(ef[i]) or np.isnan(es[i]) or np.isnan(ef[i - 1]) or np.isnan(es[i - 1]):
                continue
            if np.isnan(rng[i]) or rng[i] <= 0:
                continue

            crossed_up = ef[i - 1] <= es[i - 1] and ef[i] > es[i]
            crossed_down = ef[i - 1] >= es[i - 1] and ef[i] < es[i]

            if crossed_up:
                if trend is not None and (np.isnan(trend[i]) or close[i] < trend[i]):
                    continue
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
            elif crossed_down:
                if trend is not None and (np.isnan(trend[i]) or close[i] > trend[i]):
                    continue
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
        return sig, sl


class KeltnerVelocity(Strategy):
    """
    Sortie de canal de Keltner accompagnee d'une acceleration du prix.
    C'est la famille qui ressort gagnante du backtest de DaviddTech
    (profit factor 1.4, drawdown 10.9%). Reproduite ici avec ses propres
    parametres, pas copiee : on la valide sur nos donnees et nos frais.
    """

    name = "keltner_velocity"
    param_grid = {
        "ema_len": [20, 34],
        "atr_period": [10, 14],
        "mult": [1.0, 1.5, 2.0],
        "velocity": [3, 5],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        ema_len = int(self.params.get("ema_len", 20))
        atr_period = int(self.params.get("atr_period", 10))
        mult = float(self.params.get("mult", 1.5))
        velocity = int(self.params.get("velocity", 5))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < ema_len + atr_period + velocity + 10:
            return sig, sl

        close = rates["close"]
        upper, mid, lower = ind.keltner(rates["high"], rates["low"], close, ema_len, atr_period, mult)
        rng = ind.atr(rates["high"], rates["low"], close, atr_period)
        vel = ind.slope(close, velocity)

        for i in range(n):
            if np.isnan(upper[i]) or np.isnan(vel[i]) or np.isnan(rng[i]) or rng[i] <= 0:
                continue
            if close[i] > upper[i] and vel[i] > 0:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
            elif close[i] < lower[i] and vel[i] < 0:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
        return sig, sl


class SqueezeBreakout(Strategy):
    """
    Compression de volatilite puis expansion. On attend que les bandes de
    Bollinger rentrent dans le canal de Keltner (le squeeze), puis on prend
    la direction de la sortie.
    """

    name = "squeeze"
    param_grid = {
        "length": [20],
        "bb_mult": [2.0],
        "kc_mult": [1.5],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        length = int(self.params.get("length", 20))
        bb_mult = float(self.params.get("bb_mult", 2.0))
        kc_mult = float(self.params.get("kc_mult", 1.5))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < length + 30:
            return sig, sl

        close = rates["close"]
        bb_up, bb_mid, bb_low = ind.bollinger(close, length, bb_mult)
        kc_up, kc_mid, kc_low = ind.keltner(rates["high"], rates["low"], close, length, length, kc_mult)
        rng = ind.atr(rates["high"], rates["low"], close, 14)

        squeezed = (bb_up < kc_up) & (bb_low > kc_low)

        for i in range(1, n):
            if np.isnan(bb_up[i]) or np.isnan(kc_up[i]) or np.isnan(rng[i]) or rng[i] <= 0:
                continue
            # On veut la bougie qui SORT du squeeze, pas celles qui y sont.
            if not (squeezed[i - 1] and not squeezed[i]):
                continue
            if close[i] > bb_mid[i]:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
            else:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
        return sig, sl


class RsiReversion(Strategy):
    """
    Temoin negatif. Achete la survente, vend la surachat.
    Presente pour verifier que le backtester sait rejeter une strategie,
    et pour mesurer concretement combien les frais coutent sur du contre-tendance.
    """

    name = "rsi_reversion"
    param_grid = {
        "period": [14],
        "low": [25, 30],
        "high": [70, 75],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        period = int(self.params.get("period", 14))
        low_th = float(self.params.get("low", 30))
        high_th = float(self.params.get("high", 70))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < period + 20:
            return sig, sl

        close = rates["close"]
        r = ind.rsi(close, period)
        rng = ind.atr(rates["high"], rates["low"], close, 14)

        for i in range(1, n):
            if np.isnan(r[i]) or np.isnan(r[i - 1]) or np.isnan(rng[i]) or rng[i] <= 0:
                continue
            if r[i - 1] < low_th <= r[i]:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
            elif r[i - 1] > high_th >= r[i]:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
        return sig, sl


# ==================== FAMILLES AJOUTEES LE 2026-09-10 ====================
# Objectif : trouver des edges STRUCTURELLEMENT differents de ema_trend, donc
# susceptibles d'etre decorreles. Un portefeuille de lignes decorrelees ameliore
# le rapport rendement sur drawdown, ce qu'aucun reglage d'une strategie seule
# ne permet. Mesure sur les 3 premieres lignes : ratio multiplie par 3.3.
#
# Rappel : les timestamps MT5 sont en heure SERVEUR (UTC+3 chez ce broker).
# Session asiatique ~02h-10h serveur, Londres ~10h-18h, New York ~15h-23h.


def _day_index(times):
    """Index de journee, borne a 00h serveur. Rapide, pas de datetime dans la boucle."""
    return (np.asarray(times, dtype="i8") // 86400).astype("i8")


def _hour_of_day(times):
    return ((np.asarray(times, dtype="i8") // 3600) % 24).astype("i4")


class AsianRangeBreakout(Strategy):
    """
    Cassure du range asiatique.

    Le marche asiatique est calme et construit un range. L'ouverture de Londres
    le casse souvent dans une direction qui tient sur la journee. C'est un motif
    de microstructure horaire, sans aucun lien avec un croisement de moyennes,
    donc un bon candidat a la decorrelation.

    Le range est fige a la fin de la session asiatique. On ne prend QUE la
    premiere cassure de la journee, pour eviter de payer dix aller-retours dans
    un marche qui oscille autour de la borne.
    """

    name = "asian_breakout"
    param_grid = {
        "asia_start": [2],
        "asia_end": [10],
        "trade_until": [18],
        "buffer_atr": [0.0, 0.25],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        asia_start = int(self.params.get("asia_start", 2))
        asia_end = int(self.params.get("asia_end", 10))
        trade_until = int(self.params.get("trade_until", 18))
        buffer_atr = float(self.params.get("buffer_atr", 0.0))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < 100:
            return sig, sl

        high, low, close = rates["high"], rates["low"], rates["close"]
        rng = ind.atr(high, low, close, 14)
        days = _day_index(rates["time"])
        hours = _hour_of_day(rates["time"])

        cur_day = -1
        asia_hi = asia_lo = None
        fired = False

        for i in range(n):
            if days[i] != cur_day:
                cur_day = days[i]
                asia_hi = asia_lo = None
                fired = False

            h = hours[i]

            # Construction du range pendant la session asiatique.
            if asia_start <= h < asia_end:
                asia_hi = high[i] if asia_hi is None else max(asia_hi, high[i])
                asia_lo = low[i] if asia_lo is None else min(asia_lo, low[i])
                continue

            if fired or asia_hi is None or asia_lo is None:
                continue
            if not (asia_end <= h < trade_until):
                continue
            if np.isnan(rng[i]) or rng[i] <= 0:
                continue

            buf = buffer_atr * rng[i]
            if close[i] > asia_hi + buf:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
                fired = True
            elif close[i] < asia_lo - buf:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
                fired = True

        return sig, sl


class VolatilityBreakout(Strategy):
    """
    Expansion de volatilite depuis l'ouverture de la journee.

    On entre quand le prix s'ecarte de plus de k fois l'ATR de son ouverture du
    jour. C'est le principe des systemes de Larry Williams : une journee qui
    demarre fort a tendance a continuer. Une seule prise par jour.
    """

    name = "vol_breakout"
    param_grid = {
        "k_atr": [0.5, 0.8, 1.2],
        "sl_atr": [1.5, 2.5],
        "max_hour": [20],
    }

    def signals(self, rates):
        k_atr = float(self.params.get("k_atr", 0.8))
        sl_atr = float(self.params.get("sl_atr", 2.0))
        max_hour = int(self.params.get("max_hour", 20))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < 100:
            return sig, sl

        opens, high, low, close = rates["open"], rates["high"], rates["low"], rates["close"]
        rng = ind.atr(high, low, close, 14)
        days = _day_index(rates["time"])
        hours = _hour_of_day(rates["time"])

        cur_day = -1
        day_open = None
        fired = False

        for i in range(n):
            if days[i] != cur_day:
                cur_day = days[i]
                day_open = opens[i]
                fired = False

            if fired or day_open is None:
                continue
            if hours[i] >= max_hour:
                continue
            if np.isnan(rng[i]) or rng[i] <= 0:
                continue

            move = close[i] - day_open
            if move > k_atr * rng[i]:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
                fired = True
            elif move < -k_atr * rng[i]:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
                fired = True

        return sig, sl


class MomentumRoc(Strategy):
    """
    Momentum multi-bougies normalise par la volatilite.

    On mesure la variation sur lookback bougies, divisee par l'ATR pour la
    rendre comparable entre marches, et on entre quand elle depasse un seuil.
    Contrairement a un croisement de moyennes, il n'y a pas d'evenement de
    croisement : le signal est un ETAT, ce qui produit un profil de trades
    different et donc une chance de decorrelation.
    """

    name = "momentum_roc"
    param_grid = {
        "lookback": [24, 48, 96],
        "threshold": [1.5, 2.5, 3.5],
        "cooldown": [24],
        "sl_atr": [2.0, 3.0],
    }

    def signals(self, rates):
        lookback = int(self.params.get("lookback", 48))
        threshold = float(self.params.get("threshold", 2.5))
        cooldown = int(self.params.get("cooldown", 24))
        sl_atr = float(self.params.get("sl_atr", 2.5))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < lookback + 60:
            return sig, sl

        high, low, close = rates["high"], rates["low"], rates["close"]
        rng = ind.atr(high, low, close, 14)

        last_fire = -10 ** 9
        for i in range(lookback, n):
            if np.isnan(rng[i]) or rng[i] <= 0:
                continue
            if i - last_fire < cooldown:
                continue
            move = (close[i] - close[i - lookback]) / rng[i]
            if move > threshold:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
                last_fire = i
            elif move < -threshold:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
                last_fire = i
        return sig, sl


class BollingerReversion(Strategy):
    """
    Retour a la moyenne, mais UNIQUEMENT quand le marche est en range.

    Le filtre ADX est ici inverse par rapport aux strategies de tendance : on
    n'agit que si l'ADX est BAS, donc quand il n'y a pas de tendance. C'est ce
    qui rend cette famille naturellement anticorrelee avec ema_trend, qui elle
    ne gagne que dans les tendances.

    Sortie geree par le take profit a 2R et non par un retour a la moyenne.
    C'est volontaire : le probleme classique de la mean reversion est justement
    qu'on ne sait pas ou placer la sortie.
    """

    name = "bb_reversion"
    param_grid = {
        "period": [20, 30],
        "mult": [2.0, 2.5],
        "adx_max": [20, 25],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        period = int(self.params.get("period", 20))
        mult = float(self.params.get("mult", 2.0))
        adx_max = float(self.params.get("adx_max", 20))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < period + 60:
            return sig, sl

        high, low, close = rates["high"], rates["low"], rates["close"]
        up, mid, lo = ind.bollinger(close, period, mult)
        rng = ind.atr(high, low, close, 14)
        trend = ind.adx(high, low, close, 14)

        for i in range(1, n):
            if np.isnan(up[i]) or np.isnan(rng[i]) or rng[i] <= 0:
                continue
            if np.isnan(trend[i]) or trend[i] > adx_max:
                continue
            # On veut la bougie qui REVIENT dans la bande, pas celle qui en sort.
            if close[i - 1] < lo[i - 1] and close[i] > lo[i]:
                sig[i] = 1
                sl[i] = sl_atr * rng[i]
            elif close[i - 1] > up[i - 1] and close[i] < up[i]:
                sig[i] = -1
                sl[i] = sl_atr * rng[i]
        return sig, sl


class TrendPullback(Strategy):
    """
    Achat de repli dans une tendance etablie.

    Tendance definie par une EMA longue. On attend que le prix redescende sur la
    bande basse de Keltner puis reparte. C'est du contre-tendance a court terme
    dans du pro-tendance a long terme, donc un timing d'entree tres different de
    ema_trend qui, lui, entre sur la cassure.
    """

    name = "trend_pullback"
    param_grid = {
        "trend_len": [100, 200],
        "kc_len": [20],
        "kc_mult": [1.0, 1.5],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        trend_len = int(self.params.get("trend_len", 200))
        kc_len = int(self.params.get("kc_len", 20))
        kc_mult = float(self.params.get("kc_mult", 1.5))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < trend_len + 60:
            return sig, sl

        high, low, close = rates["high"], rates["low"], rates["close"]
        trend = ind.ema(close, trend_len)
        up, mid, lo = ind.keltner(high, low, close, kc_len, kc_len, kc_mult)
        rng = ind.atr(high, low, close, 14)

        for i in range(1, n):
            if np.isnan(trend[i]) or np.isnan(lo[i]) or np.isnan(rng[i]) or rng[i] <= 0:
                continue
            if close[i] > trend[i]:
                # Tendance haussiere : on achete le repli qui repart.
                if low[i - 1] <= lo[i - 1] and close[i] > close[i - 1]:
                    sig[i] = 1
                    sl[i] = sl_atr * rng[i]
            else:
                if high[i - 1] >= up[i - 1] and close[i] < close[i - 1]:
                    sig[i] = -1
                    sl[i] = sl_atr * rng[i]
        return sig, sl


class FvgRetest(Strategy):
    """
    Retest d'imbalance, aussi appelee Fair Value Gap.

    Definition mecanique, sans interpretation : il y a une imbalance haussiere
    quand le plus bas de la bougie i est STRICTEMENT au-dessus du plus haut de
    la bougie i-2. Les trois bougies laissent alors un trou de prix que le
    marche n'a pas cote. L'idee, tres repandue dans les approches SMC et ICT,
    est que ce trou a tendance a etre comble.

    On n'entre pas a la formation : on attend que le prix REVIENNE dans la zone,
    puis on prend la direction de l'imbalance. Une zone est consommee des la
    premiere entree dedans, et elle est invalidee si le prix la traverse
    entierement, ce qui prouve que l'inefficience a ete comblee sans reaction.

    Une zone confirmee a l'index i n'est utilisable qu'a partir de i+1, donc
    aucun regard vers l'avant.
    """

    name = "fvg_retest"
    param_grid = {
        "min_gap_atr": [0.15, 0.35],
        "max_age": [24, 72],
        "trend_len": [0, 200],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        min_gap_atr = float(self.params.get("min_gap_atr", 0.25))
        max_age = int(self.params.get("max_age", 48))
        trend_len = int(self.params.get("trend_len", 0))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < max(trend_len, 60) + 10:
            return sig, sl

        high, low, close = rates["high"], rates["low"], rates["close"]
        rng = ind.atr(high, low, close, 14)
        trend = ind.ema(close, trend_len) if trend_len > 0 else None

        zones = []   # (direction, bas, haut, index de creation)

        for i in range(2, n):
            if np.isnan(rng[i]) or rng[i] <= 0:
                continue

            # Purge des zones trop vieilles ou deja traversees.
            zones = [z for z in zones
                     if i - z[3] <= max_age
                     and not (close[i] < z[1] if z[0] > 0 else close[i] > z[2])]

            # Retest d'une zone existante, la plus recente d'abord.
            for k in range(len(zones) - 1, -1, -1):
                direction, lo_z, hi_z, born = zones[k]
                if born >= i:
                    continue
                touched = low[i] <= hi_z and high[i] >= lo_z
                if not touched:
                    continue
                zones.pop(k)
                if trend is not None:
                    if np.isnan(trend[i]):
                        continue
                    if direction > 0 and close[i] < trend[i]:
                        continue
                    if direction < 0 and close[i] > trend[i]:
                        continue
                sig[i] = direction
                sl[i] = sl_atr * rng[i]
                break

            # Detection d'une nouvelle imbalance sur les bougies i-2, i-1, i.
            gap_up = low[i] - high[i - 2]
            gap_dn = low[i - 2] - high[i]
            if gap_up > min_gap_atr * rng[i]:
                zones.append((1, high[i - 2], low[i], i))
            elif gap_dn > min_gap_atr * rng[i]:
                zones.append((-1, high[i], low[i - 2], i))

        return sig, sl


class BalancedPriceRange(Strategy):
    """
    BPR, Balanced Price Range : deux imbalances de sens opposes qui se
    chevauchent. La zone commune a ete traversee dans les deux sens sans etre
    cotee proprement, ce qui en fait un niveau reputé plus reactif qu'une simple
    imbalance.

    C'est le concept mis en avant dans la video de Kasper. Il y est valide a
    l'oeil sur quelques trades choisis. Ici il passe la meme grille que les
    autres : 100 trades minimum, frais actifs, out of sample.
    """

    name = "bpr"
    param_grid = {
        "lookback": [12, 30],
        "max_age": [48],
        "trend_len": [0, 200],
        "sl_atr": [1.5, 2.5],
    }

    def signals(self, rates):
        lookback = int(self.params.get("lookback", 20))
        max_age = int(self.params.get("max_age", 48))
        trend_len = int(self.params.get("trend_len", 0))
        sl_atr = float(self.params.get("sl_atr", 2.0))

        n = len(rates)
        sig, sl = self._empty(n)
        if n < max(trend_len, 80) + 10:
            return sig, sl

        high, low, close = rates["high"], rates["low"], rates["close"]
        rng = ind.atr(high, low, close, 14)
        trend = ind.ema(close, trend_len) if trend_len > 0 else None

        bulls = []   # (bas, haut, index)
        bears = []
        bprs = []    # (direction, bas, haut, index)

        for i in range(2, n):
            if np.isnan(rng[i]) or rng[i] <= 0:
                continue

            bprs = [z for z in bprs if i - z[3] <= max_age]

            for k in range(len(bprs) - 1, -1, -1):
                direction, lo_z, hi_z, born = bprs[k]
                if born >= i:
                    continue
                if low[i] <= hi_z and high[i] >= lo_z:
                    bprs.pop(k)
                    if trend is not None:
                        if np.isnan(trend[i]):
                            continue
                        if direction > 0 and close[i] < trend[i]:
                            continue
                        if direction < 0 and close[i] > trend[i]:
                            continue
                    sig[i] = direction
                    sl[i] = sl_atr * rng[i]
                    break

            gap_up = low[i] - high[i - 2]
            gap_dn = low[i - 2] - high[i]
            new_bull = new_bear = None
            if gap_up > 0.1 * rng[i]:
                new_bull = (high[i - 2], low[i], i)
                bulls.append(new_bull)
            elif gap_dn > 0.1 * rng[i]:
                new_bear = (high[i], low[i - 2], i)
                bears.append(new_bear)

            bulls = [z for z in bulls if i - z[2] <= lookback]
            bears = [z for z in bears if i - z[2] <= lookback]

            # Un BPR nait quand la nouvelle imbalance recouvre une imbalance
            # opposee encore recente. La zone retenue est leur intersection.
            if new_bull:
                for b in bears:
                    lo_z = max(new_bull[0], b[0])
                    hi_z = min(new_bull[1], b[1])
                    if hi_z > lo_z:
                        bprs.append((1, lo_z, hi_z, i))
                        break
            elif new_bear:
                for b in bulls:
                    lo_z = max(new_bear[0], b[0])
                    hi_z = min(new_bear[1], b[1])
                    if hi_z > lo_z:
                        bprs.append((-1, lo_z, hi_z, i))
                        break

        return sig, sl


REGISTRY = {
    DonchianBreakout.name: DonchianBreakout,
    EmaTrend.name: EmaTrend,
    KeltnerVelocity.name: KeltnerVelocity,
    SqueezeBreakout.name: SqueezeBreakout,
    RsiReversion.name: RsiReversion,
    AsianRangeBreakout.name: AsianRangeBreakout,
    VolatilityBreakout.name: VolatilityBreakout,
    MomentumRoc.name: MomentumRoc,
    BollingerReversion.name: BollingerReversion,
    TrendPullback.name: TrendPullback,
    FvgRetest.name: FvgRetest,
    BalancedPriceRange.name: BalancedPriceRange,
}


def build(name: str, **params) -> Strategy:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Strategie inconnue: {name}. Disponibles: {', '.join(REGISTRY)}")
    return cls(**params)


def expand_grid(name: str):
    """Toutes les combinaisons de parametres d'une strategie, pour le sweep."""
    from itertools import product

    cls = REGISTRY[name]
    grid = cls.param_grid
    if not grid:
        return [cls()]
    keys = sorted(grid)
    combos = []
    for values in product(*(grid[k] for k in keys)):
        combos.append(cls(**dict(zip(keys, values))))
    return combos
