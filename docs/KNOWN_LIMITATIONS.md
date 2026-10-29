# Limites constatées

- **Massive** : la clé a été déposée le 2026-10-29 et fonctionne pour M5, M15, H1, H4 et D1, mais l'accès est de fin de journée (dernière bougie : la veille) et limité en nombre de requêtes (le client espace ses appels de 13 secondes après un refus). Une clé précédente avait été collée dans une conversation : ASTRA ne peut pas vérifier qu'elle a été révoquée, c'est à faire sur le site du fournisseur.
- **Historique du courtier sur le serveur** : le terminal MT5 du serveur (sous Wine, sans écran réel) ne charge pas l'historique du compte : ses trois interrogations (fenêtre récente, fenêtre large, par position) répondent vides, alors que le courtier le détient. L'historique est donc alimenté par le terminal du PC (`astra mt5 history-sync`, lecture seule) et date de la dernière synchronisation (visible dans « Fraîcheur »). Les transactions futures du pont ne seront connues qu'après une nouvelle synchronisation. Pistes : afficher l'onglet Historique du terminal (le menu ne se dessine pas sans gestionnaire de fenêtres), ou script MQL5 appelant `HistorySelect`.
- **Stratégie** : les décisions reposent sur les bougies quotidiennes de la BCE (un prix par jour, sans haut ni bas). Elles ne réagissent pas intrajournalièrement. Les bougies M5 à D1 du courtier sont stockées mais ne servent pas encore à l'apprentissage.
- **Performances** : 6 comptes simulés sur 7 perdaient avec la stratégie de base ; aucune performance future n'est démontrée.
- **Historique du drawdown** : il part du premier dépôt et de la série d'equity enregistrée depuis la mise en service du suivi ; il ne couvre pas les heures précédentes.
- **Qualité d'exécution** : seuls les ordres envoyés après la mise en service du suivi ont un prix demandé, un glissement et une latence ; les 5 ouvertures du 2026-10-28 n'en ont pas.
- **Swaps** : le montant des swaps latents (−67,15 USD pour 5 positions en un jour) est rapporté tel quel par le courtier ; sa règle de calcul (triple swap du mercredi) n'est pas vérifiée par ASTRA.
- **Premières ouvertures** : les 5 positions du 2026-10-28 ont été ouvertes automatiquement avant le portail de risque et le rapprochement.
- **Pont MT5 sous Wine** : la première connexion du terminal prend environ 2 minutes ; le service réessaie seul.
- **Test Linux** : `test_init_admin_generates_a_one_time_password…` échoue quand il s'exécute en root (comportement voulu du code).
- **Intégration continue** : GitHub Actions est bloqué par la facturation ; les tests tournent sur le poste de développement et sur le serveur.
- **Autres courtiers** : IQ Option n'a pas d'API officielle (confirmé par son support) ; Binance Demo attend une clé de l'utilisateur ; OKX, Bybit, Capital.com et FXCM ne sont pas qualifiés.
