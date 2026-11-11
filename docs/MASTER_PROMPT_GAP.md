# Écart entre le master prompt « ASTRA Autonomous Trading Intelligence » et l'état réel

Date : 2026-11-11. Version en production au moment de l'écriture : `459fb45` (les instruments étendus sont prêts, non déployés). Chaque ligne dit ce qui a été vérifié, et comment.

## OPÉRATIONNEL ET VÉRIFIÉ (preuve en production)

| Exigence du prompt | Preuve |
|---|---|
| Connexion réelle à un compte DEMO (critère 1) | pont MT5 sous Wine sur le serveur, 24 h sur 24, compte MetaQuotes-Demo connecté (lecture du compte en base) |
| Données de marché réelles (critère 2) | cotations du courtier toutes les 5 s pour 7 paires, bougies M5 à D1 du courtier, BCE quotidienne, Massive D1 et H1 (85 756 bougies H1) |
| Spécifications des instruments (critère 3) | catalogue lu en production : 12 170 instruments ; les 7 paires ont un contrat de 100 000, volume minimum et pas de 0,01 |
| Ordres DEMO réels et confirmation du courtier (critères 7, 8) | ordres exécutés sur le compte démo (ouvertures et fermetures des 28 octobre au 10 novembre) ; transactions relues dans l'historique du courtier ; rapprochement des positions avec le courtier |
| Risque indépendant (critère 6) | portail `mt5_risk` : refus par défaut, spread, fraîcheur, marge, perte du jour, drawdown, nombre de positions, exposition nette au dollar, taille de contrat vérifiée, gel sur alerte de rapprochement ou ordre inconnu |
| Idempotence et reprise (critères 10, 17) | intention écrite avant l'envoi, résultat inconnu rapproché du compte, état du pont persistant, demandes du site jamais exécutées deux fois |
| Arrêt d'urgence | essai réel du 29 octobre ; fichier STOP ; état revérifié après chaque redémarrage |
| Mémoire persistante des décisions (critère 11) | signaux, ordres, transactions et évènements des comptes simulés en base ; journal d'audit d'apprentissage |
| Backtesting reproductible (critère 13) | empreinte SHA-256 des données, déterminisme testé, 1311 tests (Linux) |
| Dashboard alimenté par des données réelles (critère 16) | positions, comptabilité à 4 contrôles, performance, fraîcheur, décisions par paire |
| Fonctionnement continu (critère 15) | services systemd avec redémarrage automatique, sauvegarde quotidienne, restauration testée sur copie |

## DÉVELOPPÉ MAIS NON VALIDÉ EN CONDITIONS RÉELLES

- **Gouvernance automatisée** (candidats figés, promotion en simulation, comptes d'observation, revue prospective préenregistrée, suspension et retour arrière) : en production depuis le 10 novembre, le premier passage d'apprentissage avec journal n'a pas été observé.
- **Flux H1 autorisable** pour le pont DEMO : aucune paire n'est autorisée, aucun champion H1 n'est confirmé.
- **Force relative des devises, objectif de profit, filtre de régime** : testés sur données de test ; aucune preuve de performance.
- **Instruments étendus** (10 croisées, or, argent) : registre, conversion en USD, tailles d'unités et coûts testés en simulation ; coûts en hypothèse.

## EN COURS

- Déploiement des instruments étendus, import de leur historique Massive, apprentissage H1 sur l'univers étendu (simulation).

## BLOQUÉ PAR UN ACCÈS EXTERNE

- **cTrader** : le connecteur existe et n'est testé que contre un serveur simulé. Il faut un identifiant d'application, un secret et un jeton d'un compte démo cTrader du titulaire. Sans eux, aucun test réel n'est possible.
- **Binance, OKX, Bybit** : compte démo et clé API sans droit de retrait ni de transfert, déposée par le titulaire. Le compte MetaQuotes actuel ne liste aucune crypto.
- **Notifications** (courriel, messagerie) : un canal et son secret fournis par le titulaire.
- **LIVE** : activation réelle soumise à l'autorisation du titulaire, capital maximal et limites définis par lui.

## NON DÉVELOPPÉ

| Exigence | Remarque |
|---|---|
| Agents spécialisés comme composants séparés (Partie V) | **Décision d'architecture** : les rôles existent comme modules déterministes d'un monolithe modulaire (données, stratégies, risque, exécution, apprentissage, supervision). Aucun modèle de langage ne décide d'un ordre (Partie VI). Une supervision et des journaux par rôle restent à écrire. |
| Calendrier économique, actualités, profondeur de marché, transactions tick | pas de source configurée |
| Horizons M1, M30, W1 ; indicateurs Bollinger, ADX, stochastique, Fibonacci, VWAP, profil de volume, supports et résistances | seuls SMA, RSI, MACD, ATR et les familles de stratégies actuelles existent |
| Ordres limit, stop, trailing stop ; ordres partiels côté courtier au-delà du traitement existant | le pont envoie des ordres au marché avec stop |
| Drawdown hebdomadaire et mensuel, risque de gap, limite par volatilité ou VaR | seuls la perte du jour et le drawdown global sont limités |
| Analyse post-trade automatique et mémoire des situations similaires | les transactions sont stockées, pas analysées automatiquement |
| Rapports quotidiens, hebdomadaires, mensuels ; notifications | à écrire (rapport en fichier et sur le site possible sans secret) |
| Centres du tableau de bord (apprentissage, risque, supervision des agents) | une partie est affichée (décisions, niveaux, observation) ; pas de centres dédiés |
| Séparation DEMO / LIVE prouvée par un test dédié | le mode LIVE est verrouillé côté serveur ; aucun connecteur LIVE n'a été exercé |

## Règles retenues pour la suite

1. L'autorisation DEMO du propriétaire est permanente et persistante après redémarrage ; elle ne couvre ni le LIVE, ni la modification des limites de risque, ni un secret.
2. Une stratégie ne nourrit le pont DEMO que si elle a satisfait ses critères préenregistrés, dont la confirmation prospective. Le temps nécessaire n'est pas raccourci.
3. L'absence de transaction est un résultat normal quand les conditions ne sont pas réunies.
