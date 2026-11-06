# 03 - Guide d'installation

## Prérequis

* Windows 10 ou 11, Microsoft Excel 2016 / 2019 / 2021 / Microsoft 365 (32 ou 64 bits), version Windows.
* Paramètres régionaux français recommandés (le logiciel accepte aussi le point décimal).
* Aucun accès Internet, aucun serveur.

## 1. Construire le classeur (une seule fois, sur un poste avec Excel)

Le dépôt contient les **sources** ; le classeur `MINING_TRANSIT_360.xlsm` est assemblé et compilé par Excel lui-même.

1. Copier le dossier `MiningTransit360` sur le poste Windows.
2. Double-cliquer sur `build\Build-MT360.cmd`.
   * Le script active **temporairement** l'option « Accès approuvé au modèle d'objet du projet VBA » puis la restaure.
   * Il importe les modules, crée les fenêtres, **compile** le projet, installe le tableau de bord, exécute la **recette automatique**,
     exporte les sources réelles (`dist\src_export`, avec les `.frm/.frx` produits par Excel) et crée le dossier livrable.
3. Résultat : `dist\MINING_TRANSIT_360\` contenant :
   ```
   MINING_TRANSIT_360.xlsm      application
   assets\                      icônes, logo, bandeau
   data\demo_dataset.xlsx       jeu de démonstration (réinitialisation)
   docs\                        documentation
   ```
   et `dist\build_log.txt`, `dist\test_results_vba.txt` (résultat de la recette).

Options : `-SkipTests` (pas de recette), `-RegenerateData` (régénère données et icônes avec Python 3 + openpyxl + Pillow),
`-Visible` (Excel visible pendant la construction).

En cas d'erreur de compilation, Excel affiche l'erreur dans l'éditeur VBA, le script s'arrête avec le code 2 et le journal l'indique.

## 2. Installer sur le poste d'exploitation

1. Copier le dossier `dist\MINING_TRANSIT_360` (par exemple dans `C:\MiningTransit360`).
2. Ajouter ce dossier aux **emplacements approuvés** d'Excel (Fichier > Options > Centre de gestion de la confidentialité >
   Emplacements approuvés) pour que les macros s'exécutent sans avertissement.
3. Ouvrir `MINING_TRANSIT_360.xlsm` : l'écran de connexion s'affiche.

Comptes de démonstration (à changer avant tout usage réel) :

| Identifiant | Mot de passe | Profil |
|---|---|---|
| admin | Admin@2026 | Administrateur |
| directeur | Direction@2026 | Directeur |
| exploitation | Exploit@2026 | Responsable exploitation |
| comptable | Compta@2026 | Comptable |
| saisie | Saisie@2026 | Agent de saisie |

## 3. Passer en exploitation réelle

1. Administration > Sauvegardes > **Sauvegarder** (conserve la démo).
2. Paramètres : renseigner la société, le site, l'adresse, la banque, le NIF, les tarifs ; **vider `DATE_TRAVAIL`** (la date du
   jour sera utilisée) ; mettre `DEMO_MODE` à `Non` (interdit la réinitialisation démo et retire les mentions DÉMO).
3. Vider les tables de démonstration : remplacer `data\demo_dataset.xlsx` par un fichier ne contenant que les référentiels réels
   (même structure), puis Administration > Sauvegardes > Réinitialiser démo **avant** de passer `DEMO_MODE` à Non ; ou saisir les
   référentiels via les écrans (clients, lieux, camions, chauffeurs, affectations, utilisateurs).
4. Créer les comptes réels (Utilisateurs et droits) et désactiver les comptes de démonstration.

## 4. Dépannage

| Symptôme | Solution |
|---|---|
| « Les macros ont été désactivées » | Ajouter le dossier aux emplacements approuvés |
| Icônes absentes du tableau de bord | Le dossier `assets` doit être à côté du classeur ; sinon les icônes incorporées (feuille ASSETS) sont utilisées après « Actualiser » |
| Mot de passe administrateur perdu | Alt+F11 > module `mSys_Install` > exécuter `Install_EmergencyAdminReset` (admin / Admin@2026, changement exigé) |
| Ruban masqué après un arrêt brutal | Ouvrir puis fermer normalement l'application, ou mettre `UI_PLEIN_ECRAN` à Non |
| Tableau de bord abîmé | Administration (profil admin) : Alt+F8 > `Dash_Build` |
