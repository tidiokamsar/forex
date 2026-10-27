# ASTRA, rapport de mise en ligne : astra.infranetgn.com

**Destinataire : l'architecte principal (ChatGPT).** Copie : l'utilisateur. Date : 27/10/2026, 21:20 UTC.

**Statut : en ligne en HTTPS, mode SIMULATION, exécution réelle désactivée.** La mise en ligne a été faite à la demande explicite de l'utilisateur, sur un serveur qu'il a désigné. Elle ne vaut pas autorisation d'envoyer des ordres réels. Le serveur `102.211.199.132` (GLPI, compromis) n'est pas utilisé.

| Repère | Valeur |
|---|---|
| URL publique | `https://astra.infranetgn.com` |
| Serveur | `147.79.114.208` (`srv1963934`), Ubuntu 26.04.1 LTS, noyau 7.0 |
| Version installée | `23f811c4cc7491189290c958b6de6a925a2c2bb3` (branche `feat/auth-deploy`, identique sur GitHub) |
| Version testée sur l'hôte | `61eb1ee` ; `23f811c` n'en diffère que par 2 lignes de `deploy/gunicorn.conf.py` (socket de contrôle de gunicorn désactivé) |
| `main` | `60991ed`, inchangée ; aucune fusion |

## 1. DNS

- Enregistrement créé dans hPanel (Hostinger), zone `infranetgn.com` : `A astra → 147.79.114.208`, TTL 3600. Aucun autre enregistrement modifié.
- Résolution vérifiée sur le serveur faisant autorité (`lunar.dns-parking.com`), sur Google (8.8.8.8) et sur Cloudflare (1.1.1.1) : `147.79.114.208`.
- Aucun AAAA, aucun CNAME, aucun CAA (sur le nom ni sur `infranetgn.com`) : Let's Encrypt peut émettre.

## 2. Serveur et architecture

- **Hôte partagé**, pas dédié : il sert déjà `app.`, `auth.` et `chantier360.infranetgn.com`, ainsi que `routes.ageroute.gov.gn` (Caddy), plusieurs piles Docker liées à 127.0.0.1 et Ollama.
- Pare-feu ufw actif : seuls 22, 80 et 443 sont ouverts. SSH par clé seulement (`PasswordAuthentication no`, root `prohibit-password`).
- Chaîne : navigateur → Caddy 2.11.4 (TLS, port 443) → `http://127.0.0.1:8765` (gunicorn 26.2.0, `astra-web.service`).
- Python 3.14.4 (paquet Ubuntu ; `python3.14-venv` installé depuis le dépôt officiel). Dépendances installées avec `--require-hashes` : Flask 3.1.3, Werkzeug 3.1.9, gunicorn 26.2.0, argon2-cffi 25.1.0, pyotp 2.10.0, cryptography 50.0.2.
- Code dans `/opt/astra/releases/<sha>` (root, lecture seule), liens `/opt/astra/app` et `/opt/astra/venv`. Données dans `/var/lib/astra` (astra:astra 0700, fichiers 0600).
- **Adaptations à cet hôte :**
  - le mandataire est Caddy (utilisateur `caddy`) et non Apache ou nginx ;
  - la règle nftables (`/etc/astra/astra-loopback.nft`) autorise `caddy` et `astra` au lieu de `www-data` et `astra`, via une surcharge de `astra-nft.service` ;
  - les fichiers du dépôt sont inchangés.

## 3. Secrets et clés (créés sur l'hôte, jamais transmis)

| Fichier | Propriétaire et mode |
|---|---|
| `/etc/astra/mfa.key` (trousseau MFA, clé active `k20261027-d28f38`) | root:astra 0640 |
| `/etc/astra/proxy.secret` | root:astra 0640 |
| `/etc/caddy/astra-proxy-secret.caddy` (même valeur, côté Caddy) | root:caddy 0640 |
| `/etc/astra/web.env` (mode public, aucun secret) | root:astra 0640 |
| `/var/lib/astra/web_secret.key` | astra:astra 0600 |

Aucune valeur de secret n'apparaît dans le dépôt, les journaux, ce rapport ou la conversation.

## 4. Certificat et renouvellement

- Émis par Let's Encrypt (`CN=YE2`) pour `CN=astra.infranetgn.com`, SAN `DNS:astra.infranetgn.com`. Validation TLS du client : `ssl_verify_result=0`.
- **Expire le 25/01/2027 à 20:18:24 UTC.**
- Renouvellement : automatique par Caddy (gestion ACME intégrée, la même que pour les autres sites de l'hôte). Caddy n'offre pas de « dry-run ». La surveillance est assurée par `astra-cert-check.timer` (actif), dont la première exécution indique 2159 h restantes, pour un seuil d'alerte de 336 h.
- Alerte : `astra-alert@` écrit une entrée CRITIQUE dans le journal. **Aucun destinataire de courriel n'est configuré** (`ASTRA_ALERT_TO` vide) : à définir.

## 5. Tests réellement effectués (depuis l'extérieur, sauf indication)

| Test | Résultat |
|---|---|
| Suite complète sur l'hôte, utilisateur `astra`, `61eb1ee` | **1030 tests OK, aucun ignoré, 15 échecs attendus (Lot 4), code 0** ; `check_unittest_log` code 0 |
| Résolution DNS publique | `147.79.114.208` |
| Certificat valide pour le nom | oui, `ssl_verify_result=0` |
| HTTP vers HTTPS | `308` vers `https://astra.infranetgn.com/` |
| `/api/health` en HTTPS | `200 {"status":"ok"}` |
| Les 24 routes de l'application, sans session | pages : `303` vers `/login` ; API : `401` ; POST sans jeton CSRF : `403` ; `/login` : `200` |
| En-têtes forgés (`X-Astra-Proxy`, `X-Forwarded-For`, `X-Forwarded-Proto`, `Forwarded`) | sans effet (`303` vers la connexion) |
| En-têtes de sécurité | HSTS 1 an, CSP stricte, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` ; cookie `__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict` |
| Port 8765 depuis Internet | fermé |
| Port 8765 en local, comme `nobody` et comme `root` | refusé par nftables (seuls `caddy` et `astra` passent) |
| Trafic sortant du service | refusé (`IPAddressDeny=any`) |
| `systemd-analyze security astra-web` | exposition 1.1 (« OK ») ; aucun SIGSYS |
| Redémarrage contrôlé (`systemctl restart astra-nft astra-web`) | service actif, HTTPS `200` aussitôt après |
| Services activés au démarrage | `astra-web`, `astra-nft`, `astra-cert-check.timer` |
| Non-régression des autres sites (avant et après) | `app.` 200, `auth.` 302, `chantier360.` 200, `routes.ageroute.gov.gn` 200, certificats valides : identiques |

**Non effectués :**
- **Connexion, TOTP et changement de mot de passe sur le serveur :** aucun compte n'existe encore. Le premier administrateur doit être créé par l'utilisateur lui-même (section 7), pour que son mot de passe ne passe pas par la conversation. Ces parcours sont couverts par la suite de tests (hôte et Linux) et par le test de bout en bout local (22/22 sur `0bc20b0`).
- **Redémarrage complet de l'hôte :** il interromprait les autres applications de production. Il reste à planifier avec le propriétaire du serveur.

## 6. Modification de Caddy (réversible)

- Sauvegarde : `/etc/caddy/Caddyfile.avant-astra-20261027T211652Z`.
- Ajout d'un seul bloc `astra.infranetgn.com`. Dans ce bloc, la taille des corps est limitée à 64 Ko, les en-têtes de transfert (`X-Forwarded-For`, `X-Forwarded-Proto`) sont imposés par Caddy, les autres en-têtes sont retirés, et le secret de proxy est importé d'un fichier root:caddy 0640.
- Configuration validée par `caddy validate`, puis rechargement de Caddy sans interruption.
- Retour arrière : restaurer la sauvegarde, `systemctl reload caddy`, `systemctl disable --now astra-web astra-nft`.

## 7. Actions restantes

1. **Premier administrateur**, à créer par l'utilisateur dans son propre terminal (mot de passe saisi deux fois, jamais affiché ici), puis enrôlement TOTP et conservation hors ligne des 10 codes de récupération à la première connexion :
   ```
   ssh -t -i ~/.ssh/infranet_vps root@147.79.114.208 "systemd-run --pty --wait -q -p User=astra -p EnvironmentFile=/etc/astra/web.env -p StateDirectory=astra -p StateDirectoryMode=0700 -p UMask=0077 -p Environment=PYTHONPATH=/opt/astra/app/src /opt/astra/venv/bin/python -m astra auth init-admin --username <nom>"
   ```
2. **Sauvegardes :**
   - aucune tâche planifiée n'existe encore pour `astra backup create` et `auth.sqlite3` ;
   - le trousseau `/etc/astra/mfa.key` doit être copié hors de l'hôte, **séparément** des bases.
3. **Alertes :** définir `ASTRA_ALERT_TO` dans `/etc/astra/alert.env` (le serveur dispose de `sendmail`) et tester l'envoi.
4. **Redémarrage de l'hôte :** à faire lors d'une fenêtre convenue avec le propriétaire, puis revérifier.
5. **Risques résiduels propres à un hôte partagé :**
   - l'API d'administration de Caddy (`127.0.0.1:2019`, sans authentification, comme sur toute installation Caddy par défaut) permet à un utilisateur local de lire la configuration, y compris le secret de proxy. Le filtre nftables du port 8765 reste la barrière principale ;
   - root sur cet hôte est partagé avec les autres projets ; fail2ban est inactif (SSH par clé seulement).
6. **CI GitHub :** toujours bloquée par le verrou de facturation ; aucun contrôle CI n'est présenté comme réussi.

Arrêt au point de contrôle.
