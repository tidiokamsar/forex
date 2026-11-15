# LOT 1 - Socle de données : tests d'acceptation (présentés avant le code)

Branche dédiée : `lot1-socle-donnees`, partant de 24abedd. Aucun ordre, aucune modification des limites de risque ni du LIVE.

## Constat de départ (LOT 0)

- **24abedd** garde une cotation par minute, sans les millisecondes. La section III demande des ticks Bid/Ask horodatés à la milliseconde.
- **Compte :** il est sur MetaQuotes-Demo. Les coûts mesurés là ne représentent aucun courtier réel.
- **Mode de marge :** ni le netting ni le hedging n'est relevé.

## Tests automatiques (unittest, sans terminal réel)

| # | Exigence | Test | Critère de réussite |
|---|---|---|---|
| T1 | Ticks bruts | Le pont lit `copy_ticks_range` depuis le dernier tick connu et publie des lots | Chaque tick est stocké avec bid, ask, `time_msc` serveur, réception locale et flags, sans perte ni réécriture |
| T2 | Pas de doublon | Le même lot est publié deux fois | Aucune ligne en double, la clé est (symbole, time_msc, bid, ask) |
| T3 | Brut immuable | Une donnée corrigée est enregistrée | Elle va dans une table distincte, l'original ne change pas |
| T4 | Données anormales | Ticks désordonnés, ask < bid, prix nuls ou non finis | Ils sont refusés et comptés avec leur motif, rien n'est stocké en silence |
| T5 | Trous | Absence de tick plus longue que le seuil pendant les heures de marché | Un trou est détecté et enregistré ; il n'est pas compté le week-end ni hors horaires du symbole |
| T6 | Fraîcheur | La dernière cotation date de plus de 5 s (PRESET) | La cotation est marquée périmée. L'état est publié, rien n'est bloqué dans ce lot |
| T7 | Dérive d'horloge | L'écart entre l'heure serveur et l'heure de réception est mesuré | Sa médiane et son maximum sont publiés par jour |
| T8 | Spécifications quotidiennes | Contrat, digits, volumes, swap long et court, mode de swap, marge, horaires | Relevés et datés une fois par jour ; un changement crée une nouvelle version, l'ancienne est gardée |
| T9 | Compte | Serveur, société, mode de marge (netting ou hedging), devise | Relevés et stockés. Le serveur `MetaQuotes-Demo` est étiqueté NON_REPRESENTATIF |
| T10 | Empreinte | Un jeu de données est exporté pour (symbole, jour) | Le SHA-256 est stable d'un export à l'autre et la provenance (serveur, version du pont) est jointe |
| T11 | Coûts mesurés | Spread médian et 90e centile par instrument et par heure UTC, à partir des ticks | Le résultat est identique à un calcul de référence sur des ticks connus |
| T12 | Changement d'heure | Des ticks chevauchent un passage à l'heure d'été | Les heures UTC restent continues, sans trou ni doublon artificiel |
| T13 | Volume borné | Rétention et taille des lots | Les lots ont une taille maximale, une purge tourne au-delà de la rétention (PRESET 90 jours) et la base reste sous un plafond surveillé |
| T14 | Isolement | La boucle de collecte échoue | Elle n'empêche ni la gestion des positions ni la passerelle de risque |

## Porte de sortie (preuves sur le serveur, section XIV)

- **P1 :** la suite complète est verte, hors l'échec connu de `test_init_admin` sous root. La sortie brute est archivée.
- **P2 :** 5 jours de marché de ticks sur les 19 instruments, sans trou inexpliqué. Le rapport de trous est archivé.
- **P3 :** les empreintes de jeux de données sont rejouées à l'identique.
- **P4 :** une table des coûts mesurés par instrument et par heure est archivée.
- **P5 :** le mode de marge et le serveur relevés sont archivés.

## Hors périmètre, signalé seulement

- Le trou de rapprochement après une panne (question H), à traiter au LOT 5.
- Les limites de risque dans le code (question I), à traiter au LOT 4.
- Le choix d'un compte démo chez un vrai courtier : c'est votre décision. Le LOT 1 fonctionne sur MetaQuotes-Demo, mais ses coûts y resteront NON_REPRESENTATIF.
