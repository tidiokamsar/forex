# ASTRA, rapport de la Gate Auth & Deployment

**Destinataire : l'architecte principal (ChatGPT).** Copie : l'utilisateur. Date : 27/10/2026. Branche : `feat/auth-deploy`, créée depuis `feat/multi-venue` (`a2b96c8`).

**Statut : lot NON terminé.**
- **Fait :** l'authentification, les sessions, la MFA, les rôles et la protection CSRF existent et sont vérifiés hors ligne, ainsi que sur un vrai socket local.
- **Bloqué :** la publication sur `102.211.199.132`. Cinq raisons :
  1. l'utilisateur a autorisé une inspection en lecture seule seulement ;
  2. `sudo` exige un mot de passe ;
  3. ni l'adresse IP ni les ports 80 et 443 ne sont disponibles ;
  4. aucun nom DNS n'existe pour ASTRA ;
  5. l'intrusion n'est que partiellement remédiée.
- **Conséquence :** aucune adresse d'accès distante n'a été vérifiée, car **aucun déploiement n'a eu lieu**.

| Repère | Valeur |
|---|---|
| SHA du code vérifié | `d7384604601dab43f4b24e70320eddc250d0e7cc` |
| SHA déployé | aucun (aucun déploiement) |
| `main` | `60991ed`, inchangée ; aucune fusion |
| Mode de trading | SIMULATION ; DÉMO et RÉEL fermés ; futures et options désactivés ; verrous d'exécution non modifiés |

## 1. État initial (audit)

**Code** (`feat/multi-venue` à `a2b96c8`) :
- `src/astra/web.py` reposait sur `http.server` de la bibliothèque standard, lié à l'adresse locale, en GET seulement.
- **Aucune authentification.** Toutes les routes étaient anonymes :
  - fichiers statiques : `/`, `/app.js`, `/styles.css` ;
  - `/api/health` (mode et verrou) ;
  - `/api/summary`, `/api/runs`, `/api/audit` ;
  - `/api/orders`, `/api/controls`, `/api/reconciliations`, `/api/models`, `/api/experiments`.
- Pas de flux temps réel. Les autres méthodes HTTP répondaient 405.
- `PermissionGuard` et `Role` gouvernaient l'exécution, pas l'accès HTTP.
- La documentation interdisait toute exposition du tableau de bord.

**Serveur `102.211.199.132`, inspecté en lecture seule (aucune modification)** :

| Élément | Constat |
|---|---|
| Accès | SSH par clé, compte `agerdb` (groupes sudo, adm, lxd). `sudo` exige un mot de passe, qui n'a pas été demandé. |
| Système | Ubuntu 24.04.4 LTS, noyau 6.8.0-147, systemd 255, cgroup v2 ; NTP synchronisé |
| Python | 3.12.3 seulement. ASTRA est validé sur 3.14. |
| Ports à l'écoute | 22, 25, 587, 993, 80 et 443 (Apache), 21115 à 21119 (RustDesk), **62354 sur 0.0.0.0, propriétaire non identifiable sans root** ; 3306 (MariaDB) et 143 en local |
| Services | Apache 2.4.58 (GLPI), MariaDB, Postfix, Dovecot, FreeRADIUS, Docker et containerd, LXD, glpi-agent, entre autres |
| Web et certificats | Hôtes virtuels GLPI sur *:80 et *:443 : `ServerName 102.211.199.132` et `glpi.ageroute.gov.gn` (certificat Let's Encrypt), alias `support.ageroute.gov.gn`. certbot 2.9.0, minuteur actif. |
| DNS | Aucun nom pour ASTRA ; pas de résolution inverse de l'adresse |
| Disque et mémoire | 59 Go libres (38 % utilisés), 13 Go de RAM disponibles |
| Pare-feu | Illisible sans root |
| ASTRA déjà présent | Non (ni `/opt/astra`, ni `/var/lib/astra`, ni utilisateur `astra`) |
| Sauvegardes | Exports SQL de GLPI dans le répertoire personnel (non ouverts) ; politique de sauvegarde du serveur inconnue |

## 2. Conception retenue

- **Choix de la pile.** Python déconseille `http.server` en production. La couche HTTP passe donc à **Flask 3.1.3 / Werkzeug 3.1.9**, servis par **gunicorn 26.2.0**.
  - Le code de trading, de risque et de recherche n'est pas modifié et reste en bibliothèque standard.
  - Seuls `astra.webapp` et `astra.auth` importent des paquets tiers, et la CLI les charge à la demande.
- **Mots de passe :** Argon2id avec **argon2-cffi 25.1.0** (profil RFC 9106 faible mémoire : 64 Mio, 3 itérations, parallélisme 4).
- **MFA :** TOTP avec **pyotp 2.10.0**.
- **Dépendances :** 13 paquets épinglés avec toutes les empreintes PyPI (`requirements/web.txt`, `requirements/server.txt`), justifiés dans `docs/DEPENDENCIES.md` et installés par la CI. Chaque version a au moins deux semaines et aucune vulnérabilité connue.
- **Exclus :** pas de fournisseur d'identité externe, pas de service payant, pas de chaîne d'outils JavaScript.

## 3. Comptes, sessions, rôles

**Premier compte.** La création se fait par commande locale sur le serveur, sous l'utilisateur `astra` :
- `python -m astra auth init-admin --username <nom>` : mot de passe saisi deux fois ;
- ou avec `--generate` : mot de passe affiché une seule fois sur la sortie d'erreur, à changer à la première connexion, qui expire après 72 h.

La commande est refusée si un compte existe déjà. Il n'y a ni compte par défaut, ni inscription publique, et aucun mot de passe n'est conservé en clair.

**Sessions :**
- Jeton opaque de 256 bits ; seule son empreinte SHA-256 est stockée.
- Cookie `__Host-astra_session` : Secure, HttpOnly, SameSite=Strict, Path=/, sans Domain.
- Expiration : 15 min d'inactivité, 8 h au plus, 5 min pour les étapes intermédiaires (MFA, enrôlement, changement imposé).
- Nouveau jeton à chaque connexion, à chaque étape et à chaque changement de privilège ; l'ancien est révoqué.
- Révocation à la déconnexion, au changement ou à la réinitialisation du mot de passe, à la réinitialisation de la MFA, au changement de rôle et à la désactivation du compte.
- 5 sessions au plus par compte. Les sessions survivent au redémarrage ; le nettoyage passe par `astra auth cleanup`.
- Une panne du stockage répond 503, jamais un accès anonyme.

**MFA :**
- Obligatoire pour ADMIN et OPERATOR avant toute donnée.
- Code TOTP accepté une seule fois.
- 10 codes de récupération à usage unique, affichés une fois.
- Récupération : `astra auth reset-mfa` et `reset-password` en local sur le serveur. Aucune API publique de réinitialisation.

**Limitation des tentatives :**
- Clés de comptage : adresse, couple (compte, adresse), et compte entier pour les navigateurs inconnus.
- Délai progressif à partir du 5e échec, plafonné à 15 min ; décroissance après une heure.
- Un navigateur déjà connecté porte un « cookie d'appareil » signé (méthode OWASP), qui empêche un attaquant de bloquer l'utilisateur légitime.
- Le changement de mot de passe et la ré-authentification administrateur sont limités de la même façon.

**Rôles, appliqués par le serveur sur chaque route :**

| Rôle | Accès |
|---|---|
| READ_ONLY | session, statut, résumé, exécutions, modèles, expériences |
| OPERATOR | + ordres, contrôles, rapprochements, audit |
| ADMIN | + comptes et journal d'authentification ; chaque action exige à nouveau le mot de passe de l'administrateur ; le dernier administrateur actif est protégé |

- **Séparation du trading :** les rôles web ne sont pas des rôles d'exécution. Aucune route ne modifie l'état de trading (test sur la table des routes, test sur l'AST, et 405 sur les routes de trading). `astra.auth` n'importe rien de l'exécution, des courtiers, du risque ou de la gouvernance.
- **Requêtes :**
  - liste exacte des valeurs `Host` acceptées : le nom public en mode public, les noms locaux en mode tunnel ;
  - en mode public, l'en-tête secret `X-Astra-Proxy` est exigé sur chaque requête (comparaison à temps constant, 403 sinon). Le proxy l'injecte depuis un fichier lisible par root seulement, si bien qu'un autre processus local, comme le PHP de GLPI exécuté en `www-data`, ne peut pas se faire passer pour le proxy ;
  - `Origin` vérifié et jeton CSRF sur toute modification ;
  - corps limité à 64 Kio ;
  - en-têtes CSP, nosniff, DENY, no-referrer, no-store, COOP et CORP, plus HSTS en HTTPS ;
  - `X-Forwarded-For` lu sur un seul saut de proxy de confiance (obligatoire en mode public).
- **Audit :** `auth_events` en ajout seul. Il enregistre l'acteur, l'action, la cible, le résultat, l'heure et l'adresse, sans aucun secret.
- **Écrans et données :**
  - les pages existantes sont conservées, avec une barre de compte (déconnexion, mot de passe, comptes) ;
  - chaque bloc est étiqueté SIMULATION ou SYNTHÉTIQUE, aucun flux réel n'étant intégré ;
  - aucune clé de courtier dans le navigateur.

## 4. Déploiement préparé (non installé)

`deploy/` contient les éléments suivants :
- `gunicorn.conf.py` : 127.0.0.1:8765, `preload_app`, seul `X-Forwarded-Proto` accepté, journal sans cookie ni paramètres d'URL ;
- `systemd/astra-web.service` : utilisateur non privilégié, `StateDirectory` en 0700, bac à sable systemd complet, réseau limité au local, `Restart=on-failure`, alerte `OnFailure` ;
- `systemd/astra-alert@.service` : journal en CRITICAL, plus un courriel si un MTA est configuré ;
- `systemd/astra-cert-check.{service,timer}` : contrôle de l'expiration du certificat toutes les 6 h (`tools/ops/check_certificate.py`, testé hors ligne) ;
- `apache/astra.conf.example` : pour un hôte où Apache détient déjà 80 et 443 ; nom DNS dédié obligatoire, en-têtes de transfert du client supprimés ;
- `nginx/astra.conf.example` : bloc fourre-tout par défaut, limites sur chaque étape d'authentification, TLS « intermediate » ;
- `nftables/astra-loopback.nft` et `systemd/astra-nft.service` : seul l'utilisateur du proxy peut joindre le port 8765. La règle est idempotente, chargée avant le tableau de bord (qui ne démarre pas sans elle) et reconstruite après un redémarrage ou un rechargement de nftables ;
- `apache/astra-proxy-secret.conf.example` : génération du secret de proxy sous umask 077, droits 0640 côté application et 0600 côté Apache ;
- `astra-web.env.example` (mode privé par défaut) et `astra-alert.env.example`.

La procédure, la liste de contrôle avant mise en ligne, le comportement face aux incidents (redémarrage, arrêt inattendu, panne de base, certificat expiré, disque plein, migration ratée, restauration) ainsi que la mise à jour, la sauvegarde et le retour arrière sont dans DEPLOYMENT.md, section « Remote dashboard ».

**Certificat.** Le choix privilégié est un certificat pour un **nom DNS dédié** (par exemple `astra.ageroute.gov.gn`). Un certificat pour l'adresse IP est écarté :
- l'adresse sert déjà GLPI ;
- un certificat d'adresse IP est de courte durée (profil `shortlived`, environ 6 jours) et ne se valide que par HTTP-01 ou TLS-ALPN-01, ce qui exigerait un client ACME récent (certbot 2.9.0 sur l'hôte : prise en charge non vérifiée).

Aucun certificat auto-signé n'est proposé comme équivalent.

## 5. Tests et contrôles (codes de sortie réels)

| Commande | Code | Résultat |
|---|---|---|
| `python -m ruff check src tests tools` | 0 | All checks passed! |
| `python -m compileall -q src tests tools deploy` | 0 | |
| `node --check src/astra/static/app.js` | 0 | |
| `tools/ci/secret_scan.py` (arbre et historique) | 0 | no secret found (arbre et 118 commits) |
| Tests ciblés (7 modules multi-plateformes et `test_webapp`) | 0 | 321 tests, OK |
| Suite complète `unittest discover -v` | 0 | 994 tests, OK (expected failures=15) |
| `check_unittest_log.py` (15 échecs attendus, tous du Lot 4) | 0 | 15 échecs attendus : test_lot4_adversarial 1, test_lot4_security_review 7, test_lot4_verification 7 |
| Bout en bout sur un vrai socket local (`astra serve`, 22 contrôles), sur `d738460` | 0 | 22/22 |

Exécution locale du 27/10/2026, de 10:48:35 à 11:08:15 UTC, sur `d738460` avec un arbre propre : Windows, Python 3.14.6, Ruff 0.16.1, Node v24.18.1, et les paquets épinglés de `requirements/web.txt` et `server.txt`.

Exécutions non retenues :
- l'exécution sur `cc3550b` a été interrompue par la déconnexion du disque F: (code 1, puis 127) ;
- une première exécution sur `d738460` a été coupée avant d'enregistrer son code de sortie, alors que son journal indiquait `OK`.

La CI GitHub n'a toujours pas tourné (compte verrouillé pour facturation). Ces résultats sont locaux, pas une preuve CI.

**Contenu de `tests/test_webapp.py` (61 tests) :**
- **Authentification :** pas de compte par défaut ; premier administrateur unique ; stockage Argon2id ; mots de passe faibles refusés ; connexion correcte et incorrecte ; compte désactivé ; limitation progressive ; attaque depuis des adresses tournantes ; mot de passe temporaire expiré ; MFA (enrôlement, code faux, rejeu, code de récupération) ; changement de mot de passe et sa limitation.
- **Sessions et cookies d'appareil :** cookie d'appareil invalidé par la réinitialisation du mot de passe, cookies malformés ignorés sans erreur, verrouillage impossible d'un appareil connu, ré-authentification et changement de mot de passe non bloquables par des échecs anonymes ; jeton valide, faux ou malformé ; expiration d'inactivité et absolue ; déconnexion ; renouvellement et ancien jeton refusé ; fixation de session ; redémarrage ; panne du stockage ; désactivation ; changement de rôle ; plafond de sessions ; nettoyage.
- **Web et permissions :** secret de proxy absent ou faux refusé ; accès anonyme à chaque route ; rôle insuffisant ; CSRF ; Origin ; Host forgé ; X-Forwarded-For en mode privé et en mode proxy ; fichiers statiques et chemins ; en-têtes ; taille de requête ; secrets absents des réponses et de l'audit ; injections.
- **Séparation du trading :** aucune route ne modifie l'état de trading ; le mode reste SIMULATION.
- **Bout en bout, hors suite (`scratchpad`, lancé localement) :**
  - création de l'administrateur par la CLI ;
  - anonyme refusé, Host forgé refusé, Origin étranger refusé ;
  - cookies `__Host-` ;
  - mauvais mot de passe ;
  - MFA obligatoire avant toute donnée ;
  - enrôlement avec 10 codes de récupération ;
  - changement imposé du mot de passe ;
  - lectures ADMIN ;
  - aucune mutation de trading (405) ;
  - déconnexion, puis ancien cookie refusé ;
  - en-têtes de sécurité.
- **Navigateur :** une vérification visuelle dans le navigateur intégré, sur l'adresse locale, a montré la redirection vers `/login`, les badges SIMULATION et RÉEL VERROUILLÉ, et le CSS chargé sous CSP.

**Tests d'infrastructure non réalisés** (aucun hôte installé) : validité du certificat dans un navigateur, renouvellement, redirection HTTPS, ports exposés, backend inaccessible de l'extérieur, permissions sur l'hôte, exercices de démarrage, de redémarrage et de restauration. La CI GitHub ne tourne toujours pas (compte verrouillé pour facturation).

## 6. Revues indépendantes

| Revue | Constats | Disposition |
|---|---|---|
| Code d'authentification (sur `a9face5`) | M1 : un attaquant pouvait maintenir le blocage d'un compte. M2 : devinette sans limite du mot de passe actuel. L1 à L7 faibles. | M1 corrigé (cookies d'appareil OWASP), M2 corrigé (limitation), L3, L4, L6, L7 et L2 (partiel) corrigés ; L1 (course limitée) et L5 (secrets TOTP en clair) documentés comme résidus |
| Configuration réseau et TLS (sur `a9face5`) | M1 : n'importe quel processus local pouvait passer pour le proxy. M2 : cohabitation avec GLPI non conçue. M3 : ni renouvellement ni alerte. L1 à L7 faibles. | Corrigés : Host et Origin stricts en mode public, règle nftables, variante Apache avec nom dédié, bloc par défaut, minuteur de certificat et alerte `OnFailure`, durcissements systemd, gunicorn et nginx |
| Ré-examen de l'authentification (sur `f609acf`) | M-NEW (moyen) : la ré-authentification administrateur et le changement de mot de passe pouvaient être bloqués par des échecs de connexion anonymes ; faibles : cookie d'appareil non révocable, audit anonyme résiduel, documentation | Corrigés dans `9bce8ed` : clés de limitation propres, cookie d'appareil lié à l'empreinte du mot de passe avec date d'émission vérifiée, audit anonyme réduit, SECURITY.md corrigé |
| Ré-examen de la configuration (sur `f609acf`) | H1 (élevé) : aucune exigence d'intégrité de l'hôte dans la liste de contrôle. M1 : un processus local partageant l'utilisateur du proxy (PHP de GLPI en www-data) pouvait passer pour le proxy. M2 : règle nftables ni persistante ni vérifiée. M3 : procédure d'installation inexécutable. Faibles divers. | Corrigés dans `9bce8ed` : prérequis bloquant d'hôte fiable, secret de proxy `X-Astra-Proxy` exigé en mode public, unité `astra-nft` requise par `astra-web`, procédure réécrite (systemd-run, choix de Python, ordre Apache, versions et lien symbolique, sauvegardes), chiffrement Apache, limites nginx |

| Revue finale, agent distinct (sur `9bce8ed`) | Aucun constat critique, élevé ou moyen. Sept constats faibles, dont une erreur 500 sur un cookie d'appareil malformé, les droits des fichiers de secret, la portabilité de nftables et la documentation. | Corrigés dans `cc3550b` (le test du cookie malformé échoue sans le correctif, ce qui a été vérifié) ; résidus documentés : verrouillage possible de la ré-authentification avec une session volée, et MTA de l'alerte |
| Confirmation (sur `cc3550b`) | Aucun constat critique, élevé ou moyen ; un résidu faible R1 : un rechargement de nftables supprimait la règle | Corrigé dans `d738460` (`ReloadPropagatedFrom`, `ExecReload`) |

**Verdict des deux revues initiales sur l'hôte :** publier ASTRA sur `102.211.199.132` dans son état actuel n'est **pas acceptable**. Un hôte dont l'intrusion n'est que partiellement remédiée doit être considéré comme non fiable : un attaquant local peut capturer les identifiants saisis, quelle que soit la qualité de l'application.

## 7. Blocages et actions externes restantes

1. **Hôte qualifié.** Il faut une machine virtuelle dédiée et propre, ou `.132` après reconstruction et remédiation attestée, avec un accès administrateur (sudo) donné par l'utilisateur.
2. **Python 3.14 sur l'hôte**, ou une validation complète de la suite sous 3.12 (décision).
3. **Nom DNS dédié** pointant vers l'hôte retenu, puis certificat Let's Encrypt pour ce nom.
4. **Canal d'alerte** : adresse de destination et MTA (décision).
5. **Port 62354** à identifier par l'administrateur du serveur, dans le cadre de la remédiation de l'intrusion.
6. **CI GitHub** : lever le verrou de facturation du compte.

Avant ces actions, seul l'**accès privé** est envisageable, par tunnel SSH depuis un hôte de rebond de confiance (mode privé, sans proxy). Le risque des identifiants saisis sur un hôte compromis demeure.

## 8. Fichiers modifiés

- **Commits :** `a9face5`, `f609acf`, `9bce8ed`, `cc3550b`, `d738460`, puis le commit de ce rapport (documentation seulement).
- **Ajoutés :**
  - code : `src/astra/auth/` (`__init__`, `crypto`, `store`, `admin_cli`), `src/astra/webapp.py`, `src/astra/templates/*` (9) ;
  - dépendances : `requirements/web.txt`, `requirements/server.txt` ;
  - déploiement : `deploy/` (gunicorn, 5 unités systemd, Apache et son secret, nginx, nftables, 2 modèles d'environnement), `tools/ops/check_certificate.py` ;
  - tests : `tests/test_webapp.py`, `tests/test_ops_tools.py` ;
  - documentation : `docs/DEPENDENCIES.md`, ce rapport.
- **Modifiés :** `src/astra/cli.py`, `src/astra/config.py` (`WebConfig`), `src/astra/static/app.js`, `src/astra/static/styles.css`, `.github/workflows/ci.yml`, `tests/test_market_risk.py`, ainsi que SECURITY, DEPLOYMENT, OPERATIONS, ARCHITECTURE, README, TESTING, `docs/CI.md`, TASKS et ROADMAP.
- **Supprimés :** `src/astra/web.py`, `src/astra/static/index.html` (devenu un modèle), `tests/test_web.py`, `tests/test_web_operations.py`.

## 9. Confirmations

- `main` n'a été ni fusionnée ni réécrite.
- La branche `feat/auth-deploy` n'est pas publiée.
- Aucun secret dans le dépôt, les journaux ou ce rapport.
- Aucune modification du serveur : inspection en lecture seule, autorisée par l'utilisateur.
- Le disque F: s'est déconnecté pendant une exécution. Le dépôt a été vérifié après la reconnexion (`git fsck` sans erreur) et l'exécution relancée.
- Aucune dépense.
- Aucun ordre ; mode SIMULATION ; RÉEL verrouillé.

Arrêt au point de contrôle.
