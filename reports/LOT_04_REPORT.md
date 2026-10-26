# ASTRA, rapport du Lot 4 : revue indépendante, intégrité quantitative, registre des essais

Dates : 25 et 26/10/2026. Branche : `feat/lot4-integrite-quant`, créée depuis `feat/validation-quant` (a2a1856, qui contient les lots 1 et 2). Destinataires : l'utilisateur et l'architecte principal (ChatGPT). Cadre : consignes du Lot 4 et de sa finalisation, puis directive architecturale V5 (Gate A).

**Nature des données : synthétiques uniquement.** Aucune donnée historique réelle, aucun flux réel, aucune connexion à cTrader, aucun ordre. Le mode SIMULATION reste actif, la DÉMO est désactivée et le RÉEL verrouillé. La branche `main` n'a pas été modifiée.

## 1. Résumé des décisions et des résultats

**Constat de départ.** La revue indépendante a montré que les deux dernières corrections du Lot 3 ne tenaient pas :
- l'empreinte du holdout ne bloquait que les copies exactes ;
- on pouvait fabriquer un holdout complet avec l'API publique du stockage et le faire promouvoir ;
- le nombre d'essais ne couvrait qu'un plan ;
- le PBO reposait sur le P&L réalisé à la sortie des transactions et sur un autre critère que la sélection réelle.

**Résultat.** Cinq tours de corrections, chacun revu par deux agents indépendants (A sécurité, D QA). Leurs verdicts finaux sont favorables : **publiable comme outil de recherche exploratoire en simulation, aucun défaut critique ou élevé ouvert**. Ce n'est **pas** une garantie de promotion : elle supposerait un ancrage externe de production commun à tous les stores, une provenance de données attestée et des rôles humains authentifiés.

**Ce qui est désormais en place (code et tests) :**
- **Registre global des essais.** Il est en ajout seul et chaîné par hachage. Chaque tentative y est enregistrée, quel qu'en soit le statut : retenue, rejetée, échouée, interrompue ou abandonnée. Il porte un identifiant d'expérience global. Le nombre d'essais compté sur tout le store alimente le Sharpe dégonflé.
- **PBO/CSCV conforme au processus réel.** La matrice est réévaluée à chaque barre, la règle de sélection est celle d'ASTRA, et les égalités sont traitées de façon prudente. Le PBO est déclaré `LOW_INFORMATION` dès que l'information manque.
- **Sharpe dégonflé (DSR) adapté aux rendements dépendants.** Il est calculé sur des rendements par barre à intervalle fixe, et non par transaction. L'autocorrélation est mesurée, avec une taille d'échantillon effective selon Lo (2002). Le DSR est retenu (non publié) si la dépendance est trop forte ou si le test de Ljung-Box rejette l'indépendance.
- **Recalcul avant promotion.** Les performances, le PBO, le DSR et le nombre d'essais sont recalculés à partir des barres et de l'enregistrement chaîné de la tentative. Les chiffres d'un rapport ne sont jamais acceptés tels quels.
- **Détection des holdouts réutilisés.** Elle couvre l'étiquette, les dates, l'échelle, le sens de cotation, l'inversion temporelle, le rééchantillonnage par deux et le bruit modéré. Le verdict est enregistré : CONFLICT (refus), SUSPECT ou UNDETERMINED (ni notation ni promotion), CLEAR.
- **Interface d'ancrage hors SQLite.** Elle détecte une restauration ancienne ou une chaîne divergente. Le store est lié à son ancre. Le seul adaptateur fourni est local et **n'est pas de niveau production**.
- **Politique de seuils versionnée (0.5.0, provisoire).** Les seuils sont dans le code et contrôlés à la promotion. Aucun agent ne peut les abaisser par l'API, la CLI ou la base.
- **Porte de provenance.** Les données SYNTHETIC ou de source inconnue ne peuvent pas être promues.

## 2. Agents utilisés et responsabilités

Tous les agents ont travaillé dans des worktrees Git isolés. Les relecteurs (A, D) n'ont écrit aucun code de production. Les correctifs ont été écrits par des agents distincts des relecteurs, puis réexaminés par eux.

| Agent | Rôle | Contribution |
|---|---|---|
| A | Revue de sécurité indépendante | 5 phases. Défauts A-1 à A-5, B-1 à B-8, C-1 à C-6, A4-D-1 à A4-D-6, A5-1 et A5-2, chacun reproduit par un test |
| B | Expert en validation quantitative | Lecture des articles PBO et DSR, matrice à valeur de marché, critère ASTRA dans CSCV, `TrialUniverse`, puis correction du DSR pour la dépendance sérielle (Lo 2002) |
| C | Registre des tentatives | `attempts.py` : événements en ajout seul, déclencheurs, chaîne de hachage, nombre d'essais sur tout le store, commande `attempts list\|count\|verify` |
| D | QA indépendante et adverse | Mesure de départ, scénarios D-1 à D-19, vérifications N, P, Q et R sur 5 phases, recalculs indépendants des formules |
| E | Développeur de l'ancrage | `anchoring.py` : points de contrôle chaînés, `LocalFileAnchor`, vérification d'un store contre son ancre |
| F1 | Correcteur du détecteur | Tours 4 et 5 : verdicts, statistique d'égalité, prise en compte de la dérive, calibration étendue |
| F2 | Correcteur de la chaîne de preuves | Tours 4 et 5 : recalcul du PBO et du DSR, liaison du store à l'ancre, porte de provenance |
| Documentation | Cohérence avec V5 | ARCHITECTURE, DATA_POLICY, DEPLOYMENT, OPERATIONS, BROKERS, RISK_POLICY et README alignés sur le code |
| Coordinateur (moi) | Intégration | Tours 1 à 3 de corrections, fusions et conflits, correctifs A5-2 et R-1, suite complète après chaque intégration, documentation, rapport |

**Erreurs de coordination, déclarées.**
- Les worktrees partaient du commit initial ; chaque agent s'est recalé sur la bonne base.
- J'ai laissé un test A-4 qui ne prouvait plus rien après un changement d'API ; A l'a relevé et réécrit.
- B et F1 ont relevé chacun la version de la politique en 0.3.0 pour des contenus différents ; je l'ai unifiée en 0.4.0, puis passée en 0.5.0.
- Une suite complète de D a duré 11 heures, parce que son dossier temporaire a disparu pendant une mise en veille. Aucun blocage ne s'est reproduit module par module.

## 3. Résultats des deux revues indépendantes

| Phase | Commit revu | A (sécurité) | D (QA) |
|---|---|---|---|
| 1 | a2a1856 | 10 défauts, dont A-1 élevé | Mesure de départ : 361 tests, 22 scénarios en échec |
| 2 | 9eae4a6 | 8 défauts résiduels (B-1 à B-8) | N-1 à N-8 |
| 3 | 0ef781b | Non publiable : C-1, C-3, C-5 élevés | P-1 à P-10, pas d'élevé |
| 4 | f1e0f76 | Pas d'élevé : A4-D-1 à A4-D-6 (moyen ou faible) | Publiable ; Q-1 à Q-4 |
| 5 | 91faef4 puis 451ba7b | **Publiable comme outil de recherche, aucun critique ou élevé** | **Publiable, aucun critique ou élevé** ; R-1 et R-2 (moyen) |
| 5b | e557506 (correctif R-1) | — | **R-1 corrigé, aucun critique ou élevé, publiable** ; test de durée instable sous charge (faible), corrigé en b3da3cc |

## 4. Défauts trouvés et corrections intégrées

| Réf. | Gravité | Défaut | État |
|---|---|---|---|
| A-1, D-2 à D-6, D-15 | Élevée | Holdout renoté après une perturbation infime ou un décalage, puis changement d'étiquette ou de dates | Corrigé (détecteur `holdout_reuse`) |
| A-4, D-1, C-1, P-1 | Élevée | Holdout fabriqué via l'API ; PBO et DSR falsifiés | Corrigé : recalcul à partir des barres et de la tentative chaînée |
| C-3, P-2 | Élevée et moyenne | Nouvelle ancre sur un store restauré ; ancre non de production acceptée | Corrigé : liaison du store à l'ancre, dérogation auditée |
| C-5 | Élevée | Un holdout plat bloque tous les plans | Corrigé |
| D-10, D-13, D-12 | Élevée | Essais comptés pour un seul plan ; nombre sous-déclaré | Corrigé : tout le store, plancher sur la largeur de la matrice |
| D-16 à D-19, N-1, P-8 | Moyenne à élevée | PBO ou DSR « confiants » sur des entrées dégénérées | Corrigé : `LOW_INFORMATION` |
| R-1 | Moyenne | DSR publié sur un échantillon à mémoire longue | Corrigé : porte de Ljung-Box (politique 0.5.0) |
| A4-D-1, A4-D-2, C-2 | Moyenne | Lecture du holdout sans le consommer (store factice, copie non ancrée) | Corrigé |
| A5-2 | Faible à moyenne | Jeu synthétique fourni déclaré HISTORICAL | Corrigé pour le jeu fourni (empreintes connues) |
| B-1, B-3, B-4, B-6, C-6, P-5 à P-7, P-9, Q-1, Q-2, A4-D-4, A4-D-5 | Faible à élevée | Évasions et faux positifs du détecteur | Corrigés ; taux résiduels en section 7 |
| D-7, D-9, D-14, N-2, N-5, P-3, P-10, Q-4 | Moyenne | Suppressions dans la base, copie de preuve, journal réécrit | Corrigés par déclencheurs, contrôles croisés du journal et ancre |
| D-8, P-4 | Moyenne à faible | Code ou seuils modifiés après le holdout | Corrigé |

**Restent ouverts, documentés et couverts par des tests en échec attendu :**

| Réf. | Gravité | Risque |
|---|---|---|
| A4-D-3 | Moyenne, structurelle | Un nouveau store avec une nouvelle ancre peut renoter un holdout. Seule une ancre de production unique par déploiement fermerait cette voie. |
| R-2 | Moyenne | L'étiquette de provenance est déclarée, pas attestée. |
| B-8 | Moyenne | Sans ancre, une restauration rend un holdout réutilisable. Il reste non promouvable. |
| C-4 | Faible | Notation sans ancre ; le résultat n'est jamais promouvable. |
| A5-1 | Faible | Copie du store et de son ancre locale. |
| A-5 | Faible, conception | Le vérificateur de preuves peut être remplacé dans le processus. |
| B-2 | Limite | Copie fortement bruitée non détectée. |
| B-5 | Faible | Ancien plan décalé dans le temps. |
| D-11 | Limite | Exploration hors du store non comptée. |
| N-6 | Faible | Le comptage des essais dépend de l'étiquette exacte de l'instrument. |
| N-7 | Faible | L'unité des Sharpe d'essais n'est pas enregistrée. |
| Q-3 | Faible | Tendance sous le tick, recotée après changement d'échelle. |

## 5. Résultats des tests

```bash
python -m ruff check src tests                        # All checks passed!
python -m compileall -q src tests                     # code de sortie 0
PYTHONPATH=src python -m unittest discover -s tests   # voir ci-dessous
```

| État | Tests | Résultat |
|---|---|---|
| Départ a2a1856 | 361 | OK |
| 9eae4a6 (tour 1) | 484 | OK, 3 échecs attendus |
| 0ef781b (tour 3) | 560 | OK, 11 échecs attendus |
| f1e0f76 (tour 4) | 614 | OK, 10 échecs attendus |
| 91faef4 (tour 5) | 656 | 1 échec : valeur attendue périmée, corrigée en 451ba7b |
| e557506 (final) | 675 | OK, 15 échecs attendus (risques résiduels documentés) ; après b3da3cc, `test_lot4_verification` et `test_lot4_security_review` relancés : OK, 14 échecs attendus |

- **Revues :** les relecteurs ont exécuté les modules un par un. Chaque test en échec attendu a été vérifié sans son décorateur : il échoue sur son assertion, pas sur une erreur.
- **Simulations et validations externes :** tout est local, sur données synthétiques, avec un serveur cTrader simulé et des marches aléatoires. **Aucune validation externe** n'a eu lieu : pas de cTrader réel, pas de données réelles, pas de stockage WORM.
- **Analyse des secrets avant publication :** propre. 38 fichiers modifiés ; aucun secret, aucune base SQLite, aucune ancre ni aucune clé n'est suivie. `.env.example` ne contient que des valeurs vides.

## 6. Fonctionnement du registre global des essais

- **Contenu :** chaque variante notée (`fold_trial`, `walk_forward_run`) et chaque holdout est un événement en ajout seul dans `research_attempt_events`. Il contient :
  - l'expérience globale, le plan, le candidat, les paramètres et leur empreinte ;
  - les versions du code et d'ASTRA, les empreintes des données, l'instrument et la période ;
  - les horodatages, les métriques, le Sharpe d'essai, le statut final et le motif.
- **Statuts :** STARTED, puis COMPLETED_SELECTED, COMPLETED_REJECTED, FAILED, INTERRUPTED ou ABANDONED. Une tentative restée STARTED compte comme interrompue.
- **Intégrité :**
  - les déclencheurs refusent UPDATE, DELETE et tout INSERT non chaîné ;
  - la vérification détecte une modification, une suppression, une réorganisation ou une troncature ;
  - la tentative de holdout enregistre l'empreinte du rapport, l'univers d'essais et les rendements du holdout.
- **Comptage :** distinct par famille, empreinte des paramètres et empreinte des données, sur tout le store, pour toutes les périodes de l'instrument et tous les statuts. Un nouveau plan ne peut qu'ajouter des essais.
- **Limites honnêtes :**
  - les essais faits hors d'ASTRA, sans store ou dans un autre fichier ne sont pas comptés ;
  - SQLite n'est pas inviolable : le propriétaire du fichier peut tout réécrire, ce que seule une ancre externe permet de détecter ;
  - la directive V5 demande de déclarer la recherche externe ; ce mécanisme n'existe pas encore.

## 7. Limites de la détection des holdouts bruités

Calibration documentée dans `tests/test_holdout_reuse_calibration.py` et RESEARCH_PROTOCOL.md : graines fixes, comptes vérifiés exactement, bornes supérieures à 95 %.
- **Copies exactes ou décalées, échelle, inversion, inversion temporelle, rééchantillonnage par deux, bruit à 0,1 fois l'échelle des rendements :** 200 sur 200 en CONFLICT.
- **Bruit à 0,2x :** toujours signalé. **Bruit à 0,3x :** environ 9 % de copies manquées. **Bruit à 0,5x :** environ 85 % manquées. Une copie arbitrairement bruitée ne peut pas être distinguée de données indépendantes.
- **Faux positifs sur des séries indépendantes** (gaussiennes, à queues épaisses, AR(1) 0,7, quantifiées au tick) : 0 sur 600, borne supérieure de 0,5 %. Les données distinctes mais fortement corrélées sont signalées SUSPECT, ce qui est voulu.
- **Non couverts :**
  - les données éparses avec trop peu de mouvements, qui sont UNDETERMINED et ne peuvent donc pas être notées ;
  - les rééchantillonnages autres que par deux ;
  - les tendances sous le tick recotées après changement d'échelle.
- **Coût :** quadratique, environ 2,4 s pour 1500 contre 1500 rendements par plan scellé, calculé hors du verrou d'écriture.

Le contrôle durable reste la provenance des données (historique attesté), pas la recherche de similarité.

## 8. Politique de seuils et ses limites

La version `astra-research-policy-0.5.0-provisional` est fixée dans le code. Elle fait partie de l'empreinte d'implémentation : la modifier invalide les holdouts notés auparavant.

| Seuil | Valeur | Rôle |
|---|---|---|
| Corrélation de conflit / de suspicion | 0,98 / 0,95 | Refus du plan / ni notation ni promotion |
| Rendements par holdout | 16 | Plancher technique du diagnostic ; insuffisant pour promouvoir |
| Transactions pour le Sharpe dégonflé | 30 | Plancher technique ; ne démontre pas la rentabilité |
| Taille d'échantillon effective, \|ρ\| max, p de Ljung-Box | 30 ; 0,3 ; 0,01 | Dépendance sérielle de l'échantillon |
| Tranches PBO, transactions par tranche, lignes non nulles | 4 ; 1 ; 50 % | Information minimale du PBO |

- Dès qu'une condition manque : `LOW_INFORMATION`, aucune valeur publiée, promotion bloquée.
- La politique appliquée est enregistrée et revérifiée à la promotion (`statistical_policy_below_floor`, `holdout_reuse_policy_below_floor`).
- **Limites.** Ce sont des valeurs d'ingénierie provisoires, et non des recommandations financières ou des seuils scientifiques validés. Elles doivent être revues statistiquement et calibrées sur données réelles. Toute modification doit être versionnée, justifiée et approuvée.

## 9. Interface d'ancrage externe : état et configuration restante

- **Implémenté :**
  - l'interface `EvidenceAnchor` : points de contrôle chaînés portant la tête de chaîne précédente et actuelle, la séquence, les holdouts consommés, les plans scellés, les expériences et l'identité de l'ancre ;
  - la liaison d'un store à son ancre ;
  - les vérifications à la notation et à la promotion ;
  - `LocalFileAnchor`, marqué `LOCAL_FILE_NOT_FOR_PRODUCTION`.
- **Garanties :**
  - une restauration ancienne ou une chaîne divergente bloque la notation et la promotion ;
  - sans ancre, la promotion échoue (`evidence_anchor_missing`) ;
  - avec une ancre locale, elle échoue (`evidence_anchor_not_production_grade`), sauf dérogation explicite et auditée d'un relecteur humain. Les rôles étant déclarés et non authentifiés, cette dérogation ne vaut pas approbation humaine prouvée.
- **Reste à faire :** un adaptateur externe immuable (WORM / Object Lock ou équivalent), unique par déploiement et partagé par tous les stores. Aucun fournisseur n'a été inventé ni configuré. **L'immutabilité de production n'est pas acquise.**

## 10. État Git, commits et publication

- **Branche** `feat/lot4-integrite-quant` : 33 commits au-dessus de `feat/validation-quant` (38 fichiers, environ 10 700 lignes ajoutées).
- **Commits principaux :**
  - 3534ae8 : registre des essais ;
  - 9e0da16 : PBO et DSR alignés sur les articles ;
  - ba33e73, b70df72, 0ef781b : tours 1 à 3 ;
  - 8ac78ce : ancrage ;
  - 765ea17, f677654 : tour 4 ;
  - f1d70c1 : DSR et dépendance sérielle ;
  - 3d32479, 3d1c2d5 : tour 5 ;
  - 3909430 : A5-2 ;
  - e557506 : R-1.
- **Les tests des relecteurs ont été intégrés à chaque phase :** 70c7f03, a5a3d4f, 8e4dd60, cfb8d54, 2c0a8d0, b29fa87, e08a690, 9fa2f50, 8f873dc.
- **Historique :**
  - `main` est inchangée (60991ed) ;
  - aucun historique réécrit, aucun envoi forcé ;
  - les branches de lot précédentes ne sont pas modifiées.
- **Publication :** branche `feat/lot4-integrite-quant` publiée sur `origin` après les verdicts favorables de A et D et une suite verte ; aucune autre branche n'a été poussée.

## 11. Risques résiduels et dépendances humaines

**Risques :**
- conclure sur des données synthétiques ou déclarées à tort (R-2) ;
- absence d'ancre de production (A4-D-3, A5-1) ;
- rôles non authentifiés ;
- limites du détecteur (B-2, Q-3) ;
- coût quadratique du détecteur ;
- seuils provisoires ;
- exploration hors du store non comptée.

**Interventions humaines indispensables :**
1. lancer `ctrader-check` en lecture seule avec votre compte démo (Gate C) ;
2. choisir une source d'historique réel et sa licence (Gate D) ;
3. choisir et payer un stockage immuable externe pour l'ancre ;
4. choisir l'hébergement permanent du service.

## Recommandation d'architecture pour la suite

- **Meilleure prochaine tâche : Gate B, protéger le dépôt et ajouter la CI.**
  - Protection recommandée de `main`.
  - CI avec tests, Ruff, compileall, analyse des secrets et des dépendances.
  - Vérification sur un clone propre, sur une machine qui ne se met pas en veille.

  Pourquoi d'abord : chaque lot prend désormais plus de 20 minutes de tests, et une exécution locale a été faussée par une mise en veille. Une CI reproductible est la condition pour faire confiance aux lots suivants. Elle ne dépend de personne.
- **Ensuite :**
  - **Gate C**, sous réserve que vous lanciez le contrôle cTrader ;
  - **Gate D** : historique réel, fichiers bruts immuables, étiquettes V5 attestées. C'est la seule façon de fermer R-2.
- **Bloquants :** Gate C et Gate D dépendent de vous (compte démo, secrets dans votre environnement, source de données). L'ancre de production dépend d'un choix de stockage payant.
- **Alternatives écartées :**
  - brancher cTrader avant la CI : les preuves ne seraient pas reproductibles ;
  - continuer d'affiner le détecteur : son rendement est décroissant, et la provenance des données est le vrai contrôle ;
  - remplacer SQLite : ce n'est pas nécessaire tant que l'ancre externe n'existe pas.
- **Preuves qui justifieraient de passer à la gate suivante :**
  - Gate B : une CI verte sur un clone propre, avec analyse des secrets.
  - Gate C : un `ctrader-check` réussi, avec `orders_sent: 0`, le compte démo confirmé par le fournisseur et les spécifications EURUSD vérifiées.

**Aucune rentabilité n'est démontrée ni revendiquée.**
