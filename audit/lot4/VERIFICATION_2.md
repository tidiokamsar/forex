# LOT 4 - Vérification indépendante n° 2 (après corrections)

Vérificateur : n'a pas écrit le code. Base : `HEAD` = `f31d65c`, diff `84af020..HEAD`, tests locaux, `audit/lot4/preuves/production.txt`.

## Ordre des commits
`92b633b` (tests `tests/test_lot4_fixes.py`, f1..f5) et `5576f28` (lint, aucune assertion changée) précèdent `f31d65c` (corrections) : CONFORME.
Local : `test_lot4_fixes test_lot4_risk test_mt5_risk test_mt5_bridge` : 80 tests, OK. Serveur : `Ran 1393 tests ... OK` (production.txt, rapporté).

## Défauts de la vérification 1
| Défaut | Statut | Preuve |
|---|---|---|
| 1 Ordre CLI hors passerelle (bloquant) | CONFORME | `cli.py:531-533` renvoie `refused` et `return 2` avant tout `trader.open` ; test f1. Seul appel restant : `mt5_bridge.py:381`, après `evaluate_entry` (`:370`) ; `order_send` uniquement dans `brokers/mt5_demo_orders.py:202` (couche courtier) |
| 2 Équité NaN sans arrêt | CONFORME | `mt5_emergency.py:44-52` `_finite` + `> 0` ; test f2 |
| 3 Veilleur tué par une erreur quelconque | CONFORME | `mt5_emergency.py` `watch_once` `except Exception`, journalisé ; test f3 |
| 4 « resume » du site lève l'arrêt d'urgence | CONFORME | `mt5_bridge.py:450-452` refuse si `EMERGENCY` ; `:462` refuse tout ordre ; `:223` bloque ; test f4 |

## Porte et limites PRESET
| Élément | Statut | Preuve |
|---|---|---|
| Ordres non conformes refusés | CONFORME | chemin unique `_enter` -> `evaluate_entry` (`mt5_bridge.py:370-381`) ; tests r1..r7 |
| Perte au stop 0,1 % ; nette/devise <= 1x ; levier <= 3x ; cotation 5 s ; volume min hors budget ; pas de hausse après perte ; mois -4 % = /2 | CONFORME (inchangé depuis vérif. 1) | `mt5_risk.py` (voir VERIFICATION.md) ; tests r2..r7 passent |
| -12 % sous le sommet : tout fermer | CONFORME en code ; NON VÉRIFIABLE en production avec positions | `trigger`, `run`, fermeture par le pont `mt5_bridge.py:505-509` ; tests r8, r9, f4, f5. Prod : `"aucune position ASTRA, aucun ordre"` : aucune fermeture réelle exercée |
| Limites lues hors dépôt | CONFORME | `/etc/astra-risk/mt5-risk.json` (empreinte `e5f5635f...`, root, 230 o) ; unité `ConditionPathExists` |
| Arrêt d'urgence = processus séparé qui bloque la passerelle | CONFORME | unité `astra-mt5-emergency.service` (`--watch`, active) ; prod : fichier `EMERGENCY` à 23:42:27 « depuis un processus separe », pont `halted` à 23:44:49, `STOP` présent |

## Défauts restants (non bloquants)
1. En production le veilleur n'atteint pas le terminal (`IPC timeout -10005` à chaque tour) : il décide sur l'équité enregistrée par le pont (<= 180 s). Si le pont est arrêté, aucune fermeture (repli : stops côté courtier, journal). Dépendance à documenter et surveiller.
2. La commande qui a posé l'EMERGENCY en production n'est pas capturée dans la preuve (seul son effet l'est) ; fermeture effective de positions jamais exercée en réel.
3. `cli.py:534+` : code mort après `return 2`, à supprimer au LOT 5.
4. Le texte d'en-tête de l'unité parle encore seulement du fichier STOP (pas d'EMERGENCY) : documentation.

## Verdict
**PORTE FRANCHIE** (en code et en blocage de passerelle en production). Réserve : exercer en DEMO une fermeture réelle d'au moins une position ASTRA par l'arrêt d'urgence et capturer la commande lancée.
