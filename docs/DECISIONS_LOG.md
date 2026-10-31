# Journal des décisions

| Date | Décision | Raison |
|---|---|---|
| 2026-10-28 | Les ordres MT5 passent par le terminal du compte, sans mot de passe dans ASTRA (PC) | éviter de stocker un secret de trading sur la machine du pont |
| 2026-10-28 | Le site web n'a aucun chemin vers l'exécution ni l'arrêt d'urgence | des tests de sécurité l'imposent ; réduit les dégâts si le site est compromis |
| 2026-10-28 | Jetons d'agent révocables à portées (`read`, `lab`, `mt5`) | un agent ne reçoit jamais un mot de passe ni un code TOTP |
| 2026-10-29 | Heure du serveur MT5 convertie en UTC par la règle européenne (UTC+2 l'hiver, +3 l'été) | mesurée : 3,36 pips d'écart contre 11,72 pour la règle américaine sur les semaines où elles diffèrent |
| 2026-10-29 | Terminal MT5 et pont sur le serveur sous WineHQ 11.20 et Python 3.12 | Wine 10.0 d'Ubuntu déclenche la fausse alerte « débogueur » de MT5 ; Python 3.14 échoue sous Wine (numpy) |
| 2026-10-29 | Mot de passe du compte démo déposé par l'utilisateur seul (`astra-set-mt5-demo-password`) | un assistant ne saisit jamais de mot de passe |
| 2026-10-29 | Les bougies sont envoyées au serveur par paquets de 250 | le proxy refuse les corps de plus de 64 Ko |
| 2026-10-29 | Portail de risque propre aux ordres MT5, hors de la stratégie | la stratégie ne peut pas autoriser sa propre exécution ; les sorties ne passent jamais par lui |
| 2026-10-29 | Comptabilité unique côté serveur, historique issu du courtier (`mt5accounting`) | le résultat net était calculé depuis le premier instantané reçu et ignorait les swaps ; l'historique vide venait du journal du pont |
| 2026-10-29 | Fichier STOP sur le pont : ni ouverture ni fermeture automatique pendant l'audit | consigne de l'audit ; les positions existantes restent protégées par leurs stops chez le courtier |
| 2026-10-29 | Chaque ordre du pont est enregistré sur disque avant l'envoi, avec un identifiant dans le commentaire (`ASTRA:<id>`) ; un résultat inconnu bloque la paire et se règle d'après les positions réelles | un nouvel envoi après une réponse perdue pouvait doubler une position ; la marque ASTRA du courtier reste vérifiable |
| 2026-10-29 | Les boutons de trading du site déposent une demande en base ; seul le pont, avec ses protections, l'exécute | le site n'a toujours aucun accès au courtier ni à l'arrêt d'urgence ; une demande expire en 2 minutes et n'est jamais exécutée en retard |
| 2026-10-29 | Un ordre du site ne choisit jamais son volume (calculé par le risque), est refusé sous STOP ou arrêt d'urgence, et porte un numéro magique distinct (20261029) | le pont automatique ne ferme alors jamais une position posée à la main ; fermer et resserrer un stop restent toujours possibles |
| 2026-10-29 | « Reprendre » retire seulement le fichier STOP et reste refusé tant que l'arrêt d'urgence est actif ; l'arrêt d'urgence se commande et se lève en ligne de commande | le site ne peut pas lever un verrou de sécurité |
| 2026-10-29 | Clés Massive lues dans l'en-tête `Authorization`, jamais dans l'adresse | une clé dans une adresse finit dans les journaux |
| 2026-10-29 | Les agrégats Forex de Massive sont étiquetés « cotations, pas transactions » | la documentation du fournisseur le précise ; aucun volume centralisé n'est affiché |
| 2026-10-29 | Interrogation de la BCE toutes les 30 minutes en semaine dès 14:00 UTC, jusqu'à l'enregistrement de la bougie du jour (6 h sinon) | la bougie du jour était signalée en retard jusqu'à 6 h après sa publication |
| 2026-10-31 | Quatrième famille de stratégie « cassure de canal » (plus haut / plus bas des bougies lentes précédentes, bougie courante exclue), ouverte à l'apprentissage champion/challenger | diversifier les candidats ; la bougie courante hors du canal évite d'utiliser une information non connue avant la clôture |
| 2026-10-31 | Sharpe, Sortino, Calmar et baisse maximale calculés sur les fonds propres du compte (par jour UTC, 252 jours), jamais affichés sous 10 rendements journaliers ; régime de marché (tendance ou range, volatilité haute ou basse) par paire sur les bougies D1 | un ratio sur un échantillon trop court est trompeur ; les définitions sont montrées en infobulle |
| 2026-10-31 | Restauration testée sur une copie de la dernière sauvegarde de production, jamais sur la base active | prouver que la sauvegarde sert, sans risque pour la production |
