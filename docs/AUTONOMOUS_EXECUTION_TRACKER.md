# Suivi d'exécution autonome

États : `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`. Un `DONE` suppose une preuve de test (voir `ACCEPTANCE_MATRIX.md`). Dernière mise à jour : 2026-10-29. Exécution séquentielle (aucun sous-agent lancé).

| Id | Phase | Tâche | Dépend de | Fichiers | État | Preuve | Risque résiduel | Commit | Prochaine action |
|---|---|---|---|---|---|---|---|---|---|
| T-001 | 0 | Inventaire dépôt et production | — | docs/EXECUTION_MASTER_PLAN.md | DONE | inventaire du 2026-10-29 | — | 67a97c5 | — |
| T-002 | 0 | Suspendre les nouvelles ouvertures pendant l'audit | T-001 | fichier STOP du pont | DONE | STOP présent depuis 03:12 UTC, aucune position modifiée | à lever après T-010 | — | lever après la vérification en production |
| T-003 | 1 | Clé Massive en secret serveur | — | deploy/astra-set-massive-key | IN_PROGRESS | fichier 0600 présent, 32 caractères ; révocation de l'ancienne clé non vérifiable | clé collée dans le chat : à révoquer | abaef0b | test d'accès réel après déploiement |
| T-004 | 1 | Test d'accès Massive, import, synchronisation incrémentale | T-003, T-010 | src/astra/massive.py | IN_PROGRESS | accès M5 à D1 confirmé, D1 importé sur 7 paires, H1 en cours ; 429 géré par espacement des requêtes | forfait à fin de journée | 950428f | finir H1, planifier la synchronisation incrémentale |
| T-005 | 2 | Choix du premier exchange crypto DEMO | — | ce fichier | DONE | documentation officielle lue | éligibilité régionale non vérifiable | — | l'utilisateur ouvre le compte |
| T-006 | 3 | Connecteur Bybit DEMO | T-005, compte et clé démo | à créer | BLOCKED (compte utilisateur) | — | — | — | écrire le connecteur public et les tests simulés en attendant |
| T-007 | 4 | Lecture MT5 et rapprochement du compte réel | — | src/astra/mt5accounting.py | DONE | audit du 2026-10-29, équations vérifiées | — | à venir | — |
| T-008 | 4 | Comptabilité côté serveur, historique du courtier, origine, fraîcheur | T-007 | mt5view, mt5accounting, boardstats, labweb | DONE | production du 2026-10-29 : 4 contrôles OK, 5 origines confirmées | historique alimenté par le terminal du PC | e21b59e | — |
| T-009 | 7 | Portail de risque et rapprochement par position | — | src/astra/mt5_risk.py | REVIEW | test_mt5_risk, test_mt5_bridge | non encore exercé en production | 67a97c5 | déployer, essai réel de l'arrêt d'urgence |
| T-010 | 11 | Déploiement de la version suivante | T-008, T-009 | deploy_lab.sh | DONE | a4e227e en production, services actifs, autres applications 200/302 | — | a4e227e | — |
| T-011 | 8 | Reprise des entrées automatiques | T-008, T-009, T-010 vérifiés | pont MT5 | BLOCKED | — | décision humaine souhaitable | — | proposer la levée du STOP après rapprochement |
| T-012 | 10 | Contrôles de trading DEMO sur le site : ordre, fermeture, resserrer le stop, suspendre, reprendre (annulation sans objet : ordres au marché ; arrêt d'urgence : ligne de commande) | T-009, T-015 | mt5view, labweb, mt5_bridge, board.js | REVIEW | suite complète 1236 tests OK ; tests SiteRequestTests et BridgeTests (13 tests), test_board_assets ; panneau non vérifié visuellement en rôle opérateur (la politique de sécurité de la page interdit le code dynamique) | non exercé sur le vrai compte (STOP et arrêt d'urgence actifs) | à venir | déployer, puis essai d'une fermeture sur demande de l'utilisateur |
| T-015 | 7 | États d'ordre, intention enregistrée avant l'envoi, rapprochement après résultat inconnu, rejet, exécution partielle | T-009 | mt5_bridge, mt5_demo_orders | REVIEW | 7 tests de cycle de vie, tous OK ; suite complète 1236 tests OK (2026-10-29) | non exercé sur le vrai compte (STOP en place) | à venir | déployer après la suite complète |
| T-013 | 6 | Mesures de risque, cassure, régimes | — | papertrade, learning | TODO | — | — | — | — |
| T-014 | 12 | Restauration de sauvegarde testée sur une copie | — | — | TODO | — | — | — | — |

## Décision crypto (T-005)

| Critère | Bybit | OKX |
|---|---|---|
| Compte de démo | indépendant du compte réel, son propre identifiant | mode « Demo Trading » du compte |
| Domaine de l'API de démo | `api-demo.bybit.com`, séparé du réel | domaine du réel + en-tête `x-simulated-trading: 1` |
| Flux de marché | public identique au réel ; privé de démo séparé | domaine WebSocket de démo séparé |
| Ordres pris en charge en démo | création, modification, annulation, historique, positions, solde | idem ; pas de dépôt ni de retrait |
| Limites connues | ordres conservés 7 jours ; WebSocket de trading non pris en charge | non précisées par la page lue |
| Risque de mélange démo / réel | faible : domaine différent | plus élevé : même domaine, un en-tête à ne jamais oublier |

Choix : **Bybit en premier**, OKX en plan B. Conditions requises de l'utilisateur : un compte chez l'exchange, dans le respect de ses conditions régionales (non vérifiable ici), puis une clé API de démonstration sans droit de retrait ni de transfert, déposée par commande.
