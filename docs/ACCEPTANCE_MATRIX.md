# Matrice d'acceptation

Statuts : `RÉUSSI`, `ÉCHEC`, `BLOQUÉ`, `NON TESTÉ`. Un test non exécuté n'est jamais compté comme réussi. Dernière mise à jour : 2026-10-29.

## Niveau opérationnel (définition de « terminé »)

| # | Exigence | Statut | Preuve ou cause |
|---|---|---|---|
| 1 | Massive fournit des données autorisées, stockées, qualifiées | RÉUSSI (D1), EN COURS (H1) | clé déposée ; accès confirmé sur M5, M15, H1, H4, D1 ; D1 importé sur 7 paires (environ 622 bougies, 0 rejet) ; accès fin de journée ; limite de requêtes respectée |
| 2 | Backtest reproductible | RÉUSSI | 41 backtests terminés en production ; tests de déterminisme dans `test_papertrade` |
| 3 | Compte Forex DEMO vérifié et connecté | RÉUSSI | terminal MT5 : serveur MetaQuotes-Demo, mode DEMO, devise USD (audit du 2026-10-29) |
| 4 | Cotations du courtier reçues | RÉUSSI | flux toutes les 5 s, 7 paires ; bougies M5 à D1 stockées |
| 5 | Décision justifiée via le Risk Gateway | NON TESTÉ en production | portail codé et testé (`test_mt5_risk`, `test_mt5_bridge`) mais non déployé |
| 6 | Ordre de test DEMO confirmé | RÉUSSI | 2026-10-28 21:22 UTC, achat puis vente de 0,01 lot EURUSD, ordres 11149214323/24, transactions 10896045589/90, code 10009 |
| 7 | Ordres et positions rapprochés avec le courtier | RÉUSSI | production du 2026-10-29 04:05 UTC : 5 positions d'origine confirmée, 7 ordres, 7 exécutions, 1 clôturée ; 4 contrôles comptables OK ; l'historique vient du terminal du PC (limite connue) |
| 8 | Résultats conservés après redémarrage | RÉUSSI | tests `StoreTests.test_everything_survives_a_restart` ; bases conservées lors des déploiements |
| 9 | Entrées automatiques activées seulement après validation des tranches de sécurité | ÉCHEC (corrigé) | les 5 positions ont été ouvertes le 2026-10-28 avant le portail de risque et le rapprochement ; ouvertures suspendues depuis le 2026-10-29 03:12 UTC |
| 10 | LIVE reste verrouillé | RÉUSSI | `/api/status` : `live_locked: true`, mode SIMULATION ; connecteur MT5 limité au mode DEMO et au serveur autorisé |
| 11 | Tableau de bord cohérent, attribué, non fictif | RÉUSSI (données) | version a4e227e en production : résultat net −185,04 USD = −0,12 réalisé − 184,92 latent ; contrôle visuel complet à refaire sur la page publiée |

## Rapprochement du 2026-10-29 (valeurs lues dans MT5)

| Contrôle | Résultat |
|---|---|
| Fonds propres = balance + profit latent + swap latent | 99 855,61 = 99 999,88 − 77,12 − 67,15 : RÉUSSI |
| Balance = dépôts + réalisé | 99 999,88 = 100 000 − 0,12 : RÉUSSI |
| Marge libre = fonds propres − marge | 94 842,95 = 99 855,61 − 5 012,66 : RÉUSSI |
| Résultat net = fonds propres − dépôts | −144,39 = −0,12 réalisé − 144,27 latent : RÉUSSI |
| Ancien affichage du résultat net (+153,35) | ÉCHEC : base prise au premier message reçu ; corrigé |
| Ancien résultat latent (−135,75) | ÉCHEC : sans les swaps ; corrigé |
| Historique vide | ÉCHEC : historique issu du journal du pont ; corrigé (historique du courtier) |
| Origine des 5 positions | RÉUSSI : marque ASTRA du courtier et transaction d'ouverture présente |

## Tests

| Domaine | Statut | Détail |
|---|---|---|
| Suite complète Windows | RÉUSSI (version précédente) | 1 179 tests, 15 échecs attendus des modules `test_lot4_` ; la version courante est en cours d'exécution |
| Suite complète Linux | RÉUSSI avec 1 échec connu | `test_init_admin…` : refusé volontairement quand il s'exécute en root |
| Indicateurs (SMA, RSI, MACD) | RÉUSSI | RSI 70,46 sur l'exemple de référence de Wilder |
| Sécurité : anonyme refusé, CSRF, rôles | RÉUSSI | `test_webapp` |
| Sécurité : jeton d'agent limité aux routes autorisées | RÉUSSI | `test_agent` |
| Portail de risque : refus par règle, profil invalide, données absentes | RÉUSSI | `test_mt5_risk` |
| Doublon d'ordre empêché | RÉUSSI | état `mirrored` et test « pas de réouverture après un stop » |
| Timeout puis rapprochement avant renvoi | RÉUSSI (tests) | `test_mt5_bridge` : réponse perdue après exécution (retrouvée sur le compte, un seul ordre), réponse perdue sans exécution (blocage 2 minutes puis un seul nouvel essai), code de timeout traité comme inconnu, reprise après redémarrage ; non exercé sur le vrai compte |
| Fills partiels, rejets, annulations | RÉUSSI (tests) pour partiel et rejet ; NON APPLICABLE pour l'annulation (ordres au marché seulement) | exécution partielle rapportée avec le volume réellement rempli ; rejet du courtier journalisé une fois, sans répétition à chaque cycle |
| Arrêt d'urgence bloque les nouvelles entrées | RÉUSSI | essai réel du 2026-10-29 06:31 UTC, autorisé par l'utilisateur : arrêt d'urgence activé, fichier STOP retiré, 1 cycle complet du pont exécuté, 0 ordre envoyé, panneau « Entrées bloquées » ; STOP remis ensuite ; aucune entrée en attente pendant l'essai, donc le refus d'une entrée précise reste couvert par les tests |
| Sauvegarde et restauration | NON TESTÉ | sauvegarde quotidienne active ; restauration à tester sur une copie |
| Responsive de la page | NON TESTÉ sur mobile | contrôle visuel desktop fait |
