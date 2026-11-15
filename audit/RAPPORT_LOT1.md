# RAPPORT LOT 1 - Socle de données (point d'étape du 15 novembre 2026, lot NON FERMÉ)

## Fait

- **Branche :** `lot1-socle-donnees`, commits 96003d4, 6e0b4a7 et 03c9a35, construite sur 24abedd.
- **Déploiement :** release `/opt/astra/releases/03c9a35`, le 15-11 à 18:28 UTC.
- **Ticks Bid/Ask bruts :** le pont lit `copy_ticks_from` toutes les 10 s. Il convertit l'heure serveur en UTC à la milliseconde et envoie au plus 800 ticks par requête. Son curseur est gardé sur disque et ne bouge qu'après acceptation.
- **Stockage :** une base séparée, `/var/lib/astra/ticks.sqlite3`, gardée 14 jours. Chaque jour UTC est scellé une heure après sa fin dans `ticks-archive/<symbole>/<jour>.csv.gz`, avec un SHA-256 et sa provenance. Un jour scellé n'est jamais réécrit.
- **Qualité :**
  - les ticks invalides sont refusés et comptés par motif ;
  - les trous sont étiquetés (week-end, rollover, inexpliqué) ;
  - la fraîcheur (seuil de 5 s) et le décalage d'horloge sont publiés ;
  - les spécifications et les swaps sont versionnés à chaque changement.
- **Compte :** le serveur, la société et le mode de marge sont relevés.
- **Commande d'audit :** `astra ticks status|gaps|costs|verify`.

## Tests

- **En local :** les tests T1 à T14 se trouvent dans `tests/test_ticks.py`, avec une route ajoutée dans `test_agent.py`.
- **Sur le serveur :** `Ran 1376 tests`, `FAILED (failures=1, expected failures=15)`. Le seul échec est `test_init_admin` sous root, déjà connu. La sortie est dans `/root/lot1-suite.log`.

## Premières preuves en production (marché fermé)

- **Compte :** `astra ticks status` renvoie le serveur MetaQuotes-Demo, la société MetaQuotes Ltd., le mode de marge **HEDGING** et l'étiquette NON_REPRESENTATIF. Cela répond à la question B du LOT 0.
- **Horodatage :** le dernier tick EURUSD reçu est daté du 2026-11-13 à 21:59:59.073 UTC, soit 23:59:59 à l'heure du serveur. La conversion est juste.
- **Catalogue :** 12 157 instruments ont été relevés, avec leurs versions de spécifications.

## Écarts

- **Le 13-11 est scellé partiel.** Le pont n'a pu lire que la dernière minute de ce jour. Le fichier est vrai mais incomplet, et son rapport de trous le montrera. Seuls les jours à partir du 16-11 comptent pour la porte P2.
- **Le décalage d'horloge n'est pas encore mesurable.** Marché fermé, il mesure l'âge des données ; il ne prend son sens qu'avec des ticks en continu.
- **L'interprétation de l'heure par `copy_ticks_from` reste à confirmer.** Il faut vérifier lundi que le flux reste continu, sans trou au fil des cycles.

## Reste pour fermer le lot

- **P2 :** 5 jours de marché sans trou inexpliqué, du 16 au 20-11, avec `astra ticks gaps --day ...` archivé.
- **P3 :** `astra ticks verify`, avec fichiers et base identiques.
- **P4 :** la table `astra ticks costs`.
- **P5 :** l'archive du statut du compte.
- **Ensuite :** votre décision de fermer le lot.
