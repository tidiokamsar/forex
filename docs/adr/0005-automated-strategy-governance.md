# ADR 0005 : gouvernance automatisée des stratégies (simulation d'abord, DEMO sur autorisation)

Statut : accepté le 2026-11-10 (règles fixées par le propriétaire, détail technique décidé par l'ingénieur).

## Contexte

Avant cet ADR, le pipeline d'apprentissage (`learning.py`) promouvait un champion automatiquement et ce champion nourrissait aussitôt le pont DEMO (`mt5_bridge.py`). Le contrôle final était réévalué à chaque passage, un diplôme ou une rétrogradation automatiques s'appuyaient sur un seuil universel de 20 transactions, et aucun journal ne figeait les critères avant l'évaluation. Un audit indépendant (Lot 0, `docs/LOT0_AUDIT.md`) a relevé ces écarts.

Règles du propriétaire : aucune revue technique quotidienne de sa part ; promotion automatique en SIMULATION uniquement ; SHADOW observe sans ordre ; suspension et retour arrière automatiques ; aucun processus automatique ne modifie les limites du moteur de risque, ne déverrouille la DEMO ni ne déploie ; tout est journalisé et auditable.

## Options comparées

| Option | Principe | Effet |
|---|---|---|
| 1. Garder le couplage, ajouter un journal | le champion promu continue d'alimenter le pont | rien n'empêche une promotion de produire des ordres sans autorisation distincte |
| **2. Découpler** (retenue) | la promotion n'agit que sur des comptes d'observation en simulation ; le compte qui nourrit le pont ne change que par une commande humaine, journalisée | respecte « simulation seulement » ; petit changement, sans nouvelle dépendance |
| 3. Brancher `registry.promote` | réutiliser le registre avec garde humaine existant | fort couplage et adaptation du modèle de données ; exige une revue humaine, contraire aux règles |

## Décision

1. **Candidats figés et journalisés avant évaluation.** `learn()` écrit, avant toute évaluation, un évènement `CANDIDATS_ENREGISTRES` : empreinte du lot de candidats, critères, identité des données (source, nature, nombre de bougies, première et dernière bougie, empreinte SHA-256). La table `*_learn_events` est en ajout seul.
2. **Promotion automatique en simulation seulement.** Un candidat qui satisfait tous les critères devient champion : il est appliqué au **compte d'observation** de la paire (sans ordre). Il ne touche jamais le compte qui nourrit le pont DEMO.
3. **Le pont ne lit que le flux DEMO du pipeline quotidien** (comptes gérés, stratégies autorisées par une personne). Le pipeline H1 et tous les comptes d'observation n'envoient aucun ordre.
4. **Autorisation DEMO par une personne** : `astra learn authorize --symbol X --operator NOM --reason TEXTE` applique le champion actuel au compte qui nourrit le pont ; évènement `AUTORISATION_DEMO` journalisé. Aucun processus automatique n'appelle cette fonction.
5. **Revue prospective préenregistrée.** À la promotion, les critères de revue sont calculés à partir des transactions de sélection du candidat et enregistrés avec lui : `n_required` (nombre de transactions observées à partir duquel un test unilatéral à 5 % pourrait confirmer la moitié du rendement moyen en échantillon), plancher de 8 observations avant tout verdict, suspension à t <= -1,28 (10 %), confirmation à t >= 1,645 (5 %). Pas de seuil universel de transactions : le seuil dépend de la dispersion du candidat.
6. **Suspension et retour arrière automatiques** (simulation) si le test sur le résultat observé par transaction est négatif : évènements `SUSPENSION` et `RETOUR_ARRIERE`. La confirmation produit `CONFIRMATION_PROSPECTIVE`. En cas de preuve insuffisante, rien n'est promu et rien n'est confirmé.
7. **Sharpe dégonflé** : le niveau « validé » exige une probabilité d'au moins 0,95 (valeur du protocole de recherche), calculée contre tous les essais cumulés de la paire.
8. **Limites du RiskEngine** : inchangées. La promotion ne peut pas les modifier ; le palier de risque par niveau de champion a été retiré du pont parce que le pont ne suit plus les promotions.

## Conséquences

- **Positives** : plus aucune promotion ne produit d'ordre sans autorisation distincte ; chaque décision est auditable ; le contrôle final et les critères ne sont plus implicites.
- **Négatives et limites** :
  - le contrôle final reste réévalué à chaque passage : c'est une présélection, pas un holdout indépendant ; la preuve indépendante est l'observation prospective (limite documentée, non supprimée) ;
  - l'observation prospective est lente : à raison d'environ une transaction par semaine par paire en D1, une revue statistique exige des mois ;
  - les stratégies du flux DEMO ne s'améliorent plus d'elles-mêmes : tant qu'une personne n'autorise pas, le pont garde ses stratégies actuelles.
- **Comportement de transition** : au déploiement, une position ouverte par l'ancien couplage H1 (par exemple USDCAD) n'est plus portée par une stratégie que le pont lit. Si le pipeline quotidien dit « sans position » pour cette paire, le pont la ferme au cycle suivant (comportement normal de fermeture sur un signal, jamais une ouverture).

## Vérification

Tests dans `tests/test_learning.py` et `tests/test_mt5_bridge.py` : enregistrement avant évaluation, évènements de refus, d'échec et de promotion, observation séparée du flux DEMO, autorisation humaine avec opérateur et raison obligatoires, suspension, confirmation, absence de verdict sous le plancher d'observations ou sans critères enregistrés, pont indifférent au pipeline H1.
