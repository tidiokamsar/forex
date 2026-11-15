# ÉTAT - mis à jour le 15-11-2026 vers 23:10 UTC

## Lots

| Lot | État | Prochaine action |
|---|---|---|
| 0 | FERMÉ | - |
| 1 | En fenêtre de 5 jours (16 au 20-11), déployé en 03c9a35 | Samedi 21-11 après 01:00 UTC : `astra ticks gaps`, `verify` et `costs`, puis vérification par un agent distinct |
| 4 | Code et tests commités sur la branche `lot4-risque` (68f3893). Suite serveur : 1 388 tests OK, 1 en quarantaine. **Non déployé** (D-007) | 1) Relancer la vérification indépendante : l'agent a été interrompu, aucun verdict n'est écrit. 2) Le 21-11 : installer `/etc/astra/mt5-risk.json`, le drop-in `ASTRA_MT5_RISK_FILE` et l'unité `astra-mt5-emergency`, déployer, puis tester l'arrêt d'urgence depuis le processus séparé |
| 2 | Non commencé | Écrire et commiter ses tests ; registre, budget d'essais, arrêt de la boucle de 120 variantes |
| 3, 5 à 9 | Non commencés | Selon l'ordre de marche du mandat |

## État de la production

- Release 03c9a35. Le fichier STOP est posé depuis le 15-11 à 22:35 UTC : le DEMO est suspendu jusqu'à la porte du LOT 5.
- Aucune position ASTRA n'est ouverte.

## Blocages

Voir `BESOINS.md` : B-001 (compte démo chez un vrai courtier) et B-002 (séparation réelle des limites).
