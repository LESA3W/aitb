#!/usr/bin/env python3
"""
gains.py - Combien le bot a gagne. C'est tout.

Lancement :  python gains.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import MetaTrader5 as mt5


def euro(x):
    return f"{x:+,.2f} $".replace(",", " ")


def main():
    if not mt5.initialize():
        print("\n  MT5 n'est pas lance. Ouvre MetaTrader 5 et relance.\n")
        return

    acc = mt5.account_info()
    positions = list(mt5.positions_get() or [])
    now = datetime.now()

    # Trades reellement fermes : c'est le seul argent vraiment gagne.
    deals = mt5.history_deals_get(now - timedelta(days=365), now) or []
    fermes = [d for d in deals if d.entry == 1]

    def somme(depuis):
        return sum(d.profit + d.commission + d.swap
                   for d in fermes if datetime.fromtimestamp(d.time) >= depuis)

    debut_jour = now.replace(hour=0, minute=0, second=0, microsecond=0)
    debut_semaine = debut_jour - timedelta(days=debut_jour.weekday())
    debut_mois = debut_jour.replace(day=1)

    flottant = acc.equity - acc.balance

    print()
    print("  " + "=" * 52)
    print(f"   A.I.T.B   compte {acc.login}   {now:%d/%m/%Y %H:%M}")
    print("  " + "=" * 52)
    print()
    print(f"   Argent sur le compte      {acc.balance:>12,.2f} $".replace(",", " "))
    print(f"   Trades en cours          {euro(flottant):>14s}")
    print(f"   Total si on ferme tout    {acc.equity:>12,.2f} $".replace(",", " "))
    print()
    print("  " + "-" * 52)
    print("   GAINS REELLEMENT ENCAISSES")
    print("  " + "-" * 52)
    print(f"   Aujourd'hui              {euro(somme(debut_jour)):>14s}")
    print(f"   Cette semaine            {euro(somme(debut_semaine)):>14s}")
    print(f"   Ce mois                  {euro(somme(debut_mois)):>14s}")
    print(f"   Depuis le debut          {euro(acc.balance - 1000.0):>14s}")
    print()
    print(f"   Objectif hebdo : 250 $   ->  {'ATTEINT' if somme(debut_semaine) >= 250 else 'pas encore'}")
    print()

    if positions:
        print("  " + "-" * 52)
        print(f"   {len(positions)} TRADE(S) EN COURS")
        print("  " + "-" * 52)
        for p in positions:
            sens = "achat" if p.type == 0 else "vente"
            info = mt5.symbol_info(p.symbol)
            pip = 10 * info.point if info else 0.0001
            avance = (p.price_current - p.price_open) / pip * (1 if p.type == 0 else -1)
            print(f"   {p.symbol:8s} {sens:5s} {p.volume:<5} "
                  f"{euro(p.profit):>10s}   ({avance:+.0f} pips)")
        print()

    if fermes:
        derniers = sorted(fermes, key=lambda d: d.time, reverse=True)[:5]
        print("  " + "-" * 52)
        print("   5 DERNIERS TRADES FERMES")
        print("  " + "-" * 52)
        for d in derniers:
            net = d.profit + d.commission + d.swap
            marque = "gagne " if net > 0 else ("perdu " if net < 0 else "neutre")
            print(f"   {datetime.fromtimestamp(d.time):%d/%m %H:%M}  {d.symbol:8s} "
                  f"{marque} {euro(net):>10s}")
        print()
    else:
        print("   Aucun trade ferme pour l'instant.")
        print("   Le bot prend environ 1 trade tous les 1 a 2 jours.")
        print()

    mt5.shutdown()


if __name__ == "__main__":
    main()
