# Exploitation de la production

Les adresses, comptes et chemins propres à l'hôte restent hors de ce dépôt public (voir `DEDICATED_SERVER_RUNBOOK.md`). Rien ici ne contient de secret.

## Services

| Service | Rôle |
|---|---|
| `astra-web` | tableau de bord et API (derrière Caddy) |
| `astra-worker` | acquisition BCE, backtests, simulations, apprentissage |
| `astra-agent` | agent de recherche par jeton, mode SIMULATION |
| `astra-mt5-bridge` | terminal MT5 et pont, compte DEMO uniquement (utilisateur `astra-mt5`, Wine) |
| `astra-demo` | exécution Binance Spot DEMO (inactif sans clé) |
| `astra-nft` | règles réseau de la boucle locale |

## Diagnostic

- État : `systemctl status <service>` ; journaux : `journalctl -u <service> -n 100`.
- Pont MT5 : `/var/lib/astra-mt5/bridge/journal-<jour>.jsonl` (un événement par ligne).
- Santé du site : `/login` répond 200 ; les autres applications du serveur (app, auth, chantier360) doivent répondre.

## Suspendre toute action automatique du pont

```bash
touch /var/lib/astra-mt5/bridge/STOP     # ni ouverture ni fermeture ; le flux et l'affichage continuent
rm /var/lib/astra-mt5/bridge/STOP        # reprise (après vérification du compte)
```

Arrêt d'urgence du moteur (bloque les nouvelles entrées, lu toutes les 15 s par le pont) : `astra controls kill --reason ... --operator ...` ; la levée est une action humaine séparée : `astra controls release`.

## Déploiement et retour arrière

1. Tests Windows (suite complète) puis tests Linux sur le serveur.
2. `deploy_lab.sh <commit> <empreinte>` installe la release dans `/opt/astra/releases/<commit>` et bascule les liens `app` et `venv`.
3. Retour arrière : repointer le lien `app` vers la release précédente (toujours conservée) et redémarrer `astra-web`, `astra-worker`.
4. Le pont MT5 lit son code dans le lien `app` : le redémarrer après un déploiement (`systemctl restart astra-mt5-bridge`).

## Secrets (jamais dans Git, le navigateur ni les journaux)

Déposés par l'utilisateur, saisie masquée : `astra-set-mt5-demo-password`, `astra-set-massive-key`, `astra-set-binance-demo-key`. Chaque fichier est en `0600` pour root et remis au seul service concerné par un identifiant systemd.

## Sauvegarde

Minuteur `astra-backup.timer` (quotidien). Restauration : à tester sur une copie avant toute dépendance (voir `ACCEPTANCE_MATRIX.md`).

## Test de restauration (sur une copie)

Dernier essai réussi le 2026-10-31. Ne jamais restaurer sur la base active pour un test.

1. Copier la dernière sauvegarde et son manifeste dans un dossier de travail privé.
2. `python -m astra backup verify <fichier>` : empreinte, intégrité et comptes de tables.
3. `ASTRA_DATABASE=<dossier>/restored.sqlite3 python -m astra backup restore <fichier> --operator <nom> --reason "<raison>"` (le fichier cible ne doit pas exister).
4. Comparer le nombre de lignes de chaque table : seule la ligne d'audit de la restauration doit différer.
5. Supprimer le dossier de travail.
