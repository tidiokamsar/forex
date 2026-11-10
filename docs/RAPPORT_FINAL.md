# Rapport final ASTRA

Date : 2026-11-01. Branche : `feat/auth-deploy` (la branche principale n'est pas touchée). Version en production : voir la dernière ligne de ce fichier. Statuts : `RÉUSSI`, `ÉCHEC`, `BLOQUÉ`, `NON TESTÉ`. Un test non exécuté n'est jamais compté comme réussi.

## Cadre respecté

- Ordres exclusivement sur le compte DEMO MetaQuotes-Demo ; aucun ordre réel ; le mode LIVE reste verrouillé côté serveur.
- Massive est le fournisseur de données ; il n'achemine aucun ordre.
- Aucun secret dans Git, le navigateur, les journaux ou ce rapport. Les secrets (mot de passe MT5, clé Massive, jeton du pont) sont déposés par l'utilisateur seul, par commande.
- Aucune application existante du serveur n'a été modifiée ; l'ancien serveur 102.211.199.132 n'a pas été touché.
- Aucune fusion dans une branche protégée, aucun push forcé.

## Ce qui fonctionne, avec preuve

| Domaine | Statut | Preuve |
|---|---|---|
| Pont MT5 24 h sur 24 sans le PC | RÉUSSI | terminal et pont sous Wine sur le serveur ; flux toutes les 5 s ; services actifs |
| Comptabilité unique et rapprochement | RÉUSSI | 4 contrôles OK sur le compte réel (balance, fonds propres, marge, résultat net) ; origine des positions confirmée par l'historique du courtier |
| Cycle de vie des ordres | RÉUSSI (tests) | intention écrite avant l'envoi, résultat inconnu rapproché du compte, rejet, exécution partielle ; non exercé sur le vrai compte |
| Portail de risque | RÉUSSI (tests), NON TESTÉ en production | `test_mt5_risk`, `test_mt5_bridge` ; aucune entrée décidée en production depuis son déploiement |
| Arrêt d'urgence | RÉUSSI | essai réel du 2026-10-29 : 1 cycle complet, 0 ordre envoyé ; état revérifié après chaque redémarrage |
| Massive | RÉUSSI | D1 (4 359 bougies) et H1 (85 756 bougies) sur 7 paires, du 2024-10-29 au 2026-10-28 ; rattrapage quotidien planifié |
| Apprentissage champion/challenger | RÉUSSI (fonctionnement), NON PROUVÉ (performance) | 6 familles (tendance SMA, momentum, retour à la moyenne, cassure, force relative des devises en suivi et en inverse), filtre de régime et objectif de profit appris ; promotions tracées ; promotion automatique sans revue humaine : écart au cadre, voir `docs/LOT0_AUDIT.md` |
| Mesures de risque et régimes | RÉUSSI | Sharpe, Sortino, Calmar, baisse maximale, régime par paire ; ratios masqués sous 10 jours d'historique |
| Tableau de bord | RÉUSSI | données réelles, horodatages séparés, aucune valeur fictive ; panneau d'actions vérifié visuellement (rôle administrateur, serveur local) |
| Contrôles de trading du site | RÉUSSI (affichage et tests), NON TESTÉ (exécution réelle) | file de demandes, confirmation avant envoi, volume jamais choisi par la page |
| Sauvegarde et restauration | RÉUSSI | restauration de la sauvegarde du jour sur une copie : 42 tables identiques, intégrité ok, production intacte |
| Tests | RÉUSSI avec réserves | suite complète Linux de la version `915da73` (1290 tests, seul échec connu : `test_init_admin` sous root) ; dernière suite complète Windows : version `3ebfe80` (1247 tests, 15 échecs attendus, 5 ignorés), non refaite depuis : le disque F: est défaillant |

## Ce qui reste BLOQUÉ ou NON TESTÉ

| Sujet | Statut | Cause | Ce qu'il faut |
|---|---|---|---|
| Reprise des entrées automatiques | BLOQUÉ (décision humaine) | l'utilisateur a demandé « pas encore », puis la reprise ; la levée de l'arrêt d'urgence est volontairement un geste humain, et le contrôle de sécurité de la session a refusé mes actions de production sur ce point | l'utilisateur retire le STOP puis lève l'arrêt d'urgence (commandes dans `PRODUCTION_RUNBOOK.md`) |
| Fermeture d'une position depuis le site, en réel | NON TESTÉ | la commande de dépôt de la demande a été refusée par le contrôle de sécurité de la session ; je n'ai pas de session web opérateur | l'utilisateur clique « Fermer » depuis le site, ou autorise la commande |
| Connecteur Bybit DEMO | BLOQUÉ | il faut un compte démo Bybit et une clé sans droit de retrait ni de transfert, déposée par commande | compte et clé (OKX en plan B) |
| Second courtier Forex (Capital.com ou FXCM) | BLOQUÉ | même principe | compte démo et clé |
| Affichage à 375 px | NON TESTÉ | le volet du navigateur impose 701 px de largeur minimale | essai sur un vrai téléphone |
| Premier rattrapage Massive en production | NON TESTÉ au moment du rapport | planifié après 03:00 UTC ; voir la dernière section | observer le journal du worker |

## Limites connues

- L'historique du courtier vient du terminal du PC (`astra mt5 history-sync`), car celui du serveur ne charge pas l'historique.
- Les stratégies décident sur les bougies D1 de la BCE (un prix par jour, sans haut ni bas) ; elles ne réagissent pas en cours de journée. Aucune performance future n'est démontrée.
- La stratégie de cassure n'a pas de mémoire entre deux bougies : dans le canal, aucune position.
- Les ratios de risque reposent sur un historique de fonds propres court : à titre descriptif seulement.
- Les swaps sont rapportés tels quels par le courtier ; leur règle de calcul n'est pas vérifiée.
- La clé MFA n'est pas dans la sauvegarde : elle se sauvegarde séparément, hors de l'hôte.
- GitHub Actions est bloqué par la facturation : ne pas présenter la CI comme verte ; les tests tournent sur le poste de développement et sur le serveur.
- Les 5 premières positions (2026-10-28) ont été ouvertes avant le portail de risque et le rapprochement ; les ouvertures ont été suspendues dès le 2026-10-29.

## Actions de l'utilisateur encore ouvertes

1. Révoquer la clé Massive collée dans une conversation et déposer la nouvelle avec `astra-set-massive-key`.
2. Changer le mot de passe du compte démo MT5 (exposé deux fois) et le redéposer avec `astra-set-mt5-demo-password`.
3. Décider de la reprise des entrées automatiques.
4. Créer un compte démo Bybit (ou OKX) et une clé sans droit de retrait ni de transfert, si le volet crypto doit avancer.

## État du compte démo observé

Au 2026-11-01 (lecture en production) : 2 positions ouvertes (AUDUSD et NZDUSD, vente), fonds propres 98 550,05 USD. Le résultat net, défini comme fonds propres moins dépôts nets, est donc de -1 449,95 USD sur un dépôt de 100 000 USD. Aucune performance n'est promise : le compte sert à valider le dispositif, pas à démontrer un gain.
