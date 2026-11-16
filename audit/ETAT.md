# ÉTAT - mis à jour le 16-11-2026 vers 01:15 UTC

## Lots

| Lot | État | Preuve | Prochaine action |
|---|---|---|---|
| 0 | FERMÉ | `audit/RAPPORT_LOT0.md` | - |
| 1 | Fenêtre de 5 jours en cours (16 au 20-11) | `audit/RAPPORT_LOT1.md` | Samedi 21-11 après 01:00 UTC : `astra ticks gaps`, `verify` et `costs`, puis vérification par un agent distinct |
| 4 | **FERMÉ**, avec une réserve | `audit/lot4/VERIFICATION_2.md`, `audit/lot4/preuves/production.txt` | Réserve : fermer une vraie position par l'arrêt d'urgence, au LOT 5 |
| 2 | **FERMÉ** à la 3e tentative (16ad0e3). Compteur global : 19 390 essais antérieurs | `audit/lot2/VERIFICATION_3.md` | Réserves : `run_backtest` gardé par ses appelants ; 19 tests en quarantaine (D-014) à reprendre au rebranchement des commandes |
| 3 | Non commencé | - | Hypothèse « rollover » enregistrée au registre ; historique de ticks lu dans le terminal par le pont ; verdict provisoire ; réévaluation à 6 mois |
| 5 | Non commencé | - | Passerelle par positions cibles, rapprochement des deals (défaut H du LOT 0), 10 pannes simulées, ordres de test minimaux, levée de la réserve du LOT 4 |
| 6 à 9 | Non commencés | - | Selon l'ordre de marche du mandat |

## Production

- **Release :** 16ad0e3 pour le web, le worker et l'agent. Le pont tourne encore le code chargé au redémarrage de 23:39 UTC, en f31d65c ; il prendra la nouvelle release à son prochain redémarrage.
- **Trading :** le fichier STOP est posé et aucune position ASTRA n'est ouverte. Le DEMO reste suspendu jusqu'à la porte du LOT 5.
- **Limites :** `/etc/astra-risk/mt5-risk.json`, empreinte SHA-256 `e5f5635f...`.
- **Veilleur :** `astra-mt5-emergency` est actif. Il décide à partir des fonds propres enregistrés par le pont, car il reçoit un `IPC timeout` du terminal.
- **Recherche :** la boucle de 120 variantes est arrêtée et tout backtest sans fiche est refusé.

## Blocages

Voir `BESOINS.md` : B-001 (compte démo chez un vrai courtier) et B-002 (séparation réelle des limites).
