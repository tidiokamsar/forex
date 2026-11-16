# Décisions prises sans le propriétaire (mandat autonome du 15-11-2026, section 3)

Règle : option la plus prudente au sens du document maître. Chaque décision donne la raison et les alternatives écartées.

## D-001 - 15-11 - Ordre de marche

**Choix :** LOT 1 laissé courir ; LOT 4 avant LOT 2.

**Raison :** les deux sont autorisés en parallèle (mandat 6.2). Le risque passe en premier parce qu'il protège tout ordre futur, et le DEMO est suspendu.

**Écarté :** LOT 2 d'abord.

## D-002 - 15-11 - Risque réel constaté (priorité 5.1)

**Constat :** 9 ordres d'ouverture au journal du pont, du 03 au 13-11. Tous ont une perte au stop de 491 à 496 USD pour environ 99 000 USD de fonds propres, soit **0,5 %** par transaction (le champ `risk_usd` des événements `open`).

**Pourquoi :** le réglage de 0,1 % ne visait que les champions H1 non confirmés, et aucun n'a ouvert de position. L'affirmation 3 est donc réfutée par les tickets eux-mêmes.

**Correction :** au LOT 4, la perte au stop passe à 0,1 % pour toute position, puisqu'aucune stratégie n'est confirmée. C'est la valeur PRESET de la section VIII.

## D-003 - 15-11 - Interprétation des PRESET de risque (section VIII)

| PRESET | Application retenue | Alternative écartée |
|---|---|---|
| 0,1 % par position, stratégie non confirmée | `risk_per_trade_pct` = 0,1 et `unconfirmed_risk_scale` = 1,0 : 0,1 % pour toutes, aucune n'étant confirmée | Garder 0,5 % × 0,2 : les stratégies dites confirmées restaient à 0,5 % |
| Exposition nette par devise ≤ 1 fois le capital | Contrôle par devise (EUR, GBP, JPY, ...), pas seulement l'USD | USD seul, comme avant |
| Levier brut ≤ 3 | Somme des notionnels en USD divisée par les fonds propres | Plafond en lots |
| Mois à -4 % : tailles divisées par deux | Facteur 0,5 sur la taille dès -4 % ; arrêt des entrées gardé à -8 % | Arrêt des entrées dès -4 % (contraire au PRESET) |
| -12 % depuis le plus haut : tout fermer, revue humaine | Arrêt d'urgence automatique : il ferme tout et pose STOP ; seul un humain le retire | Arrêt des seules entrées |
| Âge maximal d'une cotation : 5 s | `max_quote_age_seconds` = 5 | 30 s, comme avant |
| Volatilité cible de 8 % | Reportée au LOT 6 : c'est l'allocateur qui dimensionne le portefeuille | - |

## D-004 - 15-11 - Test en échec (priorité 5.2)

**Constat :** `test_init_admin_generates_a_one_time_password_and_refuses_a_second_admin` est le seul test en échec, et il échoue aussi hors root.

**Cause :** la commande répond `no MFA key ring ... create it first with 'astra auth mfa-key-init'`. Le test décrit l'ancien comportement, où `init-admin` créait le trousseau ; le code actuel exige qu'il existe d'abord.

**Choix :** mise en quarantaine (`@unittest.skip`) avec cette raison, dans un commit séparé du code.

**Écarté :** réécrire l'assertion pour la faire passer, ce que la section 4 du mandat interdit.

## D-005 - 15-11 - Limites hors du dépôt (principe 5)

**Choix :** fichier `/etc/astra/mt5-risk.json`, propriété de root, mode 0644, lu par le pont via `ASTRA_MT5_RISK_FILE` (drop-in systemd). Sans ce fichier, toute entrée est refusée.

**Limite connue :** l'agent dispose d'un accès root par SSH. Le principe 5 n'est donc tenu que par discipline et par trace : toute modification du fichier change son empreinte SHA-256, et le pont publie cette empreinte. Une séparation réelle demanderait un accès que seul le propriétaire contrôle (voir BESOINS B-002).

## D-006 - 15-11 - Deux tests corrigés pour l'exposition par devise (commit séparé)

**Choix :** le moteur prend le taux en USD d'une devise dans le prix même d'une paire qui a une jambe en dollar : l'ordre, ou une position ouverte. Une croisée exige, elle, des taux venant des cotations du terminal. Sans eux, elle est refusée.

**Correction de deux tests :**
- `test_lot4_risk` R3 supposait qu'un ordre EURUSD ne permettait pas de mesurer l'EUR, ce qui était faux : son propre prix le donne. L'assertion de refus porte maintenant sur une croisée, EURGBP.
- `test_mt5_risk` (écart sur les croisées) acceptait une croisée sans aucun taux. Le LOT 4 l'interdit, puisque l'exposition n'y est pas mesurable. Le test fournit désormais des taux, et ce qu'il vérifie (le plafond d'écart) n'a pas changé.

**Écarté :** autoriser une exposition non mesurable, ce qui contredit la règle du refus par défaut.

## D-007 - 15-11 - Déploiement du LOT 4 différé à la fin de la fenêtre du LOT 1

**Choix :** le code du LOT 4 est construit et testé maintenant. Le redémarrage du pont, qui est aussi l'enregistreur de ticks, attend la fin de la fenêtre de 5 jours du LOT 1 : le samedi 21-11, après le scellement du vendredi.

**Raison :** mandat 6.1, « Ne redémarre pas l'enregistreur ». Le risque est nul en attendant, puisque le DEMO est suspendu (fichier STOP) et qu'aucune position ASTRA n'est ouverte.

**Écarté :** redémarrer le pont tout de suite et créer un trou dans la fenêtre du LOT 1.

## D-008 - 15-11 - Indépendance de l'arrêt d'urgence

**Choix :** un service séparé, `astra-mt5-emergency`, avec son propre processus Python et sans jeton de site ni mot de passe. Il s'attache au terminal MT5 déjà connecté.

**Limite connue :**
- Il partage le serveur Wine du pont. Quand le pont s'arrête, son `ExecStopPost` (`wineserver -k`) tue aussi le terminal et le veilleur ; celui-ci redémarre seul (`Restart=always`).
- Si le terminal est arrêté, aucun processus ne peut fermer de position. Les stops posés côté serveur chez le courtier restent alors la protection.

**Écarté :** un second terminal MT5, qui demanderait une seconde connexion avec le mot de passe, donc un secret de plus.

## D-009 - 15-11 - LOT 4 déployé tout de suite, sur instruction du propriétaire (D-007 révisée)

**Choix :** le propriétaire a demandé de déployer le LOT 4. Le pont est donc redémarré pendant la fenêtre du LOT 1.

**Raison :** le redémarrage ne crée pas de trou de données. Le curseur des ticks est gardé sur disque, et au redémarrage le pont relit dans l'historique du terminal les ticks de la coupure. Un trou se mesure sur l'horodatage des ticks, pas sur leur heure d'arrivée. Le rapport de trous du 16-11 le montrera.

**Écarté :** attendre le 21-11, contraire à l'instruction.

## D-010 - 15-11 - Corrections après la vérification n° 1 du LOT 4 (porte non franchie)

**Défauts et corrections :**
1. **`astra mt5 test-order` envoyait un ordre hors passerelle et hors STOP.** La commande est désactivée et ne fait plus que refuser. Les ordres de test du LOT 5 passeront par la passerelle.
2. **Des fonds propres NaN ou infinis ne déclenchaient pas l'arrêt.** C'est corrigé : un chiffre non fini déclenche maintenant l'arrêt.
3. **Une erreur autre que `Mt5DemoError` faisait tomber le veilleur.** Toute erreur est désormais écrite au journal, et le veilleur continue.
4. **Une demande « resume » du site retirait aussi le STOP posé par l'arrêt d'urgence.** Un fichier `EMERGENCY` distinct, que « resume » ne retire jamais, règle le problème.

**Constaté en production au premier déploiement :**
- **Le veilleur ne pouvait pas lire `/etc/astra/mt5-risk.json`.** Le dossier est en 0750 root:astra et `astra-mt5` n'est pas dans ce groupe. Le fichier passe dans `/etc/astra-risk/` (dossier 0755 root, fichier 0644 root), sans toucher aux droits de `/etc/astra`, qui contient des secrets.
- **Le terminal MT5 ne répondait pas au second processus** (`IPC timeout`). Le veilleur décide donc à partir des fonds propres que le pont enregistre toutes les 5 s, avec une fraîcheur de 180 s au plus. Il déclare l'urgence, et c'est le pont qui ferme les positions. Le veilleur ne ferme lui-même que s'il arrive à s'attacher au terminal.

## D-011 - 16-11 - LOT 2 : où le registre s'impose

**Choix :**
- `evidence.require_registration` garde chaque point d'entrée : backtests du site (tâche `backtest` du worker), commande `astra backtest` et tâche `learn`.
- La fonction de calcul `run_backtest` reste une fonction pure, appelée par le registre et par les tests unitaires.
- La boucle quotidienne de 120 variantes est arrêtée : `Worker.learning_enabled` vaut `False`, et une tâche `learn` déjà en file est refusée.

**Raison :** mettre le contrôle dans la fonction pure obligerait à faire passer un jeton par chaque test du moteur. Le garde sur les points d'entrée tient la même règle là où un backtest peut réellement être lancé.

**Écarté :** supprimer la boucle d'apprentissage, contraire à la règle XVI.5 (ne détruire aucun module qui fonctionne).

**Correction de test (commit séparé) :** le test E4 n'appelait pas `worker.initialize()`. Il manquait l'étape de préparation ; aucune assertion n'a changé.

## D-012 - 16-11 - Quatre tests encodaient l'ancienne règle (commit séparé)

**Constat :**
- `test_labweb` (deux tests) attendait qu'un backtest du site, sans fiche, se termine en DONE.
- `test_learning` (deux tests) attendait que le worker planifie la boucle d'apprentissage par défaut.

La section IV et le mandat ont remplacé ces deux règles.

**Correction :**
- Les tests du site attendent maintenant FAILED, c'est-à-dire le refus par le registre.
- Les tests du planificateur constatent que la boucle est arrêtée par défaut, puis l'activent explicitement pour tester le mécanisme, qui reste en place pour une réactivation par une hypothèse du registre.

Les autres assertions ne changent pas.
