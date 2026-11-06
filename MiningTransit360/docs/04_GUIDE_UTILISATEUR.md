# 04 - Guide utilisateur

## 1. Connexion et tableau de bord

À l'ouverture, saisissez votre identifiant et votre mot de passe. Le tableau de bord présente, pour la date de travail :

* **Tonnage du jour** (rotations saisies et validées) et sa variation par rapport au jour ouvré précédent ;
* **CA du mois** (tonnage validé valorisé au tarif client), **encaissements** du mois, **créances** (soldes dus), **camions actifs** ;
* **Production de la semaine** (6 derniers jours ouvrés), **répartition des charges** du mois, **alertes** (les 4 plus importantes,
  bouton « Voir » vers le module concerné, la pastille donne le nombre total ; cliquer sur le titre ouvre toutes les alertes) ;
* **Statut de la flotte**, **factures du mois**, **marge d'exploitation** (montant, taux, écart de taux vs mois précédent, courbe des
  8 dernières semaines).

Tout est cliquable : une carte ou un panneau ouvre le module ou l'état correspondant. Le nom d'utilisateur (en haut à droite) ouvre
le menu personnel (mot de passe, actualisation, alertes, plein écran, déconnexion).

## 2. Menu

| Entrée | Contenu |
|---|---|
| Exploitation | Journée en cours (synthèse par camion), saisie rapide, programme journalier, affectations, objectifs, clôtures |
| Camions | Fiche camion, statistiques, désactivation |
| Chauffeurs | Fiche chauffeur, performance |
| Rotations et pesées | Rotations, tickets, saisie rapide, validation d'une journée |
| Carburant | Approvisionnements cuve et distributions, analyse des anomalies |
| Location horaire | Relevés, génération automatique, relevé horaire par camion |
| Pneumatiques | Pneus, mouvements, stock de consommables |
| Incidents & maintenance | Incidents (prise en charge, clôture), ordres de travail |
| Dépenses | Dépenses, caisse, avances, personnel |
| Facturation | Génération mensuelle, validation, aperçu, PDF, annulation, litiges, encaissement |
| Paiements & recouvrement | Encaissements, relances, réclamations, balance âgée, situation client |
| Rapports et états | Centre de rapports (43 états) |
| Paramètres | Paramètres, clients, fournisseurs, lieux, documents, mot de passe |
| Administration | Utilisateurs et droits, audit, sauvegardes, cohérence, clôtures, tests |

## 3. Fenêtre de module

* **Rechercher** : texte présent dans n'importe quelle colonne (Entrée pour filtrer).
* **Du / au** : période (par défaut les 30 derniers jours), **Statut**, **Actifs seulement**.
* Cliquer sur un en-tête trie la colonne (second clic : ordre inverse).
* Double-clic : modification (ou consultation selon vos droits).
* Le pied de fenêtre affiche le nombre de lignes et les totaux (montants, tonnages, litres, heures).
* La barre d'actions ne montre que les actions permises par votre profil.

## 4. Saisir une fiche

Les champs marqués `*` sont obligatoires. Les champs grisés sont calculés (poids net, montant, heures retenues, durée
d'immobilisation...) et se mettent à jour pendant la saisie. Le chauffeur affecté et le client de la destination se remplissent
automatiquement. En cas d'erreur, le message s'affiche en rouge en bas de la fiche et rien n'est enregistré.

## 5. Processus quotidiens

1. **Programme** (Exploitation > Programme journalier > Générer programme) : une ligne par camion opérationnel affecté.
2. **Rotations** : saisie rapide (enchaînement), une rotation = un ticket de pesée ; le ticket est créé automatiquement.
3. **Carburant** : un bon par plein, compteur moteur obligatoire pour l'analyse de consommation.
4. **Location** : « Générer relevés » crée les relevés manquants ; les heures d'immobilisation sont reprises des incidents.
5. **Validation** : le responsable valide les rotations (une par une ou « Valider une journée »), le carburant, la location.
6. **Clôture journalière** (Clôtures > Clôturer journée) : refusée tant qu'il reste des saisies non validées ou des relevés manquants ;
   ensuite la journée est verrouillée.

## 6. Processus mensuels

1. Clôturer toutes les journées du mois ; valider dépenses, location, carburant.
2. **Facturation > Générer factures** : un brouillon par client (rotations validées non facturées du mois) ; vérifier l'aperçu ;
   **Valider facture** attribue le numéro légal et scelle la facture ; **Facture PDF** pour l'envoi.
3. **Encaisser** chaque règlement (partiel autorisé, jamais au-delà du solde).
4. Suivi : Balance âgée, Retards de paiement, relances (module Recouvrement), litiges.
5. **Clôturer le mois** (Clôtures > Clôturer mois) : contrôle que tout est validé et facturé, verrouille le mois, sauvegarde.

## 7. Pneumatiques

Sélectionner un pneu puis : **Monter** (camion + position libre), **Démonter** (vers stock ou réparation, profondeur mesurée),
**Transférer**, **Réparation**, **Retour réparation**, **Réformer**, **Historique pneu**. Les heures de service sont calculées à
partir des relevés de location du camion porteur.

## 8. Incidents

Déclarer l'incident (immobilisation oui/non, responsable). Le camion passe automatiquement « En panne » ou « Immobilisé ».
**Prendre en charge** puis **Clôturer** (date de reprise, intervention, responsable définitif, coût) : le camion redevient
« En service ». Seul un coût dont le responsable est « Exploitant » est imputé à la marge.

## 9. Rapports

Rapports et états : choisir la catégorie, l'état, la période (raccourcis Jour / 7 jours / Mois / Mois précédent / Année) et le
filtre, puis **Consulter à l'écran**, **Aperçu (PDF)**, **Imprimer**, **Exporter PDF** ou **Exporter Excel**. Les fichiers sont
enregistrés dans le dossier `Exports`. Toute liste de module peut aussi être imprimée ou exportée.
