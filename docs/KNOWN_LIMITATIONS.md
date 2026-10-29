# Limites constatées

- **Massive** : aucune clé n'est déposée sur le serveur, donc aucune donnée Massive n'a été importée. Si une clé a été visible sur une capture, elle doit être révoquée sur le site du fournisseur avant d'en créer une nouvelle ; ASTRA ne peut pas le vérifier.
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
