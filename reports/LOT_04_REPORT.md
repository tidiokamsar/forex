# ASTRA, rapport du Lot 4 : revue indépendante, intégrité quantitative, registre des tentatives

Date : 25/10/2026. Branche : `feat/lot4-integrite-quant`, créée depuis `feat/validation-quant` (a2a1856, qui contient bien les lots 1 et 2). Destinataires : l'utilisateur et l'architecte principal (ChatGPT).

**Note sur la consigne.** Le texte reçu s'arrête au milieu de la liste des scénarios de l'agent D (« Tentative »). J'ai exécuté les agents A à D tels que décrits, et appliqué le protocole permanent pour le reste. S'il manquait d'autres agents ou exigences, ils ne sont pas couverts.

## A. Résumé exécutif

**Objectif.** Faire relire de façon indépendante les deux dernières corrections du Lot 3, vérifier PBO/CSCV et Sharpe dégonflé par rapport aux articles, compter tous les essais de la base, et attaquer le tout avec des scénarios adverses.

**Résultat principal.** La revue indépendante a confirmé que **les deux corrections du Lot 3 étaient insuffisantes** :
- **Réutilisation du holdout.** L'empreinte du holdout ne bloquait que les copies exactes. Modifier un prix de 1e-9, ou décaler d'une barre, puis changer d'étiquette ou de dates suffisait pour noter le même holdout une seconde fois.
- **Preuves fabriquées.** On pouvait fabriquer un holdout complet avec les méthodes publiques du stockage, sans écrire de SQL, et le faire promouvoir.
- **Comptage des essais.** Le nombre d'essais ne couvrait qu'un plan.
- **PBO.** Il reposait sur le P&L réalisé à la sortie des transactions et sur un critère différent de la sélection réelle.

**Ce qui est corrigé, en code et en tests :**
- un registre des tentatives en ajout seul, chaîné par hachage, avec un comptage des essais sur toute la base ;
- un PBO réévalué à chaque barre, avec la règle de sélection d'ASTRA ;
- la détection des holdouts réutilisés ;
- surtout, le **recalcul du holdout à partir des données de marché avant toute promotion** : un rapport fabriqué ou falsifié ne passe plus.

**Niveau réel.** Tout est vérifié **sur données synthétiques uniquement**. Aucune stratégie n'est promue ni promouvable. Plusieurs limites restent ouvertes et sont listées en E et F.

**Honnêteté sur l'état de vérification :**
- **Premier tour de corrections** (commit 9eae4a6) : revu par les agents A et D, avec 484 tests réussis.
- **Second tour** (recalcul du holdout, nombre de transactions minimal, contrôles croisés du journal, détecteur durci) : testé par moi, **pas encore revu par un agent indépendant**.

## B. Travail des agents

Tous les agents ont travaillé dans des worktrees Git isolés. Les testeurs (A, D) n'ont écrit aucun code de production.

| Agent | Mission | Résultat |
|---|---|---|
| A, revue de sécurité | Revoir l'empreinte du holdout et le plancher de critères, sans rien corriger | **Tour 1** : 10 défauts confirmés, dont A-1 (élevé, contournement de l'empreinte) et A-4 (moyen, holdout fabriqué via l'API) ; 19 tests. **Tour 2**, sur mes corrections : 8 défauts résiduels (B-1 à B-8) ; il a aussi relevé que j'avais laissé un test A-4 qui ne prouvait plus rien. |
| B, validation quantitative | Comparer le code aux articles et corriger | A lu les deux articles (versions des auteurs) et confirmé les 3 constats du Lot 3 dans le code. Il a corrigé : matrice à valeur de marché, critère ASTRA dans CSCV, égalités traitées de façon prudente, interface `TrialUniverse`. Références dans `docs/references/QUANT_REFERENCES.md`. |
| C, registre des tentatives | Compter tous les essais, avec provenance | Module `attempts.py` : événements en ajout seul, déclencheurs SQLite, chaîne de hachage, comptage sur toute la base, commande `attempts list\|count\|verify`. 15 tests. |
| D, QA adverse | Mesurer l'état de départ, écrire des scénarios d'attaque, vérifier les corrections | **Départ** : 361 tests réussis, 22 scénarios en échec (D-1 à D-19). **Vérification** : la plupart sont corrigés ; il a trouvé N-1 à N-8, dont N-1 (statistiques « complètes » obtenues à partir d'une seule transaction). Il a recalculé lui-même le PBO et l'exemple de l'article sur le Sharpe dégonflé. |
| Coordinateur (moi) | Intégration, résolution des conflits B/C, toutes les corrections issues des revues | Deux tours de corrections ; la suite complète relancée à chaque tour |

**Mon erreur de coordination.** Les worktrees des agents partaient du commit initial et non de a2a1856. Chaque agent l'a vu et s'est recalé ; aucun travail n'a été perdu.

## C. Preuves techniques

```bash
python -m ruff check src tests                        # All checks passed!
python -m compileall -q src tests                     # code de sortie 0
PYTHONPATH=src python -m unittest discover -s tests   # voir ci-dessous
```

- **Départ, a2a1856 :** 361 tests réussis.
- **Premier tour, 9eae4a6, revu par A et D :** 484 tests réussis, 3 échecs attendus.
- **Second tour :** [RÉSULTAT DE LA SUITE COMPLÈTE].

**Défauts confirmés et leur sort :**

| Réf. | Gravité | Défaut | État |
|---|---|---|---|
| A-1, D-2 à D-6, D-15 | Élevée | Holdout renoté après un changement infime de prix, de longueur ou d'alignement, puis un changement d'étiquette ou de dates | Corrigé : détecteur de trajectoire de prix partagée (`holdout_reuse.py`), qui ignore l'étiquette, les dates, l'échelle et le sens de cotation |
| A-2, B-3 | Faible | Cotation inversée, ou trajectoire jouée à l'envers | Corrigé |
| B-1 | Élevée | Rééchantillonnage, par exemple de M15 en M30 | Corrigé pour un facteur 2 ; les autres facteurs ne sont pas détectés |
| B-4 | Élevée | Holdout découpé en tranches de moins de 8 rendements | Corrigé : minimum de 16 rendements, recherche plus fine |
| B-6, N-8 | Moyenne | Faux positifs sur des données presque plates | Corrigé : les fenêtres peu informatives sont ignorées |
| B-7 | Moyenne | Comparaison lente, faite pendant le verrou d'écriture | Atténué : faite avant le verrou. Le coût reste proportionnel au nombre de plans scellés. |
| A-3 | Faible à moyenne | Plan sans empreinte | Corrigé pour les nouveaux plans. Les anciens plans ne sont protégés que par leurs dates (B-5, ouvert). |
| A-4, D-1, N-3 | Élevée | Holdout fabriqué via l'API, ou rapport falsifié dans le fichier | Corrigé : **recalcul à partir des barres** avant promotion, plus les liens avec le plan, le code et le journal des tentatives |
| D-8 | Moyenne | Code modifié après le holdout | Corrigé (`code_changed_since_holdout`) |
| D-9 | Moyenne | Holdout copié sur une version sœur | Corrigé : vérifié aussi à la promotion |
| D-10, D-13 | Élevée | Essais comptés pour un seul plan | Corrigé : toutes les périodes de l'instrument, et injecté dans le Sharpe dégonflé |
| D-12 | Élevée | Nombre d'essais déclaré sous le nombre de variantes testées | Corrigé : le nombre de variantes est un plancher |
| D-16 à D-18 | Élevée | PBO « confiant » sur des variantes identiques | Corrigé : `LOW_INFORMATION` |
| D-19, N-1 | Moyenne à élevée | Statistiques « complètes » à partir d'une seule position tenue | Corrigé : au moins 30 transactions pour le Sharpe dégonflé, et au moins une transaction par tranche et par variante pour le PBO |
| D-7, D-14, N-2, N-5 | Moyenne | Lignes supprimées dans la base, journal réécrit | Atténué : déclencheurs en ajout seul ; le journal est consulté au scellement ; chaque plan scellé doit avoir ses essais ; la tête de chaîne enregistrée est vérifiée |

**Réponse aux 5 questions sur le PBO et le Sharpe dégonflé (agent B, vérifiée par D) :**
1. **Ce que le code calculait** : un P&L affecté à la période de sortie des transactions ; un classement par un ratio de type Sharpe ; les égalités en faveur du premier indice ; le nombre d'essais d'un seul plan.
2. **Conformité CSCV** : la structure est conforme (combinaisons, rang relatif, logit). Les égalités et le contenu de la matrice ne l'étaient pas.
3. **Sélection réelle** : la sélection simulée ne correspondait pas à celle d'ASTRA. C'est maintenant la même règle, au calcul près du drawdown, documenté.
4. **Biais relevés** : zéros structurels, égalités, nombre d'essais sous-estimé, statistiques tirées d'une seule transaction.
5. **Corrections et tests** : voir plus haut. Les exemples calculés à la main et celui de l'article (0,9004) sont reproduits.

## D. Avancement du cahier des charges

| Exigence | État |
|---|---|
| BT-07, AI-05 : essais enregistrés, nombre de variantes | Satisfaite localement : registre des tentatives sur toute la base |
| Section 7.2 : PBO/CSCV, Sharpe dégonflé | Conforme aux articles sur données synthétiques ; limites documentées |
| IMP-03 : critères prédéfinis, aucune promotion sur preuve fabriquée | Renforcée : recalcul des preuves à partir des données |
| Preuve de rentabilité | **Aucune.** Données réelles nécessaires. |

## E. Blocages et interventions humaines

| Blocage | Action |
|---|---|
| Données réelles et attestées | Votre `ctrader-check` en lecture seule, puis l'historique du compte démo. Le recalcul prouve que les chiffres découlent des barres, pas que les barres sont authentiques. |
| Ancrage hors de la base | Choisir où conserver la tête de chaîne et la liste des holdouts consommés (fichier en ajout seul, dépôt Git, service externe). Sans cela, restaurer une ancienne sauvegarde rend un holdout réutilisable (B-8). |
| Seuils du détecteur et des garde-fous | Valeurs de départ (corrélation, 16 rendements, 30 transactions), à revoir avec l'architecte sur données réelles. |

## F. Recommandation technique

**Risques résiduels, documentés et non corrigés :**
- Exploration faite hors de la base (sans base, ou dans un autre fichier) : jamais comptée (D-11).
- Restaurer une ancienne sauvegarde rend un holdout consommé de nouveau utilisable (B-8).
- Le détecteur est une heuristique : un bruit supérieur à environ 0,4 fois l'échelle des rendements, d'autres facteurs de rééchantillonnage ou des tranches courtes passent (B-2).
- Les anciens plans sans rendements stockés ne sont protégés que par leurs dates (B-5).
- `ModelRegistry(evidence_verifier=...)` permet au code exécuté dans le même processus de désactiver la vérification, recalcul compris (A-5).
- Le périmètre des essais suit l'étiquette exacte de l'instrument (`EURUSD` et `EURUSD.X` sont distincts, N-6).
- Les Sharpe d'essais n'enregistrent pas leur unité (N-7). L'effet va dans le sens prudent.
- Les déclencheurs SQLite peuvent être supprimés par le propriétaire du fichier.

**Priorités recommandées pour la suite :**
1. Revue indépendante du second tour de corrections.
2. Ancrer la tête de chaîne et les holdouts consommés hors de la base (ce qui corrige B-8).
3. Données réelles attestées : c'est le vrai contrôle contre la réutilisation et la fabrication de données.
4. Enregistrer l'unité de chaque Sharpe d'essai, normaliser les étiquettes d'instrument, rendre la vérification non remplaçable hors des tests.

Le risque le plus important reste de conclure sur des données synthétiques ou fabriquées. Le code bloque la promotion, mais ne peut pas prouver l'authenticité des données.

## G. État Git et publication

- **Branche** `feat/lot4-integrite-quant`, créée depuis a2a1856. Les branches des agents ont été intégrées par fusion ou par cherry-pick.
- `main` est inchangée. Aucun commit distant n'a été réécrit.
- **Publication** : [ÉTAT DE LA PUBLICATION].
- **Secrets** : aucun ; analyse avant envoi.
- **Exécution** : aucun ordre envoyé, DÉMO non activée, RÉEL verrouillé.
