# ÉTAT - mis à jour le 16-11-2026 vers 10:30 UTC

## Lots

| Lot | État | Preuve | Prochaine action |
|---|---|---|---|
| 0 | FERMÉ | `audit/RAPPORT_LOT0.md` | - |
| 1 | Fenêtre de 5 jours en cours (16 au 20-11) | `audit/RAPPORT_LOT1.md` | Samedi 21-11 après 01:00 UTC : `astra ticks gaps`, `verify` et `costs`, puis vérification par un agent distinct |
| 4 | **FERMÉ**, avec une réserve | `audit/lot4/VERIFICATION_2.md`, `audit/lot4/preuves/production.txt` | Réserve : fermer une vraie position par l'arrêt d'urgence, au LOT 5 |
| 2 | **FERMÉ** à la 3e tentative (16ad0e3). Compteur global : 19 390 essais antérieurs | `audit/lot2/VERIFICATION_3.md` | Réserves : `run_backtest` gardé par ses appelants ; 19 tests en quarantaine (D-014) à reprendre au rebranchement des commandes |
| 3 | Code déployé (295dc35), suite serveur 1 415 tests OK. Rattrapage de 180 jours d'historique de ticks en cours, une paire et un jour par tour (environ 14 h) | `/root/lot3-suite.log` | À la fin du rattrapage (`hours_backfill.done` dans l'état du pont) : `astra rollover`, preuves, vérification par un agent distinct. La réévaluation à 6 mois tourne seule chaque jour dans le worker |
| 5 | Non commencé | - | Passerelle par positions cibles, rapprochement des deals (défaut H du LOT 0), 10 pannes simulées, ordres de test minimaux, levée de la réserve du LOT 4 |
| 6 à 9 | Non commencés | - | Selon l'ordre de marche du mandat |

## Production

- **Release :** 295dc35 pour tous les services, pont compris (redémarré le 16-11 vers 10:20 UTC ; le curseur des ticks évite tout trou).
- **Trading :** le fichier STOP est posé et aucune position ASTRA n'est ouverte. Le DEMO reste suspendu jusqu'à la porte du LOT 5.
- **Limites :** `/etc/astra-risk/mt5-risk.json`, empreinte SHA-256 `e5f5635f...`.
- **Veilleur :** `astra-mt5-emergency` est actif. Il décide à partir des fonds propres enregistrés par le pont, car il reçoit un `IPC timeout` du terminal.
- **Recherche :** la boucle de 120 variantes est arrêtée et tout backtest sans fiche est refusé.

## Blocages

Voir `BESOINS.md` : B-001 (compte démo chez un vrai courtier) et B-002 (séparation réelle des limites).
