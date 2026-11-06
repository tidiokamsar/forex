# 06 - Guide de sauvegarde et de restauration

## Types de sauvegarde

| Type | Déclenchement |
|---|---|
| Manuelle | Administration > Sauvegardes > Sauvegarder |
| Automatique | À la fermeture, si la dernière sauvegarde date de plus de `SAUV_AUTO_HEURES` (24 h par défaut) |
| Clôture | À chaque clôture mensuelle |
| Pré-restauration | Systématiquement avant une restauration ou une réinitialisation démo |

Chaque sauvegarde est une **copie complète** du classeur (`SaveCopyAs`) nommée `MT360_AAAAMMJJ_HHMMSS_<type>.xlsm`, placée dans
`SAUV_DOSSIER` (par défaut le sous-dossier `Sauvegardes` du classeur ; idéalement un disque externe ou un dossier synchronisé).

## Vérification d'intégrité

Après chaque copie, le fichier est **rouvert en lecture seule, macros désactivées**, et l'application vérifie que toutes les tables
existent et que leur nombre de lignes est identique à celui de l'application. Le résultat (« Intégrité vérifiée ») est inscrit dans
l'historique (table `T_SAUVEGARDES`). « Vérifier intégrité » relance ce contrôle sur une sauvegarde sélectionnée.

## Conservation

Les `SAUV_CONSERVER` (30) dernières sauvegardes sont conservées ; les plus anciennes sont supprimées, **sauf la dernière sauvegarde
vérifiée, qui n'est jamais supprimée**.

## Restauration (administrateur)

1. Administration > Sauvegardes > Restaurer, choisir le fichier.
2. L'application fait une sauvegarde « Pré-restauration » de l'état actuel.
3. Elle contrôle que la sauvegarde contient toutes les tables, puis remplace les **données** table par table (correspondance par nom
   de colonne : une sauvegarde d'une version antérieure reste restaurable). Le code et l'interface restent ceux de la version en cours.
4. Contrôler ensuite : Administration > Contrôle de cohérence.

En cas d'échec en cours de restauration, le message l'indique et la sauvegarde pré-restauration permet de revenir à l'état antérieur.

## Bonnes pratiques

* Une copie hebdomadaire hors du poste (clé USB chiffrée, serveur).
* Tester une restauration chaque mois sur un poste de test.
* Ne jamais ouvrir deux instances du classeur en même temps (application monoposte).
