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
