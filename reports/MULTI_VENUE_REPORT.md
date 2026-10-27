# ASTRA, rapport de clôture du lot multi-plateformes et multi-marchés

**Destinataire : l'architecte principal (ChatGPT)**, pour évaluation au point de contrôle. Copie : l'utilisateur.
Date : 27/10/2026. Branche : `feat/multi-venue`, créée depuis `feat/lot4-integrite-quant` (70b3b4a, publiée).

**Statut : lot NON validé.** Les contrôles de clôture sont terminés et favorables ; la validation relève de l'architecte.

| Repère | Valeur |
|---|---|
| SHA du code vérifié (suite complète, tests ciblés, Ruff, compileall, scan de secrets) | `0d6f084cb55b0564b19ca5a860d86732bb79732d` |
| Commit final de la branche | le commit qui ajoute ce rapport, au-dessus de `0d6f084`. Il ne modifie que des fichiers `.md` et la suite complète est rejouée sur lui (section 7.4). |
| `main` | `60991ed`, inchangée, identique à `origin/main` |
| Publication de la branche | non publiée (aucune demande de publication) |

**Nature des travaux.** Recherche sur sources publiques, et code testé **hors ligne uniquement** :
- aucune plateforme n'a été contactée par le code ;
- aucun compte n'a été ouvert ;
- aucune clé ni aucun secret n'a été demandé ou utilisé ;
- aucun ordre, réel ou démo, n'a été envoyé ;
- aucune porte d'exécution n'a été ouverte ;
- aucun déploiement, aucune fusion dans `main`.

Le mode SIMULATION reste le mode par défaut, la DÉMO est désactivée, le RÉEL verrouillé, futures et options désactivés. Aucune plateforme n'est « intégrée » ni autorisée à trader : un adaptateur ou une classe qui existe ne vaut pas validation. Aucune API externe n'a été validée. Aucune rentabilité n'est revendiquée.

## 1. Résumé

| Point | État |
|---|---|
| Recherche d'admissibilité (Guinée) | Deux dossiers sur sources officielles, consultées le 26/10/2026 : `docs/venues/research_forex.md` et `docs/venues/research_crypto_other.md` |
| Registre central des plateformes | `src/astra/venues/venue_registry.json`, version 0.3.0, 15 plateformes, scellé par empreinte SHA-256 (`c22ec319…e64380`). Toutes les portes sont fermées ; `TRADING_ENABLED_IN_THIS_LOT = False`. |
| Contrat commun | `src/astra/venues/contract.py`, en lecture seule. Les capacités d'ordre sont refusées par construction et chaque capacité absente porte une raison explicite. |
| Modèles par marché | `src/astra/markets/` : Forex/CFD, crypto spot, futures et options. Futures et options sont désactivés (`ENABLED = False`). |
| Risque et coûts par marché | `risk_policies.py`, `costs.py`. Crypto spot : ALLOW seulement en simulation, sans levier ni vente à découvert. Les dérivés sont toujours refusés. |
| Connecteur Binance Spot | `src/astra/venues/binance_spot.py`, données publiques et lecteur de compte en lecture seule. Requêtes GET uniquement, signature HMAC-SHA256. Testé hors ligne seulement ; statut au registre : pas READ_ONLY_CONNECTED. |
| Revues indépendantes | Sécurité (A) : 18 constats (MV-A-1..16, A-17, A-18) ; QA (D) : 18 constats (MV-D-1..19, MV-D-6 non attribué) ; plus 1 constat de clôture. Tous corrigés ou acceptés comme résidus faibles documentés. Verdicts finaux : aucun constat critique, élevé ou moyen ouvert (section 6). |
| Contrôles finaux sur `0d6f084` | Tous les codes de sortie à 0 : Ruff, compileall, scan de secrets, 260 tests ciblés, suite complète de 937 tests (section 7). |

## 2. Agents mobilisés

Tous les agents ont travaillé dans des worktrees Git isolés ou en lecture seule, avec des périmètres distincts. Les relecteurs n'ont écrit aucun code de production et aucun auteur n'a validé seul ses propres correctifs.

| Agent | Rôle | Livrable |
|---|---|---|
| Recherche Forex/CFD | Admissibilité des courtiers et des API | `research_forex.md` : cTrader et ses courtiers, MT5, IBKR, OANDA, FXCM |
| Recherche crypto et autres plateformes | Binance, IQ Option, GoForex | `research_crypto_other.md` |
| Architecte d'intégration | Contrat, registre, modèles par marché | `venues/contract.py`, `venues/registry.py`, `markets/*` |
| Spécialiste crypto spot et API Binance | Connecteur et correctifs MV-A-1..5, MV-D-1..5 | `venues/binance_spot.py`, banc d'essai hors ligne `tests/binance_fixture.py` |
| Spécialiste dérivés et risque | Politiques et coûts par marché, correctifs MV-A-6..9, MV-D-8..12 | `markets/risk_policies.py`, `markets/costs.py`, section de RISK_POLICY.md |
| Documentation et preuves | Registre et documents | `venue_registry.json`, VENUE_REGISTRY.md, BROKERS.md |
| A, sécurité (indépendant) | Revues successives, puis revue finale sur le HEAD réel `9584f46` | MV-A-1..16, A-17, A-18 ; tests de régression |
| D, QA (indépendant) | Revues successives, puis revue finale sur le HEAD réel `9584f46` | MV-D-1..19 ; tests de régression |
| Relecteurs ponctuels (indépendants, deux agents distincts) | Revue de `9584f46` (correctif de test) et revue de `0d6f084` (correctifs A-17, A-18, MV-D-17..19) | Approuvés ; constats faibles consignés (section 6.5) |
| Coordinateur (Claude) | Intégration | Fusions, correctifs MV-A-10..16, MV-D-13, CL-1, A-17, A-18, MV-D-17..19, contrôles, ce rapport |

**Interruptions.** Le disque F: s'est démonté une fois, et le réseau puis l'authentification ont coupé plusieurs agents. Aucun travail n'a été perdu : les agents ont été relancés ou leurs commits récupérés, et l'intégrité du dépôt a été vérifiée (`git fsck`).

## 3. Sources officielles et constats d'admissibilité

Le détail, avec les adresses exactes et les dates de consultation, figure dans les deux dossiers et dans VENUE_REGISTRY.md.

| Plateforme | Constat principal | Statut au registre |
|---|---|---|
| Interactive Brokers | Seul fournisseur dont une page officielle nomme la Guinée comme acceptée. Entité inscrite au registre FINRA (CRD 36418). Le compte papier exige un compte réel financé. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN (contrôles humains restants) |
| cTrader (Spotware) | API Open officielle vérifiée : OAuth, portées `accounts`/`trading`, limites documentées. L'admissibilité dépend du courtier. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| MT5 (paquet Python officiel) | Officiel, Windows uniquement, terminal requis, connexion par mot de passe du courtier. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| IC Markets, Pepperstone, FxPro, Deriv, Exness | Listes de pays exclus seulement indicatives ; aucune confirmation pour la Guinée ; numéros de licence et adresses à vérifier. | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| Binance Spot | API officielle vérifiée. Guinée absente de la liste des pays interdits, sans confirmation positive. Données publiques sans clé via `data-api.binance.vision`. Hôtes injoignables depuis cette machine (expiration réseau) ; aucun contournement tenté. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| Binance Futures | Admissibilité inconnue ; revue du risque des dérivés requise. | BLOCKED |
| IQ Option | Aucune API officielle ; les bibliothèques non officielles sont interdites. | BLOCKED |
| goforexfx.com | Le numéro FCA affiché (487321) appartient, au registre FCA, à une autre entité. | BLOCKED |
| goforextrading.com | Aucun régulateur nommé. | BLOCKED |
| OANDA | Guinée absente ; v20 non offert à la seule entité candidate. | BLOCKED |
| FXCM | Entité internationale non régulée ; API REST en cours d'abandon. | UNVERIFIED, ELIGIBILITY_UNKNOWN |

**Recommandation pour le deuxième courtier Forex : Interactive Brokers,** via l'API TWS et IB Gateway. Critères : accès depuis la Guinée, coûts publiés, qualité de l'API, facilité de validation ; ni le dépôt minimal ni le levier. En repli : IC Markets Global puis Deriv, seulement après une confirmation écrite qu'ils acceptent les résidents de Guinée.

## 4. Architecture et modèles par marché

- **Contrat commun** (`astra.venues.contract`).
  - Classes : `VenueConnector`, `MarketDataConnector`, `AccountReadConnector`.
  - Capacités : chaque capacité est prise en charge ou refusée avec une raison, ce qui expose les différences entre fournisseurs au lieu de les masquer.
  - Fraîcheur : `ensure_fresh` refuse les données périmées ou datées dans le futur.
  - Environnement : il ne peut venir que d'une preuve du fournisseur ; un serveur local reste `UNVERIFIED`.
  - Ordres : ils ne font pas partie du contrat. Ils passeront plus tard par la frontière d'exécution existante (permis HMAC, RiskEngine), après revue.
- **Types partagés** (`astra.connector_types`, MV-D-13). `EnvironmentSource`, `CapabilityNotSupported` et `Quote` y ont été déplacés sans modification ; `astra.brokers.base` les réexporte (mêmes objets). Importer le contrat, le registre, les politiques de risque ou les coûts ne charge **aucun** module `astra.brokers` (vérifié par test et par D dans des sous-processus `python -I`).
- **Registre.** Les statuts sont des preuves cumulées et datées :
  - échelle : DISCOVERED, OFFICIAL_API_VERIFIED, REGION_ELIGIBILITY_VERIFIED, READ_ONLY_CONNECTED, PAPER_TESTED, DEMO_VALIDATED, LIVE_REVIEW_REQUIRED ;
  - statuts négatifs : BLOCKED, UNVERIFIED, ELIGIBILITY_UNKNOWN ;
  - chargeur strict : prérequis vérifiés, empreinte de contenu ; `python -m astra.venues.registry check` affiche `ok 0.3.0 … venues=15` ;
  - `can_trade` vaut faux pour les 15 plateformes.
- **Connecteur Binance Spot.**
  - GET uniquement, chemins limités à `ALLOWED_PATHS` ; données publiques sur `data-api.binance.vision` ; lecteur de compte n'acceptant qu'une clé dont le caractère lecture seule est confirmé par le fournisseur (`/sapi/v1/account/apiRestrictions`).
  - Bannissement 418, arrêt 429 et budget de poids partagés par portée de fournisseur (`binance:production`, `binance:testnet`, `binance:demo`), car Binance limite par IP. Une réponse ultérieure ne raccourcit jamais un bannissement ou un arrêt déjà en vigueur (A-18). Persistance optionnelle avec migration des anciennes clés ; clé inconnue refusée.
  - Porte du registre vérifiée à chaque requête.
- **Marchés distincts.**
  - Forex/CFD : lots, taille de contrat, swaps, commissions, devise de marge.
  - Crypto spot : actifs de base et de cotation, pas de quantité et de prix, notionnel minimal et maximal, frais maker et taker, calculs en Decimal. Noms d'actifs et de symboles en majuscules ASCII uniquement (A-17).
  - Futures : marges, prix de marque, financement. Options à échéance : expiration, payout, prime.
  - Futures et options sont désactivés et ne servent à aucun backtest, risque ou exécution.

## 5. Risque par type de marché

- **Forex/CFD :** simple pré-contrôle ; le RiskEngine existant reste l'autorité. Hors simulation, il refuse les spécifications illustratives et les swaps, commissions ou conversions manquants.
- **Crypto spot :**
  - Mode : ALLOW seulement en SIMULATION, sans levier ni vente à découvert.
  - Plafonds (provisoires, non calibrés) : 2 % du solde libre par achat et 10 % par jour UTC.
  - Registre des achats : un `SpotBuyLedger` en ajout seul, partagé par le processus pour la politique par défaut (`policy_for`). Un achat refusé n'est jamais enregistré : tous les contrôles passent avant l'enregistrement.
  - Identité de la plateforme : `venue_id` doit être canonique (minuscules ASCII, chiffres, soulignés) et déclaré au registre scellé avec le type `CRYPTO_SPOT`, sinon refus (`venue_id_not_canonical`, `venue_not_in_registry`). Un registre illisible refuse tout. Seul `binance_spot` est aujourd'hui déclaré.
  - Données : fraîcheur exigée ; toute valeur inconnue (solde, frais, carnet, position, dernier achat après une perte) entraîne un refus.
  - Économie : espérance nette positive exigée après frais, écart et glissement. Le glissement a un plancher et reste étiqueté UNCALIBRATED.
  - Interdits : martingale, renforcement d'une position perdante, retrait ou élargissement d'un stop.
  - Intégrité : politique finale, paramètres figés, empreinte enregistrée dans chaque décision.
- **Futures et options :** toujours refusés avec « market type not enabled: requires derivatives risk review ». Les exigences de la future revue sont documentées dans RISK_POLICY.md.

## 6. Revues indépendantes et constats

### 6.1 Vérification de l'état Git

Sur la branche active `feat/multi-venue`, `git merge-base --is-ancestor` confirme que le HEAD contient les correctifs :
- `af149f6` (MV-A-10, MV-A-11, MV-A-12) ;
- `065b3d8` et `891e645` (MV-D-14) ;
- `edf4567` (MV-D-13) ;
- `1d3b2ea` et `1cf3c5b` (MV-A-15, MV-A-16) ;
- `0d6f084` (A-17, A-18, MV-D-17..19).

Les modules de test multi-plateformes ne contiennent aucun décorateur `expectedFailure` ni `skip`. Seules deux docstrings mentionnent le mécanisme.

### 6.2 Verdicts finaux

- **A (sécurité), revue finale sur le HEAD réel `9584f46`.**
  - Vérifiés et tenus : MV-A-10 à MV-A-16 et MV-D-14, sondés par variantes d'URL, d'identifiants et de refus.
  - Aucun chemin d'ordre n'est accessible depuis le code en lecture seule.
  - Verdict : « aucun constat critique, élevé ou moyen ne reste ouvert ». Deux nouveaux constats faibles (A-17, A-18) et une docstring périmée, tous corrigés dans `0d6f084`.
- **D (QA), revue finale sur le HEAD réel `9584f46`** (diff `edf4567..9584f46`).
  - Verdict favorable, aucun constat critique, élevé ou moyen ouvert.
  - MV-D-13 confirmé réellement corrigé : zéro module `astra.brokers` chargé ; réexportation identique (`is`) ; définitions inchangées octet pour octet.
  - Trois nouveaux constats faibles (MV-D-17..19), corrigés dans `0d6f084`.
  - Ce verdict porte sur l'état pertinent : le seul changement de code ultérieur, `0d6f084`, a été relu à son tour.
- **Relecteur indépendant de `0d6f084`** (distinct de l'auteur et des agents précédents).
  - A-17, A-18, les nouveaux tests et la documentation : conformes.
  - Chaque nouveau test échoue sans son correctif, ce que le coordinateur a aussi vérifié par mutation : 6 échecs sans les correctifs.
  - Verdict : « aucun constat critique, élevé ou moyen ouvert ». Les points faibles restants sont en section 6.5.
- **Relecteur indépendant de `9584f46`** : correctif du test de délai (CL-1) approuvé. L'égalité exacte à 2 requêtes échoue toujours si le connecteur n'envoie qu'une tentative (mutation `max_attempts=1` confirmée par D).

### 6.3 Constats de A (sécurité)

| Réf. | Gravité | Défaut | Disposition |
|---|---|---|---|
| MV-A-1 | Moyenne | Porte du registre contournable par un enregistrement construit en mémoire | Corrigé (9b3ce5d) : chargeur strict obligatoire sur les hôtes réels |
| MV-A-2 | Moyenne | La seule configuration produisait LIVE + PROVIDER_API | Corrigé (9b3ce5d) : un hôte local reste UNVERIFIED |
| MV-A-3 | Moyenne | Bannissement 418, arrêt 429 et budget de poids propres à chaque instance | Corrigé (9b3ce5d) : état partagé |
| MV-A-4 | Faible | `reverify_after_seconds` sans borne ni validation | Corrigé (9b3ce5d) |
| MV-A-5 | Faible | `SecretValue` sérialisable par pickle et deepcopy | Corrigé (9b3ce5d) |
| MV-A-6 | Faible | `MarketRiskDecision` acceptait ALLOW hors des modes permis | Corrigé (d975378) |
| MV-A-7 | Faible | Politique dérivable et paramètres modifiables sous la même version | Corrigé (d975378) : classe finale, paramètres figés, empreinte |
| MV-A-8 | Faible à moyenne | Anti-martingale ouvert si le dernier achat était inconnu | Corrigé (d975378) |
| MV-A-9 | Faible | Plafond quotidien fourni par l'appelant | Corrigé (d975378) : registre des achats en ajout seul |
| MV-A-10 | Moyenne | État d'arrêt indexé par URL : `api.binance.com`, `:443`, api1..4 avaient des états distincts | Corrigé (af149f6) : état par portée de fournisseur |
| MV-A-11 | Faible | `policy_for` créait un registre d'achats vide à chaque appel | Corrigé (af149f6) : registre partagé par le processus |
| MV-A-12 | Faible | Achat refusé (bande de prix) enregistré quand même | Corrigé (af149f6) : enregistrement après tous les contrôles |
| MV-A-13 | Faible | Bannissement persisté sous l'ancien format de clé oublié après mise à jour | Corrigé (1e7b29c) : migration des anciennes clés |
| MV-A-14 | Faible | L'ancienne entrée persistée rétablissait le bannissement après acquittement humain | Corrigé (dead0d2) |
| MV-A-15 | Faible à moyenne | Plafond quotidien contournable par variantes d'écriture de `venue_id` | Corrigé (1d3b2ea) : `venue_id` canonique exigé |
| MV-A-16 | Faible à moyenne | Plafond quotidien contournable par des `venue_id` inventés de forme canonique | Corrigé (1cf3c5b) : plateforme déclarée au registre avec CRYPTO_SPOT exigée |
| A-17 | Faible | Actifs de cotation sosies Unicode (espace de largeur nulle, pleine chasse, cyrillique) : nouvelles clés du registre d'achats, 19 % au lieu de 10 % | Corrigé (0d6f084) : symboles et actifs en ASCII majuscule ; tests de régression |
| A-18 | Faible | Une réponse 418 ou 429 concurrente plus courte pouvait raccourcir un bannissement ou un arrêt en vigueur | Corrigé (0d6f084) : la valeur la plus stricte est conservée ; tests de régression |

### 6.4 Constats de D (QA) et de la clôture

| Réf. | Gravité | Défaut | Disposition |
|---|---|---|---|
| MV-D-1 | Moyenne | 429 avec Retry-After invalide ou trop long traité comme absent (pause de 60 s) | Corrigé (04ee605) : échec fermé |
| MV-D-2 | Faible | Symbole en double accepté à la troisième occurrence | Corrigé (04ee605) |
| MV-D-3 | Faible | Pas de prix ou de quantité absurde (1E-99) accepté | Corrigé (04ee605) |
| MV-D-4 | Faible | Arrondi au pas faux au-delà de 60 chiffres | Corrigé (04ee605) |
| MV-D-5 | Faible | Bornes minPrice, maxPrice et maxNotional ignorées | Corrigé (04ee605) |
| MV-D-6 | Sans objet | Numéro non attribué par D (aucun constat) | Sans objet |
| MV-D-7 | Faible | Documentation dépassée (connecteur absent des documents, Ed25519 recommandé à tort) | Corrigé (a09529e, registre 0.3.0) |
| MV-D-8 | Moyenne | Dernier achat inconnu après perte accepté (même défaut que MV-A-8) | Corrigé (d975378) |
| MV-D-9 | Faible à moyenne | Achat avec solde de base non nul et position inconnue accepté | Corrigé (d975378) |
| MV-D-10 | Faible | Étiquette de calibration en texte libre | Corrigé (d975378) : énumération |
| MV-D-11 | Faible | Carnet bloqué, croisé ou vide accepté | Corrigé (d975378) |
| MV-D-12 | Faible | Limite de prix pouvant traverser le meilleur prix sans borne | Corrigé (d975378) |
| MV-D-13 | Faible | Le contrat en lecture seule chargeait `astra.brokers.base` | Corrigé réellement (edf4567), voir 6.6 |
| MV-D-14 | Faible | Achat refusé enregistré (même défaut que MV-A-12) | Corrigé (af149f6) |
| MV-D-15 | Faible (documentation) | Ce rapport était dépassé (champs vides, MV-D-13 indiqué ouvert) | Corrigé par la présente version |
| MV-D-16 | Faible (documentation) | RISK_POLICY, ARCHITECTURE et TASKS non alignés sur l'état final | Corrigé (a946dc5), complété dans 9584f46 |
| MV-D-17 | Faible (tests) | Codes de refus `venue_id_not_canonical` et chemin fermé en cas de registre illisible non couverts par des tests ; pas de test d'une plateforme sans CRYPTO_SPOT | Corrigé (0d6f084) : classe `SpotVenueIdentityTests` (6 tests) |
| MV-D-18 | Faible (documentation) | VENUE_REGISTRY.md annonçait la version 0.2.0, l'ancienne empreinte et deux tests en échec | Corrigé (0d6f084) |
| MV-D-19 | Faible (documentation) | BROKERS.md annonçait le registre 0.2.0 et omettait le connecteur Binance | Corrigé (0d6f084) |
| CL-1 | Faible (test) | Test de délai Binance instable : le faux serveur enregistre la requête sur son propre fil, parfois après l'expiration du client | Corrigé (9584f46), test seulement ; connecteur inchangé |

### 6.5 Résidus acceptés (faibles, documentés)

Ces résidus ne sont acceptables que tant qu'aucun chemin d'ordre crypto n'existe. Ils sont à traiter avant toute porte d'exécution.

1. Le registre des achats est en mémoire : un redémarrage le vide. Une `CryptoSpotRiskPolicy()` construite directement a son propre registre.
2. Les fonctions de réinitialisation pour les tests et `acknowledge_ban_cleared` sont accessibles et non authentifiées.
3. L'ensemble des plateformes déclarées est mis en cache pour la vie du processus, et l'appartenance ignore le statut. Avec une deuxième plateforme spot, un solde attribué à la mauvaise plateforme aurait son propre plafond. Le test qui fige l'ensemble à `{binance_spot}` devra alors être mis à jour.
4. L'horloge, la position, le dernier achat et l'espérance attendue sont fournis par l'appelant.
5. La bande de prix limite aussi les sorties urgentes.
6. La persistance de l'état d'arrêt n'existe que si `state_path` est fourni. Entre plusieurs processus partageant le fichier, `save()` écrase l'entrée sans nouvelle fusion ; « jamais raccourci » ne vaut qu'à l'intérieur d'un processus (antérieur à ce lot de correctifs).
7. Après A-18, l'exception `BinanceIpBanned.banned_until` et le journal indiquent la durée de la nouvelle réponse, pas celle du bannissement plus strict effectivement en vigueur. L'application reste correcte : une nouvelle tentative anticipée est refusée localement, sans requête.
8. Variantes ASCII avec ponctuation (`USDT.`) : elles exigent un solde du même nom fabriqué par l'appelant, comme tout actif inventé. Les symboles Binance restent limités à `[A-Z0-9]{2,20}`. Restreindre les actifs à `[A-Z0-9]+` serait une amélioration peu coûteuse.
9. Si Binance renvoyait un jour un nom d'actif non ASCII, la lecture du compte entière serait refusée (échec fermé, perte de disponibilité seulement ; non vérifiable sans réseau).
10. Signature HMAC uniquement : Ed25519 exigerait une dépendance hors bibliothèque standard (décision d'architecture).
11. `binance_spot` (le connecteur) importe encore les exceptions de `astra.brokers.base`. Le contrat, le registre et les politiques n'en chargent aucun module.
12. Aucune validation sur une vraie API.

### 6.6 Disposition définitive de MV-D-13

- **Comportement initial.** `astra.venues.contract` importait `Quote`, `EnvironmentSource` et `CapabilityNotSupported` depuis `astra.brokers.base`. Importer le registre ou les politiques de risque chargeait donc le paquet des adaptateurs de courtier (abstrait, sans code d'envoi d'ordre).
- **Risque.** Faible : aucun code d'ordre n'était chargé, mais l'isolement annoncé du code en lecture seule n'était pas vrai. Un futur ajout dans `astra.brokers` aurait pu devenir accessible depuis le code de politique.
- **Correction** (`edf4567`). Les trois types sont déplacés sans modification dans `astra.connector_types`, et `astra.brokers.base` les réexporte. Le périmètre n'est pas élargi : aucun comportement ne change et les adaptateurs existants restent compatibles.
- **Preuve vérifiable.**
  - `test_multi_venue_verification.ImportAndDocHonestyTests.test_policy_modules_do_not_load_the_broker_adapter_module`, `test_market_risk.NoOrderPathTests` et `test_multi_venue_security_review.DerivativesAndImportsTests.test_regression_no_import_path_to_order_sending_code` s'exécutent dans des sous-processus et réussissent sur `0d6f084`.
  - D l'a confirmé indépendamment.
- **Statut : fermé**, sans exception.

## 7. Contrôles et tests

Chaque commande a été lancée séparément, sans tube ni filtre susceptible de masquer un code de sortie. Chaque code est enregistré tel quel dans `summary.txt`, avec les journaux complets. Interpréteur : Python 3.14, bibliothèque standard seule. Le scan de secrets couvre les fichiers suivis et le diff depuis `origin/feat/lot4-integrite-quant`.

### 7.1 Exécution sur `1cf3c5b` : échec, non retenue

| Commande | Code | Résultat |
|---|---|---|
| `python -m ruff check src tests` | 0 | Propre |
| `python -m compileall -q src tests` | 0 | Propre |
| Scan de secrets | 1 | 2 alertes. Ce sont des **noms** de variables d'environnement (`ENV_API_KEY = "ASTRA_SECRET_BINANCE_API_KEY"`, idem pour le secret), pas des valeurs : faux positif. La règle du scanner a été précisée : une valeur qui n'est qu'un identifiant en majuscules n'est plus signalée. |
| Tests ciblés (7 modules) | 0 | 251 tests, OK |
| Suite complète `unittest discover -v` | 1 | 927 tests : 1 échec, 4 erreurs, 15 échecs attendus. L'échec est CL-1. Les 4 erreurs sont des sous-processus Python morts au démarrage (code Windows 0xC0000142, STATUS_DLL_INIT_FAILED) sous forte charge, la revue de A tournant en parallèle ; ces 4 tests ont réussi dans les tests ciblés et lors de 3 réexécutions isolées. Cette exécution n'est pas comptée comme réussie. |

### 7.2 Exécution sur `9584f46` : interrompue, non retenue

Ruff 0, compileall 0, scan de secrets 0, tests ciblés 0 (251 OK). La suite complète a été **arrêtée volontairement** en cours d'exécution, sans aucun échec observé jusque-là, parce que les revues finales A et D imposaient un changement de code (`0d6f084`). Ses journaux sont archivés comme dépassés ; son code de sortie 1 provient de l'arrêt et n'est pas un résultat.

### 7.3 Exécution de référence sur `0d6f084` (code final)

SHA `0d6f084cb55b0564b19ca5a860d86732bb79732d`, branche `feat/multi-venue`, arbre propre (`dirty_files=0`), du 26/10/2026 23:42:32 au 27/10/2026 00:18:16 (UTC).

| Commande | Code | Résultat |
|---|---|---|
| `python -m ruff check src tests` | 0 | « All checks passed! » |
| `python -m compileall -q src tests` | 0 | Aucune sortie |
| Scan de secrets | 0 | « no secret found » |
| Tests ciblés : `test_multi_venue_security_review`, `test_multi_venue_verification`, `test_market_risk`, `test_binance_spot`, `test_venue_registry`, `test_venues_contract`, `test_markets` | 0 | 260 tests en 44 s, OK |
| Suite complète `python -m unittest discover -s . -v` | 0 | 937 tests en 2 072 s : **OK (expected failures=15)**, 0 échec, 0 erreur |

Les 15 échecs attendus appartiennent tous aux tests de revue du Lot 4, constats résiduels déjà documentés et publiés dans `reports/LOT_04_REPORT.md` : 1 dans `test_lot4_adversarial`, 7 dans `test_lot4_security_review`, 7 dans `test_lot4_verification`. Aucun n'est un test multi-plateformes.

### 7.4 Commit final (ce rapport)

Le commit qui ajoute ce rapport ne modifie que des fichiers `.md` : ce rapport, README.md, TASKS.md et ROADMAP.md. Cela se vérifie par `git diff --stat 0d6f084 HEAD`. La suite complète, les tests ciblés, Ruff, compileall et le scan de secrets sont rejoués sur ce HEAD final. Leurs codes de sortie figurent dans le message de point de contrôle qui accompagne ce rapport, car un fichier ne peut pas contenir le SHA du commit qui le contient.

### 7.5 Nature des tests

- Tous les tests sont **simulés ou hors ligne**. Le connecteur Binance est testé contre un faux serveur local (`tests/binance_fixture.py`, 127.0.0.1) qui rejoue les formes de réponse documentées ; D a vérifié ces formes contre la documentation officielle.
- **Aucun test sur une vraie API.** Les hôtes Binance ont expiré depuis cette machine. Le contrôle cTrader en lecture seule n'a pas été lancé. Aucun de ces tests ne constitue une validation externe.

## 8. Portes d'exécution et limites restantes

| Porte | État |
|---|---|
| SIMULATION | Mode par défaut, seul mode où la politique crypto spot peut répondre ALLOW |
| DÉMO | Désactivée ; aucune activation automatique liée à un connecteur terminé |
| RÉEL | Verrouillé |
| `can_trade` (registre) | Faux pour les 15 plateformes ; `TRADING_ENABLED_IN_THIS_LOT = False` |
| Capacités d'ordre du contrat | Refusées par construction |
| Connecteur Binance | GET uniquement, aucun chemin d'ordre |
| Futures et options | Désactivés (`ENABLED = False`), refusés par la politique de risque |

**Limites à lever (interventions humaines)**

1. **Binance :**
   - retester l'accès (ping et time) depuis le réseau de production de l'utilisateur, sans aucun contournement ;
   - vérifier l'admissibilité de la Guinée et les conditions de redistribution des données ;
   - toute clé éventuelle devra être en lecture seule, restreinte par IP, retraits et transferts désactivés, séparée de toute clé de trading, et saisie uniquement dans l'environnement de l'utilisateur, jamais dans le dépôt ni dans une conversation.
2. **cTrader :** lancer `ctrader-check` en lecture seule (Priorité A), puis confirmer les hypothèses de l'adaptateur.
3. **Courtiers :**
   - obtenir une confirmation écrite de l'acceptation des résidents de Guinée, de l'entité contractante et de l'accès par API ;
   - pour IBKR : entité, justificatif de domicile, financement, accès papier ;
   - vérifier directement les numéros de licence dans les registres des régulateurs (FSA Seychelles, FSRA ADGM, FCA, etc.).
4. **Guinée, vérifications juridiques :** crypto, dérivés et règles de change de la BCRG (comptes à l'étranger, transferts de fonds), auprès d'un juriste ; statut GAFI/GIABA à confirmer.
5. **GoForex :** envisager de signaler à la FCA le numéro de licence usurpé.

## 9. État Git

- **Branche** `feat/multi-venue` : 35 commits hors fusions au-dessus de `feat/lot4-integrite-quant` jusqu'à `0d6f084` (33 fichiers, environ 10 400 lignes ajoutées), plus le commit de ce rapport. **Non publiée.**
- **Commits principaux :**
  - 2ba1ac6, be797c1 : dossiers de recherche ;
  - 3062ed0 : contrat, registre et modèles ; 9b71dee puis a09529e : registre 0.2.0 puis 0.3.0 ;
  - a236996 : connecteur Binance ; 78eda0d : risque et coûts ;
  - 9b3ce5d, 04ee605, d975378 : premiers correctifs ;
  - af149f6, 1e7b29c, dead0d2 : MV-A-10..14 ;
  - edf4567 : MV-D-13 ; a946dc5 : MV-D-16 ;
  - 1d3b2ea, 1cf3c5b : MV-A-15 et MV-A-16 ;
  - 9584f46 : CL-1 et règle d'identité de plateforme ;
  - 0d6f084 : A-17, A-18, MV-D-17..19.
- `main` est inchangée (`60991ed`, identique à `origin/main`). L'historique n'a pas été réécrit, aucun commit distant n'a été écrasé et aucun secret n'a été publié.

## 10. Questions pour l'architecte et recommandation

**Décisions demandées**

1. Accepter ou non le lot comme outillage en lecture seule, testé hors ligne, en simulation uniquement, avec les résidus de la section 6.5.
2. Autoriser ou non la publication de `feat/multi-venue` (sans fusion dans `main`).
3. Trancher Ed25519 (dépendance externe) contre HMAC seul pour Binance.
4. Exiger ou non la persistance du registre des achats et la fusion du fichier d'état à chaque écriture avant toute future porte d'exécution crypto.

**Recommandation pour la suite** (non lancée ; décision de l'architecte)

- Gate B : protéger le dépôt et ajouter la CI. Elle ne dépend de personne.
- Ensuite, Priorité A : le contrôle cTrader en lecture seule, lancé par l'utilisateur.
- Puis, sous réserve des vérifications d'admissibilité : Binance Spot vers READ_ONLY_CONNECTED depuis un réseau qui y a accès, et IBKR comme deuxième courtier Forex.
- Écartés pour l'instant : IQ Option et GoForex ; les futures crypto sans revue du risque des dérivés.
- Preuve attendue pour avancer : pour chaque plateforme, une admissibilité confirmée par écrit et un contrôle en lecture seule réussi, sans aucun ordre envoyé.

Le travail s'arrête à ce point de contrôle. Gate B, le contrôle cTrader et tout autre lot n'ont pas été démarrés.

**Aucune rentabilité n'est revendiquée. Aucune plateforme n'est déclarée intégrée ni autorisée à trader.**
