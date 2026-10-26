# aitb/propfirm_guard.py
"""
Garde-fou des regles de prop firm.

Pourquoi ce fichier existe
--------------------------
Les simulations de research/propfirm.py ne modelisent que des trades CLOTURES :
elles font evoluer un solde trade par trade. Une prop firm ne fonctionne pas
comme ca. FTMO, comme la plupart des firmes, evalue la perte journaliere et la
perte totale sur l'EQUITY, donc positions ouvertes incluses, en continu.

Consequence concrete : un portefeuille qui ne clot aucun trade perdant peut
quand meme faire sauter le compte, parce que le flottant a touche la limite a
un instant donne. Les 91% de survie mesures en simulation sont donc un
plafond optimiste, pas une prevision.

Ce module ferme cet ecart. Il lit l'equity reelle a chaque cycle et agit AVANT
la limite, jamais apres.

Regles modelisees (calees sur FTMO 2-step, drawdown STATIQUE)
------------------------------------------------------------
  perte totale max      10% du solde INITIAL, jamais reajustee a la hausse
  perte journaliere max  5% du solde de debut de journee
  reset journalier       00:00 CE(S)T (heure de Prague, pas l'heure serveur MT5)

Deux paliers, parce qu'un seul ne suffit pas :

  PALIER SOUPLE   on arrete d'OUVRIR. Les positions en cours gardent leur stop
                  chez le broker et vivent leur vie. C'est le palier normal :
                  il ne coute rien et il suffit dans la grande majorite des cas.

  PALIER DUR      on FERME tout. Cout reel : on devie du backtest et on
                  cristallise une perte qui serait peut-etre revenue. On
                  l'accepte parce que l'alternative est de perdre le compte,
                  donc le flux de revenu entier, pour recuperer quelques
                  dizaines d'euros de flottant.

Le palier dur ne se declenche jamais en premier : il faut avoir franchi le
souple, donc avoir deja cesse d'ouvrir, et que le marche continue contre nous.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ETAT_PATH = PROJECT_ROOT / "data" / "propfirm_state.json"

# Statuts retournes par evaluer()
OK = "OK"
STOP_ENTREES = "STOP_ENTREES"
FERMER_TOUT = "FERMER_TOUT"


class PropFirmGuard:
    """
    Surveille l'equity contre les regles de la firme.

    Sans etat persistant on ne peut pas connaitre le solde de debut de journee
    apres un redemarrage du bot, et on comparerait alors la perte du jour a une
    mauvaise reference. L'etat est donc ecrit sur disque a chaque changement de
    journee.
    """

    def __init__(self, config: dict):
        pf = config.get("PROPFIRM", {}) or {}
        self.actif = bool(pf.get("ENABLED", False))
        self.solde_initial = float(pf.get("INITIAL_BALANCE", 0.0) or 0.0)
        self.perte_totale_pct = float(pf.get("MAX_TOTAL_LOSS_PCT", 10.0))
        self.perte_jour_pct = float(pf.get("MAX_DAILY_LOSS_PCT", 5.0))
        # Part de la limite qu'on s'autorise a consommer avant d'agir.
        self.seuil_souple = float(pf.get("SOFT_STOP_RATIO", 0.50))
        self.seuil_dur = float(pf.get("HARD_STOP_RATIO", 0.80))
        # FTMO remet le compteur journalier a zero a minuit heure de Prague.
        self.decalage_reset = int(pf.get("DAILY_RESET_UTC_OFFSET", 2))
        self.etat = self._lire()

    # ---------------------------------------------------------------- etat

    def _lire(self) -> dict:
        try:
            return json.loads(ETAT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _ecrire(self):
        try:
            ETAT_PATH.parent.mkdir(parents=True, exist_ok=True)
            ETAT_PATH.write_text(json.dumps(self.etat, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _jour_firme(self, maintenant: float = None) -> str:
        """
        Journee au sens de la firme, pas au sens du serveur MT5 ni du PC.
        Se tromper d'un fuseau decale la fenetre de reset de plusieurs heures
        et rend la limite journaliere fausse pile au mauvais moment.
        """
        ts = maintenant if maintenant is not None else time.time()
        fuseau = timezone(timedelta(hours=self.decalage_reset))
        return datetime.fromtimestamp(ts, fuseau).strftime("%Y-%m-%d")

    def _solde_debut_journee(self, equity: float, balance: float,
                            sans_ecrire: bool = False) -> float:
        """
        Reference de debut de journee, figee sur disque.

        sans_ecrire : calcule la meme valeur sans RIEN ecrire. Indispensable
        pour tout appelant qui n'est pas le processus qui trade. Un controle
        pre-vol qui teste l'escalade avec des equity simulees, ou un tableau de
        bord qui se rafraichit toutes les 3 secondes, poserait sinon lui-meme la
        reference que la firme utilise pour juger la perte du jour, avec la
        valeur du moment ou il s'est execute. Seul le bot a le droit de la
        fixer. Le piege est vicieux : le fichier reste lisible et plausible,
        personne ne verrait que la valeur vient d'un test.

        NOM CHOISI EXPRES : pas "dry_run". live.py a deja un dry_run qui veut
        dire "n'envoie aucun ordre". Deux sens differents sous un meme nom, a
        quelques lignes d'ecart, dans un code ou une confusion coute un compte.
        """
        jour = self._jour_firme()
        if self.etat.get("jour") != jour:
            if sans_ecrire:
                return float(balance)
            # Nouvelle journee : la reference devient le solde courant.
            #
            # Limite connue : si le bot etait arrete au passage de minuit et
            # redemarre en cours de journee apres une perte, la reference prise
            # ici est le solde DEJA reduit, donc plus permissive que celle de la
            # firme. C'est la raison pour laquelle le watchdog doit tourner en
            # continu.
            self.etat = {"jour": jour,
                         "solde_debut": float(balance),
                         "equity_debut": float(equity),
                         "fige_le": int(time.time())}
            self._ecrire()
        return float(self.etat.get("solde_debut", balance))

    # ------------------------------------------------------------ decision

    def evaluer(self, equity: float, balance: float,
                sans_ecrire: bool = False) -> dict:
        """
        Retourne un verdict a partir de l'equity courante.

        equity      = solde + flottant, c'est ce que la firme regarde
        balance     = solde des trades clotures seulement
        sans_ecrire = ne fige PAS la reference de journee sur disque. A utiliser
                      par tout appelant qui n'est pas le bot : pre-vol, tableau
                      de bord, tests. Le calcul est identique au bit pres, seule
                      l'ecriture saute.
        """
        if not self.actif or self.solde_initial <= 0:
            return {"statut": OK, "actif": False, "raison": "garde-fou desactive"}

        debut_jour = self._solde_debut_journee(equity, balance, sans_ecrire)

        plancher_total = self.solde_initial * (1 - self.perte_totale_pct / 100.0)
        plancher_jour = debut_jour * (1 - self.perte_jour_pct / 100.0)

        marge_totale = self.solde_initial - plancher_total     # en devise
        marge_jour = debut_jour - plancher_jour

        perte_totale = max(self.solde_initial - equity, 0.0)
        perte_jour = max(debut_jour - equity, 0.0)

        conso_totale = perte_totale / marge_totale if marge_totale > 0 else 0.0
        conso_jour = perte_jour / marge_jour if marge_jour > 0 else 0.0
        conso = max(conso_totale, conso_jour)

        if conso >= self.seuil_dur:
            statut = FERMER_TOUT
        elif conso >= self.seuil_souple:
            statut = STOP_ENTREES
        else:
            statut = OK

        quoi = "journaliere" if conso_jour >= conso_totale else "totale"
        return {
            "statut": statut,
            "actif": True,
            "consomme_pct": conso * 100,
            "consomme_jour_pct": conso_jour * 100,
            "consomme_total_pct": conso_totale * 100,
            "limite_qui_mord": quoi,
            "equity": equity,
            "plancher_total": plancher_total,
            "plancher_jour": plancher_jour,
            "marge_restante": min(equity - plancher_total, equity - plancher_jour),
            "raison": "%s de la limite %s consommee" % (
                format(conso * 100, ".1f") + "%", quoi),
        }

    def peut_ouvrir(self, equity: float, balance: float) -> tuple:
        v = self.evaluer(equity, balance)
        return v["statut"] == OK, v
