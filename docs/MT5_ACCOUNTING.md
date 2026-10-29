# Comptabilité du compte MT5 DEMO

Une définition par indicateur, calculée côté serveur par `src/astra/mt5accounting.py`. La page n'effectue aucun calcul.

## Sources

| Donnée | Origine | Remarque |
|---|---|---|
| Balance, fonds propres, marges, devise | `account_info` du terminal | rapportés par le courtier |
| Positions ouvertes (profit, swap, stop, billet, magic, commentaire) | `positions_get` du terminal | |
| Transactions (deals) et ordres | `history_deals_get` et `history_orders_get`, 30 jours à la mise en service puis 2 jours chaque minute | clé = billet du courtier, donc sans doublon |
| Série de fonds propres | un point par minute enregistré par ASTRA, plus le premier dépôt du courtier | le drawdown n'est connu que depuis le début de cette série |
| Décisions de stratégie | comptes simulés « ASTRA auto » | bougies quotidiennes de la BCE |

Aucune transaction n'est reconstituée : si l'historique manque, le chiffre est « inconnu » et la raison est affichée.

## Définitions

| Indicateur | Définition |
|---|---|
| Balance | solde rapporté par le courtier (dépôts, retraits, résultats réalisés, frais et swaps déjà prélevés) |
| Fonds propres | balance + résultat latent |
| Profit latent | somme du profit des positions ouvertes, sans les swaps |
| Swap latent | swaps accumulés sur les positions ouvertes, pas encore passés en balance |
| Résultat latent | profit latent + swap latent |
| Résultat réalisé (hors frais) | somme des profits des transactions de l'historique |
| Frais réalisés | commissions + swaps + frais des transactions de l'historique |
| Résultat réalisé net | résultat réalisé + frais réalisés |
| Dépôts nets | somme des opérations de balance (dépôts moins retraits) |
| Résultat net depuis le début | fonds propres - dépôts nets = réalisé net + ajustements + latent |
| Drawdown actuel | (plus haut des fonds propres - fonds propres actuels) / plus haut, sur la série enregistrée, dépôt inclus |
| Drawdown maximal | pire baisse depuis un sommet sur la même série |
| Ordre | ligne de l'historique des ordres du courtier |
| Exécution | transaction d'achat ou de vente de l'historique (ouverture ou fermeture) |
| Transaction clôturée | position ayant au moins une transaction de fermeture ; son résultat = somme des profits, commissions, swaps et frais de toutes ses transactions |
| Position ouverte | position rapportée par le terminal maintenant |
| Clôturée aujourd'hui | transaction clôturée dont la dernière fermeture date du jour UTC |

Un ordre, une exécution, une transaction clôturée et une position ouverte sont quatre nombres différents. Exemple réel du 29/10/2026 : 7 ordres, 7 exécutions, 1 transaction clôturée (le test), 5 positions ouvertes.

## Contrôles de rapprochement

1. `balance` : dépôts nets + réalisé net + ajustements = balance du courtier.
2. `equity` : balance + résultat latent (profit + swap) = fonds propres du courtier.
3. `marge` : fonds propres - marge utilisée = marge libre.
4. `resultat_net` : réalisé net + ajustements + latent = fonds propres - dépôts nets.

Tolérance : 0,011 (arrondi des chiffres à deux décimales du courtier). Un écart s'affiche en rouge et le statut système passe à « ÉCART DE COMPTABILITÉ ».

## Origine d'une position

Une position n'est « ouverte par ASTRA (confirmée) » que si le courtier lui-même la marque (magic 20261028 et commentaire `ASTRA`) **et** si sa transaction d'ouverture figure dans l'historique avec la même marque, ordre et billet cités. Sinon : « position du compte — origine non confirmée ».

## Fraîcheur : neuf horodatages séparés

Cotation reçue, bougie H1 clôturée (MT5), bougie traitée par la stratégie, évaluation de la stratégie, signal, tentative d'exécution, exécution confirmée par le courtier, message du pont, synchronisation de l'historique. Le week-end n'est pas un retard pour le flux de marché. La stratégie travaille sur des bougies quotidiennes de la BCE : la suivante est attendue le jour ouvré suivant vers 16 h 15 UTC.
