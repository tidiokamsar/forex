# LOT 4 - Vérification indépendante (risque et arrêt d'urgence)

Branche `lot4-risque`. Vérificateur : n'a pas écrit le code. Preuves : lecture du code, historique git, tests locaux.

## Ordre des commits et tests
- `cce051d` (tests d'acceptation) précède `51781c4` (deux tests corrigés) puis `6b01758` (implémentation) : CONFORME.
- Correction `51781c4` (D-006) : légitime. R3 restait vérifié sur une croisée EURGBP sans taux (refus maintenu) ; l'ordre EURUSD porte bien son propre taux EUR. Le test `test_mt5_risk` reçoit des taux, l'objet testé (plafond d'écart) est inchangé. Aucun affaiblissement d'assertion.
- Local : `test_lot4_risk test_mt5_risk test_mt5_bridge` : 75 tests, OK. Serveur (1388 tests OK) : rapporté, non vu.

## Porte et limites PRESET
| Élément | Statut | Preuve |
|---|---|---|
| Limites lues hors dépôt, sans fichier = refus | CONFORME | `mt5_risk.py:113` `load_required_limits` ; `mt5_bridge.py` `run_from_env` ; unit `ASTRA_MT5_RISK_FILE=/etc/astra/mt5-risk.json` ; test r1, r11 |
| Perte au stop 0,1 % | CONFORME | `mt5_risk.py:380` ; `deploy/risk/mt5-risk.json` ; test r2 |
| Exposition nette par devise <= 1x | CONFORME | `mt5_risk.py:326-365` ; test r3 (devise non mesurable refusée) |
| Levier brut <= 3x | CONFORME | `mt5_risk.py:362` ; test r4 |
| Mois à -4 % : tailles divisées par 2 | CONFORME (dimensionnement seulement) | `mt5_risk.py:287` `size_scale`, appliqué `mt5_bridge.py` `_enter` ; test r5. La passerelle ne revérifie pas la moitié (elle borne à 0,1 % x 1,05). |
| -12 % sous le sommet : tout fermer | CONFORME en code | `mt5_emergency.py:39` `trigger`, `:55` `run` ; test r8, r9 |
| Cotation <= 5 s | CONFORME | `mt5_risk.py:394` ; test r6 |
| Volume minimum au-delà du budget refusé | CONFORME | `taille_de_position` sur la perte réelle au volume arrondi ; test r7 |
| Pas d'augmentation après perte | CONFORME | `size_scale` ne dépasse jamais 1,0 ; mois inconnu = 0,5 |
| Ordres non conformes refusés | NON CONFORME (voir défaut 1) | `mt5_bridge.py` `_enter` appelle `evaluate_entry` pour auto et site ; mais `cli.py:534` |
| Arrêt d'urgence = processus séparé qui ferme tout et bloque la passerelle | CONFORME en code, NON VÉRIFIABLE en production | `deploy/systemd/astra-mt5-emergency.service` (service propre, `--watch`) ; STOP posé avant fermeture ; test r10 appelle `main()` dans le même processus Python, pas un processus séparé |

## Défauts constatés
1. **Bloquant.** `src/astra/cli.py:523-537` `_mt5_test_order` envoie un ordre réel au courtier (`trader.open`) sans `evaluate_entry` ni contrôle du fichier STOP. Un ordre peut donc atteindre le broker hors passerelle et pendant un arrêt d'urgence. À supprimer ou faire passer par la passerelle et le STOP.
2. `mt5_emergency.py:42` `trigger` : une équité NaN passe `not account.equity` (NaN est vrai) et la comparaison NaN donne `None` : pas d'arrêt, silencieux. Utiliser `_ok()` comme `mt5_risk`.
3. Boucle `--watch` (`mt5_emergency.py:114-132`) : seules les `Mt5DemoError` sont capturées ; une `KeyError` (`equity` absent) ou `OSError` sur STOP fait tomber le processus. Atténué par `Restart=always` et `OnFailure=astra-alert@`, mais après 5 échecs en 15 min le service reste arrêté.
4. Une requête site `resume` (`mt5_bridge.py` `_handle_request`) retire le STOP posé par l'arrêt d'urgence si le serveur l'autorise, contrairement au texte « seule une personne le retire » (c'est un opérateur, mais sans distinguer un STOP d'urgence d'une pause). Les entrées restent refusées par `drawdown` (10 %) tant que la perte dure.
5. Aucun chemin NaN/None trouvé dans `evaluate_entry` permettant une entrée : chaque valeur manquante échoue sa vérification (`_ok`).

## DECISIONS D-002..D-008
Cohérentes avec le code lu (D-003 PRESET, D-005 fichier hors dépôt avec empreinte, D-006 tests, D-008 service séparé). D-007 : déploiement différé au 21-11, donc aucune preuve production LOT 4 à ce jour.

## Verdict
**PORTE NON FRANCHIE.** Bloquant : défaut 1 (ordre CLI hors passerelle et hors STOP). Après correction, le verdict attendu est PORTE FRANCHIE EN CODE, PREUVE PRODUCTION EN ATTENTE (arrêt d'urgence à exercer depuis le service séparé sur le serveur, D-007). Défauts 2 et 3 à corriger dans le même passage.
