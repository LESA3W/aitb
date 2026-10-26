from pathlib import Path

CONFIG = {
    # ==================== PATHS ====================
    "ROOT": str(Path(__file__).resolve().parent),
    "SCREENSHOTS_FOLDER": "SCREENSHOTS/EURUSD",
    "REPORTS_FOLDER": "logs",
    "TRADE_LOG_HTML": "trade_logs.html",
    "HTML_AUTO_REFRESH_SECONDS": 15,
    "PERF_CSV": "perf.csv",
    "LOG_CONTEXT_JSONL": "context.jsonl",
    "LOG_SPREAD_DEBUG": False,
    "MACROS_FOLDER": "macros_fixe", #macros_portable OU #macoros_fixe
    "WINDOW_KEYWORD": "00000000",
    "TESSERACT_PATH": r"D:\AITB\Tesseract-OCR\tesseract.exe",
    "TRADE_COMMENT": "🐐 A.I.T.B",

    # ==================== TRADING SCHEDULE ====================
    "TIME_ZONE": "Europe/Paris",
    "INTERVAL_MINUTES": 5,               # 0.9 entre chaque capture
    "IMAGES_PER_ANALYSIS": 3,
    "MAX_CYCLES": 40,                       # tourne plusieurs heures d'affilée

    # ==================== SYMBOL & MARKET ====================
    "SYMBOL": "EURUSD",
    # Garde-fou de securite contre les explosions de spread sur news, pas un
    # filtre de strategie. Le backtest facture deja le spread de chaque bougie.
    # Plafond ABSOLU, conserve en repli quand le stop n'est pas connu.
    # Il avait ete calibre pour EURUSD (spread ~1 pip) et bloquait en silence
    # toute autre classe d'actif : NZDUSD tourne a 4,5 pips, SWI20 a 17.
    "MAX_SPREAD_PIPS": 2.0,
    # Plafond RELATIF, utilise des que le stop est connu : un spread ne se
    # juge pas dans l'absolu mais par rapport a ce qu'on risque. 4,5 pips sur
    # un stop de 90 pips coutent 5% du risque, c'est acceptable ; les memes
    # 4,5 pips sur un stop de 10 pips en coutent 45%, c'est non.
    # 8% correspond a ce que le backtest facture deja en spread moyen.
    "MAX_SPREAD_PCT_OF_SL": 8.0,
    "ALLOWED_SESSIONS": ["EU", "US"],
    # Filtres desactives car NON modelises dans le backtest. Les activer ferait
    # diverger le live du backtest, donc rendrait les chiffres inexploitables.
    # Pour les remettre, il faut d'abord les coder dans aitb/strategies.py.
    "SESSION_FILTER": False,
    "MIN_ATR_POINTS": 0,

    # ==================== RISK MANAGEMENT (1000 USD ACCOUNT) ====================
    # 1.0 % : c'est la valeur a laquelle la strategie live a ete backtestee et
    # validee out of sample (max drawdown 6 %). A 2 % le drawdown double a 12 %.
    # Ne pas remonter sans refaire tourner research/sweep.py.
    # 0,5% et pas 1% : a 1% le drawdown du book passe a 12,4%, au-dessus de la
    # limite de 10% d'une prop firm. A 0,5% il tombe a 6,2%. Le rendement suit
    # proportionnellement, mais un compte qui saute ne rapporte rien.
    #
    # 2026-09-16 : passe de 0,56 a 0,75 apres mesure DIRECTE sur le book REEL de
    # 14 lignes, 1167 trades, 19,8 ans, solde initial 80 000 EUR.
    #     0,56%   rendement  6,99%/an   drawdown max  6,65%
    #     0,75%   rendement  9,39%/an   drawdown max  8,81%
    #     1,00%   rendement 12,58%/an   drawdown max 11,60%   <- TUE LE COMPTE
    # Le 1% confirme le commentaire ci-dessus et reste interdit.
    #
    # PIEGE RENCONTRE EN PRODUISANT CES CHIFFRES, a ne pas repeter : la premiere
    # mesure utilisait research/portfolio.py tel quel, dont collect() code en dur
    # initial_balance=1000. A 1 000 EUR le lot tombe sous le minimum broker et des
    # trades sont sautes EN SILENCE. Symptome : le nombre de trades changeait avec
    # le risque (281 / 440 / 549) alors qu il doit etre constant. Avec le vrai
    # solde il vaut 1167 a tous les niveaux.
    #
    # CE QUE CE REGLAGE COUTE : la marge avant les 10% fatals tombe de 3,35 a
    # 1,19 point. Un drawdown futur a peine pire que le pire historique suffit
    # alors a faire sauter le compte. Choix assume, pas un oubli.
    "RISK_PCT": 0.75,
    # Filet contre une FAUTE DE FRAPPE dans RISK_PCT, pas un reglage de
    # strategie. A 100.0 il ne servait a rien : un "10.0" tape au lieu de "1.0"
    # serait passe sans bruit et aurait fait sauter le compte en 5 trades.
    # 3.0 laisse la place a tous les reglages prevus (0.4 a 1.0) et arrete net
    # tout ce qui ressemble a un doigt qui a glisse.
    "MAX_RISK_PCT": 3.0,
    # "LOTS" a ete supprime : taille de lot fixe, heritee de l'ancienne archi,
    # plus referencee nulle part. La taille vient de calc_lot, jamais d'une
    # constante, sinon elle serait fausse des qu'on change de compte.
    "TP_PIPS": 5,

    "MULTI_TP": [
        {"pips": 1, "close_pct": 60},        # TP1: Ferme 60% à +5 pips
        {"pips": 2, "close_pct": 30},       # TP2: Ferme 30% à +10 pips
        {"pips": 3, "close_pct": 10},       # TP3: Ferme 10% à +15 pips
    ],

    "SL_PIPS": 3,                            # ratio 2R
    "MIN_LOT": 0.01,
    "MAX_LOT": 0.1,                        # legacy, garde pour compatibilite
    # Plafond de volume PROPORTIONNEL au capital. 0.10 pour 1000 USD reproduit
    # exactement l'ancien comportement sur la demo, et suit automatiquement
    # quand on branche un compte prop firm plus gros.
    "MAX_LOT_PER_1K": 0.10,
    "MAX_LOT_ABS": 20.0,
    "LOT_STEP": 0.01,

    
    # 💡 Comment modifier :
    # - Change les valeurs "pips" pour ajuster les distances
    # - Change les valeurs "close_pct" pour ajuster les pourcentages
    # - Ajoute/supprime des niveaux selon tes besoins
    # - Le total des close_pct peut être ≤ 100% (si <100%, le reste continue en trailing)
    
    # 📊 Exemples de configurations alternatives :
    
    # Scalping ultra-agressif (sorties très rapides)
    # "MULTI_TP": [
    #     {"pips": 3, "close_pct": 70},
    #     {"pips": 6, "close_pct": 20},
    #     {"pips": 10, "close_pct": 10},
    # ],
    
    # Swing trading (laisser courir les profits)
    # "MULTI_TP": [
    #     {"pips": 15, "close_pct": 40},
    #     {"pips": 25, "close_pct": 35},
    #     {"pips": 40, "close_pct": 25},
    # ],
    
    # 4 niveaux (pour plus de granularité)
    # "MULTI_TP": [
    #     {"pips": 5, "close_pct": 40},
    #     {"pips": 10, "close_pct": 30},
    #     {"pips": 15, "close_pct": 20},
    #     {"pips": 20, "close_pct": 10},
    # ],
    
    # Runner avec trailing (laisse 20% en position infinie)
    # "MULTI_TP": [
    #     {"pips": 5, "close_pct": 50},
    #     {"pips": 10, "close_pct": 30},
    #     {"pips": 15, "close_pct": 0},    # Total = 80%, reste 20% en trailing
    # ],

    # ==================== PROTECTIONS ====================
    
    # ⚠️ MULTI-TP DESACTIVE, ne pas remettre a True sans reflechir.
    # Les niveaux Multi-TP fermeraient 60 % de la position a +1 pip alors que la
    # strategie live a un stop median de 38 pips et un objectif a 2R (~77 pips).
    # Actif, il detruirait l'edge en coupant tous les gagnants a la naissance.
    "USE_MULTI_TP": False,
    "USE_TP": True,
    "USE_SL": True,
    # Une seule position PAR SYMBOLE : c'est ce que modelise le backtest.
    # MAX_OPEN_POSITIONS vaut le nombre de lignes du portefeuille, sinon la
    # deuxieme et la troisieme ligne seraient bloquees par la protection.
    "ONE_POSITION_RULE": True,
    "MAX_OPEN_POSITIONS": 15,   # une par ligne du book
    "COOLDOWN_MINUTES": 0,                 # réengage rapidement 1.5
    "DAILY_DRAWDOWN_LIMIT_PCT": 6,           # tolère -6 % max par jour
    "MAX_DAILY_LOSS_USD": 60, # 60

    # ==================== BREAK-EVEN & TRAILING ====================
    # BE_MODE "R" : le break-even se declenche a BE_TRIGGER_R fois le risque
    # initial du trade, pas a un nombre de pips fixe. Indispensable ici car le
    # stop est calcule par ATR et varie de 20 a 86 pips selon la volatilite.
    # Mesure : sans break-even, le profit factor tombe de 1.40 a 1.24 et le
    # drawdown passe de 6 % a 11 %. Ce reglage n'est pas cosmetique.
    "BE_MODE": "R",
    "BE_TRIGGER_R": 1.0,
    # Trailing desactive : le backtest valide ne le modelise pas.
    "USE_TRAILING": False,
    "BE_TRIGGER_PIPS": 6,                    # legacy, ignore quand BE_MODE = "R"
    "TRAIL_TRIGGER_PIPS": 10,
    "TRAIL_DISTANCE_PIPS": 8,
    "MONITOR_INTERVAL_SECONDS": 2,
    "HARD_REVERSE_ON_SIGNAL": False, #True = Pour éviter d'avoir BUY et SELL en même temps sur le même symbole (hedging risqué).

    # ==================== MT5 EXECUTION ====================
    "MAGIC": 20251104,
    "DEVIATION_POINTS": 20,                  # plus strict (RAW spread bas)
    "FILLING_MODE": "IOC",  

    # ==================== MONITORING & LOGS ====================
    "ENABLE_EQUITY_SNAPSHOT": True,
    "LOG_EVERY_DECISION": True,
    "ALERT_ON_DRAWDOWN": True,
    "ALERT_THRESHOLD_PCT": 4.0,

    # ==================== MODE HEADLESS (aitb/live.py) ====================
    # Remplace la chaine screenshot + MiniMouseMacro + ChatGPT + OCR.
    # Le bot lit les bougies via l'API MT5 : aucune souris, aucune fenetre au
    # premier plan, transportable tel quel sur un VPS Windows.
    #
    # LIVE_STRATEGY reste a None tant qu'aucune strategie n'a passe la grille
    # d'acceptation. Le runner refuse de demarrer sans, c'est volontaire.
    # Pour trouver un candidat :
    #   python research/sweep.py --download EURUSD M15 EURUSD H1 XAUUSD M15
    #   python research/sweep.py --all --detail 3
    # Strategie retenue le 2026-09-10 apres 1390 backtests sur 10 datasets.
    # C'est la SEULE qui passe la grille d'acceptation.
    #   EURUSD H1, 2023-09 -> 2026-09, 18608 bougies
    #   profit factor 1.40 | rendement +22.2 % | max drawdown 6.0 %
    #   145 trades | win rate 29 % | esperance +1.53 $/trade | buy and hold +8.4 %
    # Verifications de robustesse passees :
    #   out of sample sur les 40 % finaux : PF 1.31, +7.4 %, bat le buy and hold
    #   couts doubles : PF reste a 1.33
    #   spread force a 1.5 pip : PF reste a 1.33
    # Limite connue : le voisinage de parametres n'est positif que dans 14 cas
    # sur 48. L'edge existe sur H1 et H4, il disparait en M30 et M15.
    # PORTEFEUILLE, 5 lignes selectionnees le 2026-09-10.
    # Issu de 7258 backtests : 10 familles de strategies x 19 instruments x H1/H4.
    # Seules 5 combinaisons passent la grille, sur 2 familles differentes
    # (suivi de tendance et expansion de volatilite depuis l'ouverture du jour).
    # Selection gloutonne par research/build_book.py, correlation max 0.4.
    #
    #   43.9 % annualise | drawdown 19.6 % | ratio 2.24 | 272 trades/an
    #   contre 8.0 % / 7.0 % / ratio 1.14 pour la meilleure ligne seule
    #
    # Le gain vient de la DECORRELATION, pas du reglage. Monter le risque ne
    # change pas le ratio (mesure : ~1.1 constant de 0.5 % a 8 % de risque),
    # ajouter des lignes independantes est le seul levier qui l'ameliore.
    # Verifier avec: python research/portfolio.py et research/build_book.py
    # ---------------------------------------------------------------- PROP FIRM
    # Garde-fou des regles de la firme, applique sur l'EQUITY (flottant inclus),
    # ce que les simulations de research/ ne modelisent pas.
    # Reglage cale sur FTMO 2-step : perte totale 10% STATIQUE, jour 5%.
    # Passer ENABLED a True UNIQUEMENT sur le compte de la firme.
    "PROPFIRM": {
        "ENABLED": False,
        "FIRM": "FTMO",
        "PHASE": "challenge",          # challenge | verification | funded
        "INITIAL_BALANCE": 50000.0,
        "MAX_TOTAL_LOSS_PCT": 10.0,
        "MAX_DAILY_LOSS_PCT": 5.0,
        "SOFT_STOP_RATIO": 0.50,       # 50% de la marge consommee -> plus d'entrees
        "HARD_STOP_RATIO": 0.80,       # 80% -> on ferme tout
        "DAILY_RESET_UTC_OFFSET": 2,   # minuit heure de Prague (CEST)
    },
    # Le risque optimal pour PASSER le challenge n'est pas celui qui permet de
    # GARDER le compte finance. Mesure sur 812 trades :
    #   challenge a 1.0%  -> 86.7% de reussite, 56 jours
    #   finance   a 1.0%  -> 26.9% de survie a 12 mois
    #   finance   a 0.5%  -> 91.2% de survie, 134 EUR/semaine sur 50k
    # D'ou deux reglages distincts, a basculer au moment du financement.
    "PROPFIRM_RISK_CHALLENGE": 1.0,
    "PROPFIRM_RISK_FUNDED": 0.5,

    # Book valide le 2026-09-12. Remplace 5 lignes de suivi de tendance
    # mesurees a PF 0,73 et 122,9% de drawdown en portefeuille.
    #
    # Ces deux lignes sont les seules, sur 1296 combinaisons testees, a passer
    # la chaine complete : walk-forward 6 fenetres, confirmation sur 3
    # decoupages (4/6/8), profit factor > 1 A LA VENTE COMME A L'ACHAT,
    # robustesse au retrait des 5 meilleurs trades, drawdown sous 8%, et
    # reoptimisation glissante a 1/2/3/6/12 mois.
    #
    # Les deux gagnent PLUS a la vente qu'a l'achat (1,39 vs 1,35 et 1,93 vs
    # 1,15). Une exposition directionnelle ne peut pas produire ca : c'est le
    # signal le plus fort dont on dispose que l'edge n'est pas du beta.
    #
    # Contrepartie assumee : environ 2 trades par mois au total. Le book est
    # lent. Ne pas ajouter de lignes pour "faire du volume" sans leur faire
    # passer la meme chaine, c'est exactement ce qui a produit le book
    # precedent.
    # ==================== BOOK VALIDE LE 2026-09-12 ====================
    # Issu de 3888 combinaisons : 54 marches x 6 timeframes x 12 familles.
    # Chaine de validation, dans l'ordre :
    #   1. walk-forward 6 fenetres, parametres choisis sur le passe uniquement
    #   2. 9 criteres simultanes, dont profit factor > 1 A LA VENTE COMME A
    #      L'ACHAT (tue le beta) et drawdown out-of-sample <= 8%
    #   3. confirmation sur 3 decoupages temporels (4, 6 et 8 fenetres)
    #   4. selection par correlation mensuelle, seuil 0,40
    #
    # 3888 -> 35 -> 8 lignes. Correlation moyenne 0,06, maximum 0,29 : aucune
    # n'a ete ecartee, elles sont reellement independantes.
    #
    # Book : PF 1,31 sur 266 trades out-of-sample, 9,2 trades/mois.
    # A 0,5% de risque : drawdown 6,2%, sous la limite FTMO de 10%.
    # A 1,0% : drawdown 12,4%, ce qui ferait sauter un compte prop firm.
    # NE PAS monter le risque au-dessus de 0,5% sur un compte a limite de perte.
    #
    # Decouverte qui a debloque le projet : H6, H8 et H12 n'avaient jamais ete
    # testes. Sur 35 rescapees, ZERO en H1 ou H2. L'edge vit sur les horizons
    # longs, ou les frais pesent moins lourd par trade.
    #
    # Le book precedent (5 lignes de suivi de tendance H1/H4) faisait PF 0,73
    # et 122,9% de drawdown en portefeuille : ses lignes perdaient ensemble.
    # ==================== BOOK VALIDE LE 2026-09-12 (v2) ====================
    # 5184 combinaisons : 54 marches x 8 timeframes x 12 familles de strategies.
    # 5184 -> 55 (9 criteres) -> 18 (3 decoupages) -> 14 (correlation < 0,40).
    #
    # Correlation mensuelle moyenne 0,01, maximum 0,30 : les lignes sont
    # independantes, leurs drawdowns se compensent.
    # PF 1,48 sur 266 trades, mesures sur la periode COMMUNE aux 14 lignes.
    #
    # DECOUVERTE CENTRALE : l'edge vit sur les horizons LONGS.
    # Repartition des 55 rescapees par timeframe :
    #     D1 17 | H12 13 | H8 11 | H6 7 | H4 4 | H3 3 | H2 0 | H1 0
    # Aucune en H1 ni H2, sur 4577 combinaisons evaluees. Cause probable :
    # les frais. Moins de trades, moins de spread paye, l'edge survit.
    #
    # Effet mesure de l'ajout de lignes decorrelees :
    #      4 lignes -> gain  3,4%/an, drawdown 3,0%
    #     14 lignes -> gain 11,5%/an, drawdown 5,8%
    # Gain x3,4, drawdown x1,9. C'est le seul levier connu qui ameliore le
    # ratio sans monter le risque.
    #
    # NE PAS ajouter de ligne sans lui faire passer la chaine complete :
    # walk-forward, 9 criteres dont PF > 1 A LA VENTE COMME A L'ACHAT,
    # confirmation sur 3 decoupages, puis controle de correlation.
    # ==================== BOOK VALIDE LE 2026-09-13 (v3) ====================
    # 8112 combinaisons : 142 marches x 6 timeframes x 12 familles.
    # 8112 -> 73 (9 criteres) -> 20 (3 decoupages) -> 15 (correlation < 0,40).
    #
    # H1 et H2 ne sont plus balayes : ZERO rescapee sur 4577 combinaisons
    # evaluees, contre 48 sur 55 pour D1/H12/H8/H6. L'edge vit sur les
    # horizons longs, ou les frais pesent moins lourd par trade.
    #
    # Effet mesure de l'ajout de lignes decorrelees (periode commune, 2,4 ans) :
    #      4 lignes -> gain  3,9%/an, drawdown 3,0%
    #     15 lignes -> gain 15,3%/an, drawdown 7,1%
    # Gain x3,9 pour un drawdown x2,4, avec des rendements decroissants.
    #
    # NE PAS ajouter de ligne sans la chaine complete : walk-forward,
    # 9 criteres dont PF > 1 A LA VENTE COMME A L'ACHAT, confirmation sur
    # 3 decoupages, controle de correlation.
    #
    # ===== L'EDGE EST AGREGE, PAS LIGNE PAR LIGNE (mesure 2026-09-14) =====
    # Le resultat le plus important du book, et le moins intuitif.
    #
    # Test de significativite de chaque ligne sur la periode commune, t de
    # Student sur l'esperance en R par trade :
    #
    #     XAUAUD H8     65 trades  t 2.60   SIGNIFICATIVE
    #     XAGUSD H6     96 trades  t 2.02   SIGNIFICATIVE
    #     NZDUSD H4    102 trades  t 1.91
    #     USTECH100M   133 trades  t 1.88
    #     ... puis 11 lignes de t 1.70 a t 0.12
    #     USDCAD D1     33 trades  t 0.12
    #
    #     2 lignes SEULEMENT sur 15 sont individuellement significatives.
    #
    #     PORTEFEUILLE ENTIER : 881 trades, esperance +0,2032 R par trade,
    #     erreur-type 0,0429, t = 4,74.
    #
    # C'est ce que la decorrelation achete : quinze edges individuellement
    # indemontrables s'agregent en quelque chose de mesurable. La premisse de
    # conception du book se tient.
    #
    # MAIS CE 4,74 EST UNE BORNE SUPERIEURE, PAS UNE PREUVE.
    # Ces 15 lignes ont ete SELECTIONNEES parmi 5246 combinaisons evaluees, sur
    # leur performance hors echantillon. Un t calcule sur les survivantes d'une
    # selection herite du biais de cette selection. Sous hypothese nulle, le
    # maximum attendu sur N tests INDEPENDANTS vaut environ sqrt(2 ln N).
    #
    # Mais les 5246 ne sont PAS independants. Structure reelle mesuree dans
    # research/wf_partiel.jsonl : 123 series de prix, 515 paires symbole x
    # timeframe, 12 familles. Le meme instrument teste sur 6 unites de temps
    # partage la MEME serie de prix, et 12 familles partagent la meme paire.
    #
    #     N suppose                     plancher   marge sur 4,74
    #     123  series de prix seules      3,10        +1,64
    #     515  paires symbole x TF        3,53        +1,21
    #     5246 tous les tests             4,14        +0,60
    #
    # NE PAS RETENIR UN SEUL DE CES CHIFFRES. Aucun n'est le bon N : des
    # familles differentes sur les memes donnees produisent de vrais signaux
    # differents (donc N > 515), mais elles partagent la serie de prix (donc
    # N < 5246). La marge est quelque part entre +0,6 et +1,6.
    #
    # Et une objection plus profonde, qui coupe dans l'autre sens : sqrt(2 ln N)
    # est la loi du MAXIMUM sur N tests. Ce t n'est pas un maximum, c'est
    # l'agregation de 15 lignes choisies. La vraie question nulle serait « si
    # les 5246 etaient du bruit pur, que donnerait l'agregation des 15 qui ont
    # l'air les meilleures ». Elle n'a pas de forme analytique simple et
    # personne ici ne sait la resoudre. Les deux biais vont en sens opposes et
    # se compensent partiellement, d'ampleur inconnue.
    #
    # CONCLUSION : le signal survit a toutes les hypotheses testees, mais sa
    # marge n'est pas resolvable par le calcul. SEUL LE FORWARD TRANCHERA.
    #
    # LE SEUL TEST NON BIAISE est constitue des donnees que la selection n'a
    # jamais vues, c'est-a-dire le forward test en cours. A 0,2032 R
    # d'esperance pour 1,273 R d'ecart-type, il faut 157 trades pour t = 2,0
    # et 353 pour t = 3,0, soit 1,1 an et 2,5 ans a 2,7 trades par semaine.
    # Le Free Trial de 14 jours en produira ~5 : il ne tranchera RIEN
    # statistiquement, mais c'est la seule serie non contaminee du projet.
    #
    # CONSEQUENCE PRATIQUE : ne JAMAIS retirer une ligne parce qu'elle est
    # individuellement insignifiante. C'est vrai de 13 lignes sur 15. Le
    # critere de retrait doit porter sur le PORTEFEUILLE, jamais sur la ligne.
    #
    # ET SURTOUT, ne pas utiliser le leave-one-out comme signal de qualite.
    # J'ai retire XAUUSD H8 sur un gain de ratio de +0,34, puis mesure le
    # leave-one-out de TOUTES les lignes :
    #     AUDCHF H6 (saine, pf_in 1,35) -> +0,42, MIEUX que XAUUSD
    #     bruit de fond des 14 autres : moyenne -0,062, ecart-type 0,189
    # Le delta est domine par les correlations et le calendrier des drawdowns,
    # pas par la qualite de la ligne. Retrait ANNULE, le book reste a 15.
    #
    # Faute commise et a ne pas repeter : j'ai teste plusieurs retraits et agi
    # sur le meilleur, donc selectionne sur les memes donnees que j'ai
    # mesurees. C'est exactement l'erreur documentee dans wf_search.retenu()
    # a propos de pf_out. Savoir nommer un biais ne protege pas de le commettre.
    "LIVE_BOOK": [
        {"symbol": "XAUAUD", "timeframe": "H8", "strategy": "bpr",
         "params": {"lookback": 12, "max_age": 48, "sl_atr": 2.5, "trend_len": 0}},
        {"symbol": "EURCAD", "timeframe": "D1", "strategy": "bb_reversion",
         "params": {"adx_max": 25, "mult": 2.0, "period": 20, "sl_atr": 2.5}},
        {"symbol": "EURGBP", "timeframe": "H3", "strategy": "bb_reversion",
         "params": {"adx_max": 20, "mult": 2.5, "period": 30, "sl_atr": 2.5}},
        {"symbol": "USDSGD", "timeframe": "D1", "strategy": "ema_trend",
         "params": {"fast": 12, "filter_len": 200, "sl_atr": 2.0, "slow": 100}},
        {"symbol": "GBPJPY", "timeframe": "D1", "strategy": "bpr",
         "params": {"lookback": 12, "max_age": 48, "sl_atr": 1.5, "trend_len": 200}},
        {"symbol": "EURNZD", "timeframe": "D1", "strategy": "squeeze",
         "params": {"bb_mult": 2.0, "kc_mult": 1.5, "length": 20, "sl_atr": 2.5}},
        {"symbol": "XAUUSD", "timeframe": "H8", "strategy": "squeeze",
         "params": {"bb_mult": 2.0, "kc_mult": 1.5, "length": 20, "sl_atr": 1.5}},
        {"symbol": "NZDUSD", "timeframe": "H4", "strategy": "bb_reversion",
         "params": {"adx_max": 25, "mult": 2.5, "period": 20, "sl_atr": 2.5}},
        {"symbol": "XAGUSD", "timeframe": "H6", "strategy": "ema_trend",
         "params": {"fast": 8, "filter_len": 0, "sl_atr": 2.0, "slow": 100}},
        {"symbol": "USDCAD", "timeframe": "D1", "strategy": "vol_breakout",
         "params": {"k_atr": 1.2, "max_hour": 20, "sl_atr": 2.5}},
        {"symbol": "GBPUSD", "timeframe": "D1", "strategy": "rsi_reversion",
         "params": {"high": 75, "low": 30, "period": 14, "sl_atr": 2.5}},
        {"symbol": "EURCHF", "timeframe": "H6", "strategy": "squeeze",
         "params": {"bb_mult": 2.0, "kc_mult": 1.5, "length": 20, "sl_atr": 2.5}},
        {"symbol": "AUDCHF", "timeframe": "H6", "strategy": "bb_reversion",
         "params": {"adx_max": 20, "mult": 2.0, "period": 20, "sl_atr": 2.5}},
        {"symbol": "USDCHF", "timeframe": "D1", "strategy": "vol_breakout",
         "params": {"k_atr": 1.2, "max_hour": 20, "sl_atr": 2.5}},
        {"symbol": "USTECH100M", "timeframe": "H6", "strategy": "momentum_roc",
         "params": {"cooldown": 24, "lookback": 96, "sl_atr": 3.0, "threshold": 1.5}},
    ],




    # Repli mono-ligne si LIVE_BOOK est vide.
    "LIVE_STRATEGY": "ema_trend",
    "LIVE_STRATEGY_PARAMS": {"fast": 21, "slow": 100, "filter_len": 200, "sl_atr": 3.0},
    "LIVE_TIMEFRAME": "H1",             # l'edge meurt en dessous, les frais le mangent
    "LIVE_BARS": 600,                   # il faut au moins filter_len 200 + chauffe
    "LIVE_TP_R": 2.0,                   # take profit en multiples du risque
    "LIVE_MIN_SL_PIPS": 3.0,            # garde-fou bas sur le stop calcule par ATR
    # Plafond haut, volontairement large. Le stop reel va de 20 pips sur
    # EURUSD H1 a ~165 pips sur USDJPY H4. Ecreter le stop casserait la
    # correspondance avec le backtest : c'est le sizing qui borne le risque,
    # pas ce plafond, qui ne sert que de garde-fou contre une valeur aberrante.
    "LIVE_MAX_SL_PIPS": 400.0,
    "LIVE_POLL_SECONDS": 5,             # frequence de detection d'une nouvelle bougie
}


# ---------------------------------------------------------------------------
# Surcharges par .env
# ---------------------------------------------------------------------------
# Basculer sur un compte prop firm ne doit PAS demander d'editer ce fichier.
# Un lundi matin, sous la pression du chrono d'un Free Trial de 14 jours, une
# edition de code est le meilleur moyen de casser quelque chose. Tout ce qui
# change d'un compte a l'autre se pilote donc depuis un simple .env, non
# versionne, que l'on depose et qui suffit.
#
# Cles reconnues, toutes optionnelles :
#   FTMO_LOGIN / FTMO_PASSWORD / FTMO_SERVER   lues par aitb/trade_manager.py
#   PROPFIRM_ENABLED=true                      active le garde-fou
#   PROPFIRM_BALANCE=80000                     solde initial du compte
#   PROPFIRM_MAX_TOTAL_LOSS=10                 perte totale max en %
#   PROPFIRM_MAX_DAILY_LOSS=5                  perte journaliere max en %
#   RISK_PCT=0.5                               risque par trade en %

def _appliquer_env(cfg):
    """Surcharge CONFIG depuis .env. Sans .env, rien ne change."""
    import os
    fichier = Path(__file__).resolve().parent.parent / ".env"
    valeurs = {}
    if fichier.exists():
        for ligne in fichier.read_text(encoding="utf-8", errors="replace").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                k, v = ligne.split("=", 1)
                valeurs[k.strip()] = v.strip().strip('"').strip("'")
    valeurs.update({k: v for k, v in os.environ.items()
                    if k.startswith(("PROPFIRM_", "RISK_PCT"))})

    def _nombre(cle, defaut=None):
        try:
            return float(valeurs[cle])
        except (KeyError, ValueError):
            return defaut

    if "PROPFIRM_ENABLED" in valeurs:
        cfg["PROPFIRM"]["ENABLED"] = valeurs["PROPFIRM_ENABLED"].lower() in (
            "1", "true", "oui", "yes")
    for cle_env, cle_cfg in (("PROPFIRM_BALANCE", "INITIAL_BALANCE"),
                             ("PROPFIRM_MAX_TOTAL_LOSS", "MAX_TOTAL_LOSS_PCT"),
                             ("PROPFIRM_MAX_DAILY_LOSS", "MAX_DAILY_LOSS_PCT")):
        v = _nombre(cle_env)
        if v is not None:
            cfg["PROPFIRM"][cle_cfg] = v

    v = _nombre("RISK_PCT")
    if v is not None:
        # Le plafond MAX_RISK_PCT reste la derniere barriere contre une faute
        # de frappe : un "5" tape au lieu de "0.5" doit etre refuse ici.
        if v <= cfg.get("MAX_RISK_PCT", 3.0):
            cfg["RISK_PCT"] = v
        else:
            print(f"  .env ignore : RISK_PCT={v} depasse MAX_RISK_PCT="
                  f"{cfg.get('MAX_RISK_PCT')}")
    return cfg


CONFIG = _appliquer_env(CONFIG)

####IDEAS####
# Faire 2 dossier de macros un dossier macros_fixe et un dossier macros_portable (comme ça je peux tester, faire fonctionner et travailler sur aitb à n'importe quel endroit)
# ajouter plusieurs TP
# une page de dashboard par jour geenre page dashboard du jour 11/20/2025 etc...
# tp/sl touchés = color
#FAIRE UN TRUC PLUS TARD QUI NE LANCE PAS LE PROGRAMME SI C'EST UN WEEK END ET QUI DEMANDE CONFIRMATION DE LANCER QUAND MEME LE PROGRAMME 
# time_emoji TPC = XXmXXs (Time Per Cycle)