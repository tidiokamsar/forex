# Lot 0 : audit et sécurisation du travail existant

Date : 2026-11-10. Dépôt audité : copie propre de `feat/auth-deploy` (commit `915da73` au départ), parce que le volume F: du poste est signalé « réparation complète nécessaire » par Windows. Aucun push, déploiement, fusion ni changement de branche n'a été fait pendant ce lot.

## 1. Faits vérifiés

- **Checkout** : F: est lisible mais fragile (journal Windows : erreurs matérielles de lecture et d'écriture). F: est sur `dfb1468`, avec 2 fichiers modifiés non commités (`mt5_bridge.py` et son test : le correctif de fermeture refusée, publié ensuite en `934d49c`). Je n'y ai pas touché. La copie de travail est un clone de GitHub (identique à `origin/feat/auth-deploy`).
- **Worktrees** : 19 worktrees d'agents sous `.claude/worktrees` sur F:, sans modification locale. Non touchés.
- **Documents** : `ARCHITECTURE.md`, `SPEC.md`, `ROADMAP.md`, `RISK_POLICY.md`, `SECURITY.md`, `RESEARCH_PROTOCOL.md`, `DATA_POLICY.md` sont à la racine du dépôt ; `docs/` contient les ADR 0001 à 0004, le journal des décisions, la matrice d'acceptation, le rapport final. Un sous-agent a d'abord cru les documents absents parce qu'il les cherchait dans `docs/` : constat corrigé ici.
- **Production** (lecture, 2026-11-10) : version `915da73`, compte démo connecté, 2 positions, fichier STOP absent, arrêt d'urgence inactif depuis la levée du 2026-10-31 par l'opérateur. Les entrées automatiques sont donc actives sur le compte DEMO.
- **Quatre revues indépendantes en lecture seule** ont été exécutées (recherche et données ; risque et sécurité ; architecture et challengers ; tests et preuves). Leurs constats ont été recoupés avec le code avant d'être retenus.

### Écarts constatés entre le cadre de la mission et le code (vérifiés)

| # | Écart | Preuve | Gravité |
|---|---|---|---|
| 1 | Promotion automatique des champions, sans revue humaine ; le compte simulé promu alimente aussitôt le pont | `learning.learn` puis `jobs.run_job` | Critique |
| 2 | Pas de mode shadow prospectif : la seule observation après promotion est le compte géré qui pilote déjà le pont | recherche de « shadow » sans résultat dans `learning`, `jobs`, `mt5_bridge` | Élevée |
| 3 | Contrôle final réévalué à chaque passage (365 jours en D1, 120 jours en H1) : il sert de fait à choisir | `learning.learn`, docstring du module | Élevée |
| 4 | Diplôme et rétrogradation automatiques sur un seuil universel de 20 transactions simulées | `learning.graduate_or_demote`, `LIVE_MIN_TRADES` | Élevée |
| 5 | Remplacement possible du champion chaque jour (cadence de 20 h) | `jobs.LEARN_INTERVAL` | Moyenne |
| 6 | Seuils fixés sans justification : `MIN_TRADES`, `MIN_SCORE_GAIN`, Sharpe dégonflé « validé » à 0,80 (le protocole écrit dit 0,95) | `learning.py` | Moyenne |
| 7 | Plafond d'exposition nette au dollar à 3 fois les fonds propres, sans politique documentée | `mt5_risk.RiskLimits.max_net_usd_exposure_x` | Moyenne |
| 8 | Le pipeline de champions n'utilise ni le registre d'essais chaîné, ni la politique de recherche, ni le registre de promotion avec garde humaine déjà codé (`registry.promote`, `governance`) | recherche d'imports | Moyenne |
| 9 | Provenance : l'apprentissage ne stocke ni empreinte des bougies, ni nature de la source, ni trous ; rien ne distingue synthétique, historique, shadow, démo, réel | `learning.learn` (`data_json`) | Moyenne |
| 10 | Les déploiements précédents ont été faits sans demander d'approbation à chaque fois | historique de la session | Processus |

### Défauts de code confirmés et corrigés dans ce lot (localement, non déployés)

| # | Défaut | Correction | Test |
|---|---|---|---|
| A | Le pont ne transmettait pas le prix des positions au portail : l'exposition au dollar valorisait toute position au prix de l'ordre en cours (erreur de -50 % à +13 000 %, avec des refus erronés possibles) | `account_state` transmet `price_open` et `price_current` ; la règle échoue fermée si un prix, un sens ou la devise du compte (USD) est inconnu | `test_mt5_risk` (3 tests), `test_mt5_bridge` (1 test) |
| B | Si la lecture du pipeline H1 échouait (incident réseau), une paire pilotée par le H1 retombait sur le quotidien et sa position pouvait être fermée si le quotidien disait « sans position » | les paires H1 sont laissées telles quelles tant que la lecture H1 échoue ; une ligne `hourly_unavailable` est journalisée ; une vraie décision continue de fermer | `test_mt5_bridge` |

## 2. Hypothèses (non vérifiées)

- La taille de contrat de 100 000 est supposée pour les 7 paires ; elle n'est pas lue dans les spécifications du courtier. La règle d'exposition dépend de cette hypothèse.
- Le comportement du Sharpe dégonflé avec des rendements dépendants (autocorrélation) n'est pas mesuré : aucune correction n'est appliquée.
- L'heure de publication de la BCE est supposée proche de 14:15 (heure de Francfort) ; la bougie quotidienne est horodatée à cette heure.
- Les écarts de prix entre l'historique Massive (cotations) et les bougies du courtier (MT5) ne sont pas mesurés.

## 3. Décisions et raisons

- **Pas de push ni de déploiement sans approbation** : le cadre l'impose. Les corrections A et B restent locales dans la copie de travail.
- **Corriger A et B avant tout nouveau chantier** : ce sont des défauts du code que j'ai écrit, ils touchent le chemin des ordres, et ils sont petits et réversibles.
- **Ne pas encore modifier la gouvernance des promotions** : passer en revue humaine et shadow change le comportement de la production. Trois options sont comparées ci-dessous ; le choix revient à l'opérateur.

### Options pour la revue humaine et le shadow (recommandation en gras)

| Option | Principe | Effort | Limite |
|---|---|---|---|
| A | statut « candidat » : `learn()` enregistre le candidat sans l'activer ; un opérateur l'approuve | faible | pas de shadow |
| **B** | A, plus un compte simulé propre au candidat (décisions journalisées sans ordres), critères fixés à l'enregistrement, comparaison au champion et aux références, puis revue humaine | moyen | tables et compte de plus |
| C | brancher `learning` sur `registry.promote` (garde humaine déjà codée) | moyen à élevé | fort couplage, adaptation du modèle de données |

Recommandation : livrer B en deux temps (A d'abord, qui supprime l'écart critique), arrêter la graduation et la rétrogradation automatiques (suspension et retour arrière audités seulement), et ne plus utiliser la cadence quotidienne que pour générer des candidats, jamais pour les activer.

## 4. Fichiers modifiés dans ce lot

`src/astra/mt5_risk.py`, `src/astra/mt5_bridge.py`, `tests/test_mt5_risk.py`, `tests/test_mt5_bridge.py`, `docs/LOT0_AUDIT.md`, `docs/RAPPORT_FINAL.md`, `docs/KNOWN_LIMITATIONS.md`, `RISK_POLICY.md`, `SECURITY.md`.

## 5. Validations réellement exécutées

- Tests ciblés sur le serveur Linux (version locale avant correction de l'attendu d'un test) : 88 tests, 1 échec dû à une erreur d'arithmétique de mon propre test, corrigé ensuite.
- Suite complète Linux de la version corrigée : voir le message de fin de lot (résultat reporté tel quel).
- Aucune exécution Windows complète sur cette version : le disque F: est défaillant et l'environnement Python local en dépend.

## 6. Niveau de preuve

Synthétique (tests unitaires et jeux de données de test) pour les corrections ; historique pour l'apprentissage ; démo pour les ordres déjà passés. Aucune preuve prospective (shadow) n'existe encore. Aucune preuve de performance ne peut être tirée de ce lot.

## 7. Risques et limites restantes

- La production exécute encore la version `915da73`, qui contient les défauts A et B tant que les corrections ne sont pas déployées.
- Le risque agrégé se limite au nombre de lots et à l'exposition nette au dollar : aucune limite par devise, par volatilité ou par perte cumulée sur un scénario.
- Un échec de rapprochement ne gèle pas les nouvelles entrées (alerte seulement).
- Le résultat d'une demande du site est posté sans protection contre un rejeu pendant les 120 secondes de validité.
- L'état journalier et le sommet des fonds propres vivent dans un fichier local du pont.

## 8. Dépendances ou décisions humaines requises

1. **Approbation de pousser et déployer** les corrections A et B (elles corrigent des défauts de production).
2. **Choix de l'option de gouvernance** (A seule, B en deux temps, ou C), car elle change la production.
3. Décision sur le disque F: (copie des données, puis réparation) : hors du périmètre du code.

## 9. Prochaine action prioritaire

Après approbation, déployer A et B, puis réaliser le Lot 3 (risque portefeuille : contrats vérifiés, limites par devise, gel sur échec de rapprochement) et la première étape de l'option retenue pour les promotions.

## 10. Suite : déploiement approuvé et exécuté (2026-11-10)

L'opérateur a approuvé le push et le déploiement des corrections A et B. Version en production : `970f128`. Contrôles faits après le déploiement : services web, worker, agent et pont actifs ; applications du serveur répondant ; cycles du pont normaux et aucune action inattendue dans le journal ; portail de risque publié avec tous ses contrôles réussis, dont `devise_du_compte` (compte en USD) ; les 2 positions existantes sont inchangées. L'échec de publication d'un instantané juste après le redémarrage est l'effet normal du redémarrage du site pendant le déploiement. Le risque listé en section 7 sur la version `915da73` est donc levé pour les défauts A et B ; les autres risques de la section 7 restent ouverts.

