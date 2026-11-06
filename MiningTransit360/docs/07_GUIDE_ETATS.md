# 07 - Guide de génération des états

Le catalogue complet (43 états) est dans `07_ETATS_CATALOGUE.md`.

## Accès

* **Rapports et États** (menu ou action rapide « Voir les rapports ») : centre de rapports.
* Depuis une fenêtre de module : **Imprimer** / **Exporter** la liste filtrée ; actions dédiées (Fiche camion, Fiche chauffeur,
  Relevé horaire, Situation client, Balance âgée, Historique pneu, Aperçu facture, Facture PDF).
* Depuis le tableau de bord : un clic sur le CA ou la marge ouvre le tableau de bord mensuel, sur la répartition des charges
  l'analyse de rentabilité.

## Sorties

| Sortie | Résultat |
|---|---|
| Consulter à l'écran | État affiché sur la feuille RAPPORT (bouton « Tableau de bord » pour revenir, non imprimé) |
| Aperçu | PDF généré et ouvert dans le lecteur PDF |
| Imprimer | Imprimante par défaut |
| Exporter PDF | Fichier dans `Exports` (ou `DOSSIER_EXPORTS`) |
| Exporter Excel | Classeur `.xlsx` indépendant dans `Exports` |

## Modèle d'impression

* A4, **portrait jusqu'à 7 colonnes, paysage au-delà**, ajusté à la largeur d'une page, marges 1 cm.
* En-tête : logo, raison sociale, adresse, téléphone, e-mail, catégorie de l'état, mention « DONNÉES DE DÉMONSTRATION FICTIVES » en mode démo.
* Bandeau de titre marine + liseré orange, période, filtres appliqués.
* Tableaux : en-têtes marine, lignes alternées, formats (dates, heures, montants GNF sans décimale, tonnages 2 décimales, %),
  ligne de totaux, nombre de lignes.
* Bloc de signatures « Établi par / Vérifié par / Approuvé par » (le préparateur est prérempli).
* Pied de page : nom de l'état, « Page n / N », date et heure de génération, préparateur.

## Facture

En-tête société, « FACTURE », n°, date, échéance, contrat ; bloc client ; objet (période, nombre de rotations) ; lignes
(désignation par destination, quantité, unité, prix unitaire, montant) ; totaux HT / TVA / TTC, déjà réglé et reste à payer ;
**montant en lettres** ; conditions de paiement, coordonnées bancaires, mentions (`FACT_MENTIONS`), signataire (`FACT_SIGNATAIRE`) ;
**annexe** : récapitulatif journalier des rotations facturées. Filigrane BROUILLON / ANNULÉE / DÉMO. PDF : `FAC-AAAA-nnnn_CLIENT.pdf`.

## Ajouter un état

1. Déclarer l'état dans `Rpt_Catalog` (code, catégorie, titre, période, filtre, description).
2. Écrire son constructeur dans `mRpt_Catalog` : créer un `cReport`, ajouter des blocs `AddInfo` / `AddTable` / `AddText`.
3. Le rendu, la mise en page, l'aperçu et les exports sont automatiques ; la recette (`T_Reports`) génère tous les états du catalogue.
