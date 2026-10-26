# ASTRA, rapport du lot multi-plateformes et multi-marchés

Date : 26/10/2026. Branche : `feat/multi-venue`, créée depuis `feat/lot4-integrite-quant` (70b3b4a, publiée). Destinataires : l'utilisateur et l'architecte principal (ChatGPT).

**Nature des travaux.** Recherche sur sources publiques, et code testé **hors ligne uniquement** :
- aucune plateforme n'a été contactée par le code ;
- aucun compte n'a été ouvert ;
- aucune clé n'a été utilisée ;
- aucun ordre n'a été envoyé.

Le mode SIMULATION reste actif, la DÉMO est désactivée, le RÉEL verrouillé, et `main` n'a pas été modifiée. Aucune plateforme n'est « intégrée » : un adaptateur ou une classe qui existe ne vaut pas validation.

## 1. Résumé

| Point | État |
|---|---|
| Recherche d'admissibilité (Guinée) | Deux dossiers sur sources officielles, consultées le 26/10/2026 : `docs/venues/research_forex.md` et `docs/venues/research_crypto_other.md` |
| Registre central des plateformes | `src/astra/venues/venue_registry.json`, version 0.3.0, 15 plateformes, scellé par empreinte. Toutes les portes sont fermées et aucune plateforme ne peut trader. |
| Contrat commun | `src/astra/venues/contract.py`, en lecture seule. Les capacités d'ordre sont refusées par construction et chaque capacité absente porte une raison explicite. |
| Modèles par marché | `src/astra/markets/` : Forex/CFD, crypto spot, futures et options. Futures et options sont désactivés (`ENABLED = False`). |
| Risque et coûts par marché | `risk_policies.py`, `costs.py`. Crypto spot : ALLOW seulement en simulation, sans levier ni vente à découvert. Les dérivés sont toujours refusés. |
| Connecteur Binance Spot | `src/astra/venues/binance_spot.py`, données publiques et lecteur de compte en lecture seule. Requêtes GET uniquement. Testé hors ligne seulement. |
| Revues indépendantes | Sécurité (A) et QA (D). Aucun défaut critique ou élevé ; les correctifs sont intégrés et en cours de re-vérification (section 6). |

## 2. Agents mobilisés

Tous les agents ont travaillé dans des worktrees Git isolés, avec des périmètres de fichiers distincts. Les relecteurs n'ont écrit aucun code de production.

| Agent | Rôle | Livrable |
|---|---|---|
| Recherche Forex/CFD | Admissibilité des courtiers et des API | `research_forex.md` : cTrader et ses courtiers, MT5, IBKR, OANDA, FXCM |
| Recherche crypto et autres plateformes | Binance, IQ Option, GoForex | `research_crypto_other.md` |
| Architecte d'intégration | Contrat, registre, modèles par marché | `venues/contract.py`, `venues/registry.py`, `markets/*` |
| Spécialiste crypto spot et API Binance | Connecteur | `venues/binance_spot.py`, banc d'essai hors ligne, 51 tests au départ |
| Spécialiste dérivés et risque | Politiques et coûts par marché | `markets/risk_policies.py`, `markets/costs.py`, section de RISK_POLICY.md |
| Documentation et preuves | Registre et documents | `venue_registry.json` (0.2.0), VENUE_REGISTRY.md, BROKERS.md |
| A, sécurité et secrets (indépendant) | Revue | MV-A-1 à MV-A-9 |
| D, QA (indépendant) | Revue | MV-D-1 à MV-D-13 |
| Coordinateur (moi) | Intégration | Fusions et conflits, contrat partagé, corrections de documentation (MV-D-7), registre 0.3.0, rapport |

**Interruptions.** Le disque F: s'est démonté une fois, et le réseau puis l'authentification ont coupé trois agents. Aucun travail n'a été perdu : les agents ont été relancés ou leurs commits récupérés.

## 3. Sources officielles et constats d'admissibilité

Le détail, avec les adresses exactes et les dates de consultation, figure dans les deux dossiers et dans VENUE_REGISTRY.md.

| Plateforme | Constat principal | Statut au registre |
|---|---|---|
| Interactive Brokers | Seul fournisseur dont une page officielle nomme la Guinée comme acceptée. Entité inscrite au registre FINRA (CRD 36418). Le compte papier exige un compte réel financé. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN (contrôles humains restants) |
| cTrader (Spotware) | API Open officielle vérifiée : OAuth, portées `accounts`/`trading`, limites documentées. L'admissibilité dépend du courtier. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| MT5 (paquet Python officiel) | Officiel, Windows uniquement, terminal requis, connexion par mot de passe du courtier. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| IC Markets, Pepperstone, FxPro, Deriv, Exness | Listes de pays exclus seulement indicatives ; aucune confirmation pour la Guinée ; numéros de licence et adresses à vérifier. | UNVERIFIED, ELIGIBILITY_UNKNOWN |
| Binance Spot | API officielle vérifiée. Guinée absente de la liste des pays interdits, sans confirmation positive. Données publiques sans clé via `data-api.binance.vision`. Injoignable depuis cette machine, sans doute à cause d'un filtrage réseau. | OFFICIAL_API_VERIFIED, ELIGIBILITY_UNKNOWN |
| Binance Futures | Admissibilité inconnue ; revue du risque des dérivés requise. | BLOCKED |
| IQ Option | Aucune API officielle ; les bibliothèques non officielles sont interdites. | BLOCKED |
| goforexfx.com | Le numéro FCA affiché (487321) appartient, au registre FCA, à une autre entité. | BLOCKED |
| goforextrading.com | Aucun régulateur nommé. | BLOCKED |
| OANDA | Guinée absente ; v20 non offert à la seule entité candidate. | BLOCKED |
| FXCM | Entité internationale non régulée ; API REST en cours d'abandon. | UNVERIFIED, ELIGIBILITY_UNKNOWN |

**Recommandation pour le deuxième courtier Forex : Interactive Brokers,** via l'API TWS et IB Gateway. Les critères retenus sont l'accès depuis la Guinée, les coûts publiés, la qualité de l'API et la facilité de validation, et non le dépôt minimal ni le levier. En repli : IC Markets Global puis Deriv, seulement après une confirmation écrite qu'ils acceptent les résidents de Guinée.

## 4. Architecture et modèles par marché

- **Contrat commun.**
  - Classes : `VenueConnector`, `MarketDataConnector`, `AccountReadConnector`.
  - Capacités : chaque capacité est explicitement prise en charge ou refusée avec une raison, ce qui expose les différences entre fournisseurs au lieu de les masquer.
  - Fraîcheur : `ensure_fresh` refuse les données périmées ou datées dans le futur.
  - Environnement : il ne peut venir que d'une preuve du fournisseur, sinon il est `UNVERIFIED`.
  - Ordres : ils ne font pas partie du contrat. Ils passeront plus tard par la frontière d'exécution existante (permis HMAC, RiskEngine), après revue.
- **Registre.** Les statuts sont des preuves cumulées et datées :
  - échelle : DISCOVERED, OFFICIAL_API_VERIFIED, REGION_ELIGIBILITY_VERIFIED, READ_ONLY_CONNECTED, PAPER_TESTED, DEMO_VALIDATED, LIVE_REVIEW_REQUIRED ;
  - statuts négatifs : BLOCKED, UNVERIFIED, ELIGIBILITY_UNKNOWN ;
  - le chargeur est strict, vérifie les prérequis et une empreinte de contenu ;
  - `can_trade` vaut toujours faux dans ce lot.
- **Marchés distincts.**
  - Forex/CFD : lots, taille de contrat, swaps, commissions, devise de marge.
  - Crypto spot : actifs de base et de cotation, pas de quantité et de prix, notionnel minimal et maximal, frais maker et taker, calculs en Decimal.
  - Futures : marges, prix de marque, financement.
  - Options à échéance : expiration, payout, prime.
  - Les deux derniers sont désactivés et ne servent à aucun backtest, risque ou exécution.

## 5. Risque par type de marché

- **Forex/CFD :** simple pré-contrôle ; le RiskEngine existant reste l'autorité. Hors simulation, il refuse les spécifications illustratives et les swaps, commissions ou conversions manquants.
- **Crypto spot :**
  - Mode : ALLOW seulement en SIMULATION, sans levier ni vente à découvert.
  - Plafonds (provisoires, non calibrés) : 2 % du solde libre par achat et 10 % par jour UTC, suivis dans un registre des achats en ajout seul.
  - Données : fraîcheur exigée ; toute valeur inconnue (solde, frais, carnet, position) entraîne un refus.
  - Économie : espérance nette positive exigée après frais, écart et glissement. Le glissement a un plancher et reste étiqueté UNCALIBRATED.
  - Interdits : martingale, renforcement d'une position perdante, retrait ou élargissement d'un stop.
  - Intégrité : politique finale, paramètres figés, empreinte enregistrée dans chaque décision.
- **Futures et options :** toujours refusés avec « market type not enabled: requires derivatives risk review ». Les exigences de la future revue sont documentées dans RISK_POLICY.md.

## 6. Revues indépendantes et défauts

| Réf. | Gravité | Défaut | État |
|---|---|---|---|
| MV-A-1 | Moyenne | La porte du registre pouvait être contournée par un enregistrement construit en mémoire | Corrigé : chargeur strict obligatoire sur les hôtes réels |
| MV-A-2 | Moyenne | Un serveur local pouvait produire un environnement « fournisseur » | Corrigé : le local est toujours UNVERIFIED |
| MV-A-3, MV-D-1 | Moyenne | Bannissement IP et pause sur 429 oubliés par une nouvelle instance ; Retry-After invalide traité comme une pause de 60 s | Corrigés : état partagé par hôte, échec fermé |
| MV-A-8, MV-D-8, MV-D-9 | Faible à moyenne | Anti-martingale et position inconnue ouverts par défaut | Corrigés |
| MV-A-4 à 7, MV-A-9, MV-D-2 à 5, MV-D-10 à 12 | Faible | Intervalle de revérification, secret sérialisable, décision forgeable, politique plus laxiste sous la même version, plafond quotidien fourni par l'appelant, symboles en double, ticks absurdes, Decimal, prix min/max, glissement nul, carnet bloqué, limite de prix | Corrigés |
| MV-D-7 | Faible | Documentation dépassée | Corrigé (registre 0.3.0) |
| MV-D-13 | Faible (formulation) | `astra.brokers.base` (abstrait) est chargé via le contrat | Formulation corrigée ; reste ouvert et documenté |

Les verdicts initiaux d'A et de D sont favorables : publiable comme outillage en lecture seule testé hors ligne, sans défaut critique ou élevé. La re-vérification des correctifs est en cours : [VERDICTS DE RE-VÉRIFICATION].

## 7. Tests

| Exécution | Résultat |
|---|---|
| Modules multi-plateformes après intégration de tous les correctifs (8831d88), 10 modules | 296 tests OK, 1 échec attendu (MV-D-13) |
| Suite complète sur `feat/multi-venue` | [RÉSULTAT DE LA SUITE COMPLÈTE] |
| Ruff et compileall | Propres |

- **Nature :** tous les tests sont **simulés ou hors ligne**. Le connecteur Binance est testé contre un faux serveur local qui rejoue les formes de réponse documentées, et D a vérifié ces formes contre la documentation officielle.
- **Aucun test sur une vraie API.** Les hôtes Binance ont expiré depuis cette machine. Le contrôle cTrader en lecture seule n'a pas encore été lancé.

## 8. Interventions humaines restantes

1. **Courtiers :** obtenir une confirmation écrite de l'acceptation des résidents de Guinée, de l'entité contractante et de l'accès par API. Pour IBKR : entité, justificatif de domicile, financement, accès papier.
2. **Registres des régulateurs :** vérifier directement les numéros de licence (FSA Seychelles, FSRA ADGM pour Binance, etc.).
3. **Droit guinéen :** crypto, dérivés et règles de change de la BCRG, auprès d'un juriste.
4. **Binance :** retester l'accès (ping et time) depuis votre réseau de production et vérifier les conditions de redistribution des données. Une clé Binance éventuelle doit être en lecture seule, restreinte par IP, retraits désactivés, saisie uniquement dans votre environnement.
5. **cTrader :** lancer le contrôle en lecture seule (Priorité A).
6. **GoForex :** envisager de signaler à la FCA le numéro de licence usurpé.

## 9. État Git

- **Branche** `feat/multi-venue` : 18 commits hors fusions au-dessus de `feat/lot4-integrite-quant` (28 fichiers, environ 9 800 lignes ajoutées), non publiée.
- **Commits principaux :**
  - 2ba1ac6, be797c1 : dossiers de recherche ;
  - 3062ed0 : contrat, registre et modèles ;
  - 9b71dee : registre 0.2.0 ;
  - a236996 : connecteur Binance ;
  - 78eda0d : risque et coûts ;
  - b98a759, 5d08f6d, b1a4cbf : tests de revue ;
  - 9b3ce5d, 04ee605, d975378 : correctifs ;
  - a09529e : registre 0.3.0 et documents.
- `main` est inchangée, l'historique n'a pas été réécrit et aucun secret n'a été publié.

## Recommandation d'architecture pour la suite

- **Meilleure prochaine tâche : Gate B, protéger le dépôt et ajouter la CI.** Elle reste prioritaire et ne dépend de personne.
- **Ensuite, Priorité A :** le contrôle cTrader en lecture seule, que vous lancez.
- **Puis, sous réserve de vos vérifications d'admissibilité :**
  - Binance Spot : passage à READ_ONLY_CONNECTED en lecture seule supervisée depuis un réseau qui y a accès ;
  - IBKR : comme deuxième courtier Forex.
- **Écartés pour l'instant :**
  - IQ Option et GoForex, sans API officielle ou avec une licence non confirmée ;
  - les futures crypto, sans revue du risque des dérivés ;
  - la signature Ed25519, qui exige une bibliothèque hors de la bibliothèque standard ; à décider avec l'architecte.
- **Preuve attendue pour avancer :** pour chaque plateforme, une admissibilité confirmée par écrit et un contrôle en lecture seule réussi, sans aucun ordre envoyé.

**Aucune rentabilité n'est revendiquée. Aucune plateforme n'est déclarée intégrée.**
