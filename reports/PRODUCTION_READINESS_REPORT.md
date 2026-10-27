# ASTRA, dossier de préparation à la production

**Destinataires : l'utilisateur et l'architecte principal (ChatGPT).** Date de première version : 27/10/2026. Base auditée : `feat/multi-venue` à `956644d`. Le dossier est tenu à jour à chaque gate.

**Statut global : NON prêt pour la production.**
- Aucune fonction n'a été validée contre un fournisseur réel, un testnet ou un compte de démonstration.
- Aucune donnée de marché réelle n'a été ingérée.
- Le mode SIMULATION est le seul mode exécutable. DÉMO est désactivé et RÉEL verrouillé.

Ce dossier distingue trois niveaux de preuve : **hors ligne** (simulateur, faux serveurs, données synthétiques), **testnet ou démo**, et **compte réel**. Aujourd'hui, toutes les preuves sont hors ligne.

## 1. Méthode

Quatre agents d'audit spécialisés, en lecture seule, chacun sur un périmètre distinct :
1. données de marché et courtiers ;
2. intelligence décisionnelle et apprentissage ;
3. risque, exécution, persistance et service ;
4. tableau de bord, sécurité, exploitation et conformité.

Chaque constat cite un fichier et une ligne. Un connecteur écrit n'est pas compté comme une intégration. Une donnée synthétique n'est jamais comptée comme réelle.

## 2. Matrice de préparation

Légende du niveau de preuve : **0** aucune, **H** hors ligne, **T** testnet ou démo, **R** compte réel.

| Domaine | Ce qui existe | Preuve | Ce qui manque pour la production | Blocage |
|---|---|---|---|---|
| Données : import historique | Import CSV contrôlé (`data.py` : schéma, OHLC, bid/ask, doublons, ordre, trous si intervalle donné) | H | La provenance est **déclarée** par l'appelant. L'import ne gère qu'un instrument par fichier. Il manque la détection des valeurs aberrantes, des bougies plates, et le calendrier des week-ends et changements d'heure. | Ingénierie ; externe : source historique sous licence |
| Données : temps réel | `BarBuilder` à partir des cotations ; flux `SYNTHETIC` étiqueté ; cotations cTrader (adaptateur) | H | Bougies sans volume. Fermeture d'une bougie seulement à la cotation suivante. Une cotation hors ordre est fusionnée sans alerte. Aucune détection de trou ou de silence. Historique en mémoire seulement, aucun rattrapage, un seul horizon. | Ingénierie ; externe : flux autorisé |
| Données : fraîcheur | La passerelle relit la cotation à l'envoi, refuse au-delà de 120 s ou si elle est datée dans le futur, et refuse si elle manque | H | Des cotations peuvent paraître fraîches à tort : retour à l'horloge locale côté cTrader, horodatage par côté non suivi, cache non vidé à la reconnexion, âge du bookTicker Binance nul par construction. Il n'y a ni `received_at` ni latence mesurée. | Ingénierie |
| Données : provenance | Étiquettes CSV ; `Quote.source` en texte libre | H | Il n'y a pas de type LIVE / DELAYED / HISTORICAL / SIMULATED / SYNTHETIC porté par les cotations et les bougies, ni de stockage de barres versionné. | Ingénierie |
| Courtier : identité et admissibilité | Registre scellé (15 plateformes), dossiers de recherche | Recherche documentaire | Aucune plateforme n'est `REGION_ELIGIBILITY_VERIFIED`. Toutes sont `ELIGIBILITY_UNKNOWN` ou `BLOCKED`. | **Externe** : confirmations écrites, avis juridique (Guinée, BCRG) |
| Connecteur cTrader | Adaptateur JSON/WebSocket vers le serveur démo uniquement, refus de `isLive`, ordres MARKET, `ctrader-check` en lecture seule | H (faux serveur) | Jamais connecté. Hypothèses de protocole non vérifiées. Ni historique ni ticks. Aucune recherche par identifiant client. Aucun magasin de jetons. **Pas de porte de registre**, à la différence de Binance. | Externe : compte démo et identifiants OAuth ; ingénierie : porte de registre |
| Connecteur Binance Spot | Données publiques et lecture de compte, GET uniquement | H (faux serveur local) | Hôtes injoignables depuis la machine. Un trou dans les bougies fait échouer toute la requête. Pas de pagination, pas de flux temps réel, non branché au service. | Externe : réseau, admissibilité, clé en lecture seule |
| Stratégies | Familles interprétables (momentum, cassure, référence SMA), décision avec action, horizon, invalidation, coût, incertitude et motif | H synthétique | Paramètres illustratifs. Retour à la moyenne exclu du walk-forward. Régimes non transmis au moment de la décision : l'incertitude vaut toujours `None`. | Ingénierie ; externe : données réelles |
| ML | Régression logistique en walk-forward purgé, calibration | H synthétique | Aucune règle « probabilité vers ordre » après coûts. Les candidats ML ne peuvent pas passer le holdout. | Ingénierie |
| Validation et surapprentissage | Walk-forward scellé, holdout protégé, PBO, DSR, registre des essais chaîné, détecteur de réutilisation, ancre locale | H synthétique (résultat `LOW_INFORMATION`) | Seuils non calibrés. L'ancre n'est pas de niveau production (il faudrait du WORM). Le scellé reste réécrivable par le propriétaire du fichier. | Externe : données réelles, ancre WORM, revue statistique |
| Registre des modèles et promotion | Critères pré-enregistrés, re-dérivation du holdout, refus des données synthétiques, promotion par rôle humain | H | Rôles déclarés, non authentifiés. `require_shadow` hors du plancher. La table `model_versions` est modifiable sans déclencheur. **Défaut probable** : deux rollbacks successifs réactivent la version écartée (lu dans le code, à confirmer par un test). | Ingénierie ; externe : authentification |
| Lien champion et exécution | Aucun | 0 | Le service exécute une stratégie choisie en ligne de commande, pas la version promue. | Ingénierie |
| Shadow, dérive, réentraînement, rollback automatique | Statut `SHADOW` seulement | 0 | Tout le cycle d'apprentissage continu est à construire. | Ingénierie ; externe : flux démo pour le shadow |
| Enregistrement des décisions | Ordres avec stratégie, version, sens, stop, états et motifs | H | Non conservés : source et âge des données, motif du signal, horizon, incertitude, régime, contexte de risque, version du modèle, résultat. Les abstentions sont jetées. | Ingénierie |
| Isolation du LLM | Schéma strict, sortie consultative seulement, aucun appelant en exécution | H | Isolation par absence de câblage, sans test de frontière. | Ingénierie |
| Séparation apprentissage et limites | Plafonds codés en dur, changement de risque à deux personnes, kill switch libérable par un humain seulement | H | Séparation structurelle seulement, sans test d'import qui l'impose. | Ingénierie |
| Risk Engine | Indépendant des stratégies ; risque par position, perte du jour, nombre de positions, âge des données, spread, marge ; refus en cas de doute | H | **Limites non calibrées pour le réel.** Manquent : drawdown, exposition notionnelle ou par devise, écart d'exécution. L'equity de début de journée est prise à la première entrée. La perte du jour bloque les entrées sans réduire l'exposition. Refus de tout mode autre que SIMULATION. | Décision de l'architecte (calibrage) ; ingénierie |
| Passerelle d'exécution | Identifiants idempotents, machine d'états transactionnelle, UNKNOWN avec gel, timeout jamais traité comme un échec, rapprochement avant nouvelle tentative | H | Une exception inattendue de l'adaptateur ou de la base après l'envoi laisse l'ordre `SUBMITTED` **sans gel**. Pas de table des exécutions ni des frais. Pas de balayage des ordres expirés. Une clôture faite par le courtier crée une divergence permanente. | Ingénierie ; externe : historique des deals cTrader |
| Doubles ordres et instances | Clé primaire sur l'intention, intentions déterministes | H | Aucun verrou par compte : deux services pourraient trader le même compte. L'ensemble en mémoire n'est pas borné (C-06). | Ingénierie |
| Persistance | SQLite, une connexion par opération | H | Pas de WAL, pas de `synchronous`, pas de version de schéma, pas de contrôle d'intégrité au démarrage. `audit_events` est modifiable. Une restauration peut désactiver un kill switch actif. Perdus au redémarrage : positions du simulateur, historique des barres, registre d'achats spot. | Ingénierie ; décision : PostgreSQL plus tard |
| Kill switch | Global, persistant, audité, libération humaine | H | N'annule pas les ordres en attente et ne gère pas les positions. Bloque aussi les sorties de protection automatiques. Le simulateur n'applique pas le stop. | Décision de l'architecte (mode d'incident) ; ingénierie |
| Tableau de bord | Serveur en lecture seule sur l'adresse locale, contrôle de l'en-tête Host, CSP stricte, vues backtest, ordres, contrôles, modèles | H | Ni authentification, ni TLS, ni temps réel. Les écrans marchés, trading, performance, intelligence et exploitation manquent. Aucun badge SYNTHÉTIQUE par donnée. Accès non journalisé. | Ingénierie ; décisions : TLS, accès, mutations web |
| Sécurité et secrets | Coffre `SecretVault` par variables d'environnement, journaux masqués, CI durcie (Gate B) | H | Aucun magasin de secrets ou de jetons. Audit non infalsifiable. La CI n'a jamais tourné (verrou de facturation GitHub). | Décision (coffre) ; externe : facturation GitHub |
| Supervision et alertes | Heartbeat, mode dégradé | H | Aucun superviseur, aucun redémarrage automatique, aucune alerte livrée, aucun arrêt qui tienne compte des positions. | Décision (canal d'alerte) ; externe : hôte |
| Sauvegarde et restauration | Sauvegarde vérifiée, restauration réservée à un humain avec gel | H | Non chiffrée, non planifiée, aucun exercice de restauration. | Décision (chiffrement) |
| Déploiement | Processus lancé à la main | 0 | Aucune unité de service, aucun conteneur, aucun hôte qualifié. Aucun des deux serveurs AGEROUTE décrits ne convient en l'état. | Externe : hôte dédié ; décision |
| Validation juridique et opérationnelle | Liste des points ouverts (rapport multi-venue, section 8) | 0 | Tout reste ouvert. | **Externe** |

## 3. Fonctions réellement actives aujourd'hui

- **Recherche et backtest sur données synthétiques ou CSV déclarées**, avec walk-forward scellé, PBO et DSR. La promotion refuse les données synthétiques.
- **Service continu sur le simulateur interne**, alimenté par un flux SYNTHÉTIQUE.
- **Tableau de bord local en lecture seule.**
- **Aucun flux réel, aucun ordre externe, aucun apprentissage automatique en production.**

## 4. Plan directeur

Les étapes sont ordonnées par valeur de sécurité. Chacune est réalisable **sans accès externe**, avec tests et revue indépendante, sur une branche dédiée et jamais sur `main`.

| Gate | Contenu | Pourquoi d'abord |
|---|---|---|
| **P1 Exécution sûre et persistance** | Chemin d'envoi qui gèle et passe en UNKNOWN sur toute exception ; verrou d'instance par compte ; SQLite en WAL avec `synchronous=FULL`, version de schéma et `quick_check` au démarrage ; déclencheurs d'ajout seul sur l'audit, les transitions et les rapprochements ; kill switch forcé actif dans une restauration ; C-06 borné ; test « deux rollbacks » puis correction | Supprime des chemins où un incident pourrait laisser un ordre sans gel ou deux moteurs actifs |
| **P2 Données véridiques** | Type de provenance sur `Quote` et `MarketBar` ; `received_at` et âge par côté ; cache vidé à la reconnexion ; surveillance du silence du flux ; détection des trous et du hors-ordre dans `BarBuilder`, avec suspension des décisions ; stockage de barres versionné, rééchantillonnage, rattrapage au démarrage ; porte de registre pour cTrader | Plus aucun ordre fondé sur une donnée douteuse |
| **P3 Décisions et registre** | Journal des décisions en ajout seul (abstentions comprises) ; champion lié à l'exécution, refus en cas d'écart ; corrections du registre (déclencheur, `require_shadow`, preuve shadow traçable) ; tests de frontière apprentissage, limites et LLM | Traçabilité complète et garantie que l'apprentissage ne touche pas aux limites |
| **P4 Apprentissage continu hors ligne** | Exécution shadow (décisions sans ordre), surveillance de dérive selon une politique pré-enregistrée et hachée, gel puis rollback automatique, politique de promotion automatique **désactivée par défaut** | Cycle champion, candidats et shadow démontré sur des scénarios synthétiques étiquetés |
| **P5 Risque étendu** | Drawdown sur pic persistant, plafonds notionnels et par devise, contrôle de l'écart d'exécution, table des exécutions et grand livre P&L, equity de début de journée capturée au changement de jour, modes de kill switch | Couvre les limites exigées, sans prétendre les calibrer |
| **P6 Accès authentifié et tableau de bord** | Authentification (scrypt, sessions, rôles, verrouillage), couche HTTP durcie, audit chaîné, API d'exploitation et SSE, écrans A à H alimentés par les données du système avec badges SIMULATION et SYNTHÉTIQUE, toujours sur l'adresse locale par défaut | Le tableau de bord cible, sans exposition avant décision |
| **P7 Exploitation** | Chien de garde du heartbeat, file d'alertes locale, script d'exercice de restauration, exemple d'unité systemd marqué non testé, check-list de durcissement d'hôte | Prépare l'exploitation continue |

Ces gates ne nécessitent ni compte, ni secret, ni réseau financier. Le passage aux niveaux T puis R suit la mise en service progressive exigée :
1. données réelles en lecture seule ;
2. fournisseur vérifié ;
3. tests autorisés ;
4. shadow sans ordre ;
5. démo ;
6. revue de préparation ;
7. activation réelle sur approbation explicite.

## 5. Décisions demandées à l'architecte

1. **TLS.** Module `ssl` de Python ou proxy inverse. Le proxy inverserait la règle actuelle « jamais derrière un proxy ».
2. **Accès.** VPN seulement ou HTTPS public ; MFA (TOTP faisable avec la bibliothèque standard).
3. **Mutations depuis le web.** Lesquelles autoriser (au minimum l'activation du kill switch ?) et faut-il une validation à deux personnes ?
4. **Temps réel et graphiques.** SSE (recommandé) ou serveur WebSocket. Graphiques écrits à la main ou bibliothèque JS figée et embarquée, ce qui est une décision de dépendance.
5. **Base de données.** SQLite durci tout de suite derrière une interface de stockage (recommandé), puis PostgreSQL. PostgreSQL ajoute un pilote, donc une dépendance non standard.
6. **Chiffrement et secrets.** Chiffrement des sauvegardes et magasin de secrets ou de jetons : dépendance, outil externe ou coffre.
7. **Alertes.** Canal d'alerte : SMTP (bibliothèque standard) ou autre.
8. **Kill switch sur incident.** Bloquer seulement, ou bloquer puis appliquer un plan de sortie audité. Les sorties de protection automatiques doivent-elles passer malgré le kill switch ?
9. **Limites réelles.** Valeurs des limites de risque à proposer à l'opérateur avant toute mise en service ; les valeurs actuelles sont des valeurs de simulation.
10. **Promotion automatique.** Politique et seuils pré-enregistrés à approuver explicitement avant toute activation.
11. **Python.** Version cible : 3.14 (validée) ou maintien du plancher 3.11 déclaré dans `pyproject.toml`.
12. **Hôte.** Dédié et qualifié. Aucun des deux serveurs AGEROUTE ne convient en l'état.

## 6. Blocages externes (aucune ingénierie ne les lève)

- **Compte GitHub :** verrouillé pour un problème de facturation, ce qui empêche toute CI et donc toute fusion protégée.
- **cTrader :** compte démo, application OAuth, puis `ctrader-check` lancé par l'utilisateur.
- **Binance :** accessibilité réseau, admissibilité confirmée et éventuellement une clé en lecture seule saisie par l'utilisateur.
- **Courtiers :** confirmations écrites d'admissibilité pour les résidents de Guinée (cTrader, Binance, IBKR).
- **Juridique :** avis sur le droit guinéen (crypto, dérivés, change BCRG).
- **Données :** source historique réelle sous licence.
- **Ancre de preuve :** stockage immuable externe (WORM).
- **Hôte :** hôte de production dédié.

## 7. Suivi

| Gate | Branche | Statut | Rapport |
|---|---|---|---|
| Gate B (CI et protection) | `feat/multi-venue` | Configurée et revue ; CI jamais exécutée (verrou de facturation) | `reports/GATE_B_REPORT.md` |
| Gate Auth & Deployment (directive reçue le 27/10/2026) | `feat/auth-deploy` | Authentification, sessions, MFA, rôles et configurations de déploiement faits, testés hors ligne (994 tests) et revus (aucun constat critique, élevé ou moyen ouvert) ; publication bloquée (hôte non qualifié, pas de DNS, pas de sudo) | `reports/AUTH_AND_DEPLOYMENT_REPORT.md` |
| P1 à P7 | à créer depuis `feat/multi-venue` | non commencées | à venir |

Aucun ordre réel ou démo n'a été envoyé, aucun fonds n'a été transféré, aucun mode live n'a été activé. Aucune rentabilité n'est revendiquée.
