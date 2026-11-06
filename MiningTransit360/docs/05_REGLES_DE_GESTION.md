# 05 - Règles de gestion

Chaque règle est implémentée dans le module indiqué et, quand c'est possible, vérifiée par un test (08_TESTS).

## Propriété et location (mBiz_Rental)

| Règle | Détail |
|---|---|
| R-LOC-1 | Les camions appartiennent à Atiko Guinée ; chaque camion a un tarif horaire paramétrable. |
| R-LOC-2 | Heures brutes = fin - début du relevé (passage de minuit géré). |
| R-LOC-3 | Heures retenues = brutes - attente (si `LOC_DEDUIRE_ATTENTE`) - immobilisation Atiko (si `LOC_DEDUIRE_IMMOB_ATIKO`) - immobilisation exploitant (si `LOC_DEDUIRE_IMMOB_EXPLOITANT`), puis minimum journalier (camion ou `LOC_HEURES_MIN_JOUR`, sauf immobilisation Atiko) et plafond `LOC_HEURES_MAX_JOUR`. **Aucune exonération n'est supposée** : chaque déduction est un paramètre contractuel. |
| R-LOC-4 | Le tarif est figé sur le relevé à sa création (historique conservé si le tarif du camion change ; changement tracé). |
| R-LOC-5 | Un seul relevé actif par camion et par jour ; attente + immobilisations ≤ heures brutes. |
| R-LOC-6 | « Générer relevés » reprend les heures d'immobilisation des incidents qui chevauchent le poste, réparties selon le responsable. |

## Production (mBiz_Operations)

| Règle | Détail |
|---|---|
| R-ROT-1 | Poids net = brut - tare (calculé, jamais saisi) ; brut > tare. |
| R-ROT-2 | Net ≤ capacité x (1 + `ROT_TOLERANCE_SURCHARGE_PCT`) ; net ≥ `ROT_NET_MIN`. |
| R-ROT-3 | Durée de rotation ≤ `ROT_DUREE_MAX_H` ; pas de date future. |
| R-ROT-4 | Le chauffeur doit être affecté au camion à la date et avoir un permis valide. |
| R-ROT-5 | Origine = lieu de chargement ; destination = lieu de déchargement ; le client est celui de la destination. |
| R-ROT-6 | Un ticket = une rotation (unicité sur rotations et pesées) ; doublon probable refusé (même camion, départ à moins de 20 min). |
| R-ROT-7 | Cycle : Saisie → Validée / Rejetée (motif obligatoire) ; annulation avec motif ; une rotation validée modifiée repasse en Saisie. |
| R-ROT-8 | Seules les rotations **validées** sont facturées ; une rotation facturée est verrouillée. |

## Flotte (mBiz_Fleet)

| Règle | Détail |
|---|---|
| R-FLT-1 | Affectation : camion actif non hors service (ni en panne / immobilisé pour une affectation immédiate), chauffeur actif à permis valide. |
| R-FLT-2 | Pas de chevauchement : un chauffeur sur un seul camion, un camion / poste avec un seul chauffeur. |
| R-FLT-3 | Statut camion automatique : « En panne » / « Immobilisé » tant qu'une immobilisation est ouverte, puis « En service » (affecté) ou « Disponible ». |
| R-FLT-4 | Un camion affecté ne peut être désactivé ; un chauffeur affecté ne peut être sorti. |

## Carburant (mBiz_Fuel)

| Règle | Détail |
|---|---|
| R-CAR-1 | Montant = litres x prix ; approvisionnement : fournisseur obligatoire ; distribution : camion obligatoire. |
| R-CAR-2 | Compteur moteur croissant par camion ; consommation L/h = litres / heures depuis le plein précédent. |
| R-CAR-3 | Anomalies signalées (non bloquantes) : surconsommation > référence x (1 + `CARB_TOLERANCE_PCT`), quantité > `CARB_MAX_LITRES`, doublon (< `CARB_DOUBLON_HEURES` h moteur ou 2 pleins le même jour), compteur absent, stock cuve insuffisant. |
| R-CAR-4 | **Charge = consommation (distributions)** ; les approvisionnements alimentent le stock de cuve (pas de double comptabilisation). |

## Pneumatiques et stock (mBiz_Tires)

| Règle | Détail |
|---|---|
| R-PNE-1 | Un pneu est un actif identifié par son n° de série ; tout mouvement est historisé (Entrée, Montage, Démontage, Transfert, Réparation, Retour réparation, Réforme). |
| R-PNE-2 | Une position d'un camion ne porte qu'un pneu ; seul un pneu en stock peut être monté. |
| R-PNE-3 | Heures cumulées = historique + heures de service du camion porteur depuis le montage (relevés de location). |
| R-PNE-4 | « À remplacer » : profondeur < `PNEU_PROFONDEUR_MIN` ou heures > 90 % de la durée de vie cible ; alerte de stock si < `PNEU_STOCK_MIN`. |
| R-PNE-5 | Charge pneumatique = achats (entrées) + réparations de la période. |
| R-STK-1 | Consommables : prix moyen pondéré recalculé à chaque mouvement ; sortie ≤ stock ; charge = sorties valorisées. |

## Maintenance (mBiz_Maintenance)

| Règle | Détail |
|---|---|
| R-MNT-1 | La maintenance mécanique contractuelle est à la charge d'Atiko : son coût est suivi « pour mémoire » et **n'entre jamais dans la marge de l'exploitant**. |
| R-MNT-2 | Coût imputé à l'exploitant = coût de l'incident si et seulement si le responsable est « Exploitant ». |
| R-MNT-3 | Clôture d'un incident : responsable définitif obligatoire (pas « À déterminer »), date de reprise si immobilisation, intervention décrite. |

## Dépenses et trésorerie (mBiz_Finance)

| Règle | Détail |
|---|---|
| R-DEP-1 | Une dépense « Carburant / Pneumatiques / Location camions » à la charge de l'exploitant est refusée (module dédié) : anti double comptabilisation. |
| R-DEP-2 | Doublons refusés : même fournisseur + même référence de facture ; même date + montant + libellé. |
| R-DEP-3 | Seules les dépenses **validées** à la charge de l'exploitant entrent dans la marge ; « Atiko (contrat) » et « Refacturable client » sont exclues. |
| R-CAI-1 | Caisse : sortie ≤ solde ; solde progressif recalculé chronologiquement. |
| R-AVA-1 | Avance : bénéficiaire = matricule existant ; remboursement ≤ montant ; statut Soldée automatique. |
| R-PER-1 | Coût du personnel = salaires x (1 + `CHARGES_SOCIALES_PCT`) au prorata calendaire + prime par rotation validée. |

## Facturation et recouvrement (mBiz_Billing)

| Règle | Détail |
|---|---|
| R-FAC-1 | Facturation mensuelle par client sur le tonnage **validé** : montant HT = tonnage x tarif client ; TVA paramétrable (`TVA_TAUX`, 0 par défaut, à confirmer par le fiscaliste). |
| R-FAC-2 | Un brouillon réserve les rotations ; une seule facture non annulée par client et par mois. |
| R-FAC-3 | Le numéro légal `FAC-AAAA-nnnn` (séquence sans trou) est attribué à la **validation** ; une facture validée n'est jamais modifiée. |
| R-FAC-4 | À la validation, une **empreinte d'intégrité** (n°, client, tonnage, TTC) est calculée ; le contrôle de cohérence détecte toute modification silencieuse. |
| R-FAC-5 | Annulation : motif obligatoire, impossible si un encaissement existe ; les rotations sont libérées pour une nouvelle facture. |
| R-PAI-1 | Plusieurs encaissements par facture ; montant ≤ solde dû ; date ≥ date de facture ; référence bancaire unique par facture. |
| R-PAI-2 | Statut : Validée → Partielle → Payée, recalculé à chaque encaissement ou annulation d'encaissement. |
| R-REC-1 | Relance suggérée selon le retard : `RELANCE_J1` rappel, `RELANCE_J2` relance, `RELANCE_J3` mise en demeure. |

## Indicateurs et marge (mBiz_KPI)

| Indicateur | Définition |
|---|---|
| CA reconnu | Tonnage validé de la période x tarif du client |
| CA facturé | HT des factures validées / partielles / payées dont la période commence dans l'intervalle |
| Encaissements | Paiements non annulés datés dans la période |
| Créances | Soldes des factures validées et partielles |
| Charges de l'exploitant | Location + carburant consommé + pneumatiques + consommables + personnel + dépenses diverses + charges exceptionnelles |
| Marge opérationnelle estimée | CA reconnu - charges de l'exploitant ; taux = marge / CA |
| Coût complet par tonne | Charges / tonnage validé |
| Disponibilité | 1 - heures d'immobilisation / heures brutes des relevés |
| Taux d'attente | Heures d'attente / heures brutes |

## Clôtures (mBiz_Closing)

| Règle | Détail |
|---|---|
| R-CLO-1 | Clôture journalière : aucune rotation ni opération carburant en Saisie, un relevé de location par camion ayant roulé ; les jours précédents doivent être clos (jours sans activité tolérés). |
| R-CLO-2 | Après clôture, création / modification / suppression interdites sur rotations, pesées, carburant, location de la journée. |
| R-CLO-3 | Clôture mensuelle : journées closes, location / carburant / dépenses validés, aucune production validée non facturée, aucun brouillon ; verrouille dépenses, caisse, stock du mois ; sauvegarde automatique. |
| R-CLO-4 | Réouverture : administrateur uniquement, motif obligatoire, tracée. |

## Sécurité (mCore_Security)

* Profils : Administrateur, Directeur, Responsable exploitation, Comptable, Agent de saisie ; droits L C M V S I par module,
  modifiables (Administration > Utilisateurs > Droits par profil).
* Mots de passe : jamais stockés en clair (empreinte salée) ; 6 caractères minimum ; changement exigé à la première connexion ;
  verrouillage après 5 échecs ; au moins un administrateur actif.
* Journal d'audit de toutes les opérations sensibles.

**Limites de sécurité inhérentes à Excel VBA (déclarées)** : quiconque dispose du fichier et de compétences VBA peut lire les feuilles
masquées, modifier le code ou contourner l'authentification. Les contrôles protègent contre les erreurs et les accès ordinaires, pas
contre une attaque délibérée. L'empreinte utilisée n'est pas une primitive cryptographique certifiée. Mesures complémentaires
recommandées : protéger le projet VBA par mot de passe (Outils > Propriétés de VBAProject > Protection), stocker le classeur sur un
poste à session Windows personnelle, sauvegardes hors poste, et à terme migration vers une base serveur (voir 01_ARCHITECTURE).
