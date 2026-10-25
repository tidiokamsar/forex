# ASTRA, rapport du Lot 2 : fondations d'exploitation

Date : 25/10/2026. Branche : `feat/fondations-exploitation`, publiée. Destinataires : l'utilisateur et l'architecte principal (ChatGPT).

## A. Résumé exécutif

**Objectif.** Donner à ASTRA les fondations pour tourner en continu, indépendamment d'une session de développement :
- journaux structurés (F-01) ;
- sauvegarde et restauration vérifiées (F-04) ;
- résilience du connecteur cTrader (F-03) ;
- service de trading continu (F-02).

**Résultat.**
- Les quatre volets sont implémentés.
- Ils ont passé trois tours de revue indépendante, avec 11 défauts corrigés.
- 306 tests passent, 0 échec, 0 ignoré.
- Une quatrième revue, documentaire, a fait corriger 4 points de code, dont un défaut réel (voir B).
- La branche est publiée.

**Niveau réel.** Terminé et vérifié **en local seulement**. Le service ne tourne qu'avec le courtier simulé et un flux de cotations SYNTHÉTIQUE. La résilience cTrader n'est testée que contre le faux serveur.

Aucun superviseur externe n'est fourni : redémarrage automatique en cas de crash et démarrage au boot restent à faire.

**Difficultés.**
- La première revue a trouvé un défaut **grave** : une restauration ratée pouvait laisser le système **sans base de données**.
- Une de mes corrections (D3) a introduit une régression, détectée au tour suivant.

## B. Travail des agents

| Agent | Mission | Résultat vérifié |
|---|---|---|
| Coordinateur (moi) | Développement de F-01 à F-04, toutes les corrections, intégration, tests globaux | Suite complète relancée après chaque tour |
| QA indépendant, 3 tours, clones isolés | Revue en lecture seule, scripts de reproduction, relance des tests et de la CLI | Tour 1 : 7 défauts (1 élevé, 2 moyens). Tour 2 : 6 corrigés sur 7, plus 4 nouveaux (2 moyens). Tour 3 : tout corrigé, verdict « sûr à publier ». |
| Documentation | Documenter F-01 à F-04 dans 10 fichiers `.md`, sans Git | Relu par moi, aucune surdéclaration. Ses remarques ont fait corriger 4 points de code. |

Le développement lui-même n'a pas été parallélisé : il était terminé avant le protocole multi-agents.

## C. Preuves techniques

```bash
python -m ruff check src tests                        # All checks passed!
PYTHONPATH=src python -m unittest discover -s tests   # Ran 306 tests ... OK
python -m compileall -q src tests                     # code de sortie 0
node --check src/astra/static/app.js                  # code de sortie 0
```

**Tests du lot :** test_logs 6, test_backup 7, test_ctrader_resilience 13, test_service 8, test_foundations_fixes 4, test_foundations_qa 12, test_foundations_qa_round2 7.

**Défauts corrigés après la revue :**

| ID | Gravité | Défaut | Correction |
|---|---|---|---|
| D1 | Élevée | Si la copie échouait, la base active n'était pas restaurée. La restauration lisait aussi le chemin noté dans le manifeste au lieu du fichier vérifié. | Ordre sûr : copie de travail, vérification, gel des comptes, puis bascule ; retour arrière en cas d'échec. Refus tant qu'un service est vivant. |
| D2 | Moyenne | Comptes au-delà des 500 ordres récents non gelés | `account_refs()` sans limite |
| D3 | Moyenne | Erreur permanente retentée indéfiniment ; limite de débit traitée comme fatale | Marqueur `permanent` : mode dégradé immédiat. Marqueur `transient` pour les limites de débit. |
| D4 | Faible-moyenne | Mode dégradé armé une seule fois | Réarmement après levée humaine |
| D5 | Faible-moyenne | Curseur d'événements réutilisé par une nouvelle instance d'adaptateur | Curseur lié à l'epoch de l'instance |
| D6 | Faible | Masquage des secrets par nom de champ seulement | Masquage des valeurs révélées par `SecretValue` |
| D7 | Faible | `--keep 0`, gestionnaires de signaux non restaurés | Corrigé |
| NEW-1 | Moyenne | Régression de D3 : tout refus du fournisseur était ignoré | Seules les limites de débit sont tolérées |
| NEW-2 | Moyenne | Impossible de restaurer par-dessus une base corrompue | Audit tolérant ; la base corrompue est conservée |
| NEW-3 | Faible-moyenne | Le masquage cassait le JSON avec des valeurs courtes | Masquage avant sérialisation, au moins 8 caractères |
| NEW-4 | Faible | Battement de cœur restauré pris pour un service actif | Statut `stopped_by_restore` |

**Correction issue de la revue documentaire :** le curseur d'événements courtier était global, alors qu'il doit être propre à chaque compte. Un test vérifie maintenant qu'un compte ne masque plus les événements d'un autre.

**Nature des tests :**
- simulés : `SimulatedBroker` et flux SYNTHÉTIQUE ;
- locaux hors ligne : faux serveur cTrader et serveur WebSocket en boucle locale ;
- **service réel : aucun.**

**Risques résiduels faibles, documentés :**
- un secret utilisé comme nom de champ n'est pas masqué ;
- un champ nommé seulement `refresh` n'est plus masqué par son nom ;
- une base sans ordres n'est gelée par rien après restauration (la réconciliation au démarrage du service le rattrape) ;
- avec des ticks de plus de 120 s, un service actif semble arrêté.

## D. Avancement du cahier des charges

| Exigence | Avant | Après |
|---|---|---|
| Section 13, fonctionnement continu | Absent | Partiel : service supervisable, état persistant, reprise après crash sans doublon, réconciliation. Le superviseur externe manque. |
| Section 12.1, fiabilité | — | Sauvegarde et restauration vérifiées, journaux structurés |
| RSK-07, mode dégradé | — | Renforcé : gel `service_degraded` |
| Section 5.10, sauvegardes chiffrées | Absent | Toujours non satisfait : les sauvegardes ne sont pas chiffrées |
| BRK-04, réconciliation après reconnexion | — | Renforcé côté service ; toujours non prouvé contre un vrai courtier |

## E. Blocages et interventions humaines

| Blocage | Action |
|---|---|
| Contrôle cTrader réel | Votre `ctrader-check`, déjà prévu de votre côté |
| Stockage sécurisé des jetons OAuth | Choisir un mécanisme, par exemple le Gestionnaire d'identification Windows. Le renouvellement du jeton reste désactivé d'ici là. |
| Hébergement permanent et supervision | Décider plus tard : PC allumé en permanence ou serveur, Planificateur de tâches, NSSM ou systemd |

## F. Recommandation technique

**Priorités.**
1. Brancher le service sur l'adaptateur cTrader en lecture seule, après votre contrôle.
2. Superviseur externe et alertes.
3. Chiffrement des sauvegardes.

**Risques.** Le service n'a jamais tourné contre un vrai flux.

## G. État Git et publication

- **Branche** `feat/fondations-exploitation`, publiée sur `8918ee6` (ce rapport est le commit suivant). Elle est basée sur `fix/lot1-securite`.
- `main` est inchangée.
- **Secrets** : aucun ; analyse avant l'envoi, confirmée par la QA indépendante.
- **Exécution** : aucun ordre envoyé, DÉMO non activée, RÉEL verrouillé.
