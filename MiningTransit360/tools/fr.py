# -*- coding: utf-8 -*-
"""Accentuation francaise des libelles / valeurs (les noms de champs ne sont jamais modifies)."""
import re

_WORDS = """validee:validée validees:validées rejetee:rejetée rejetees:rejetées annulee:annulée annule:annulé
terminee:terminée planifiee:planifiée planifie:planifié realise:réalisé realisee:réalisée payee:payée soldee:soldée
monte:monté reforme:réformé reparation:réparation entree:entrée entrees:entrées demontage:démontage enregistre:enregistré
resolue:résolue conge:congé journaliere:journalière journalieres:journalières preventive:préventive revision:révision
controle:contrôle controles:contrôles dechargement:déchargement securite:sécurité electrique:électrique mecanique:mécanique
especes:espèces cheque:chèque telephone:téléphone guinee:guinée determiner:déterminer immobilise:immobilisé
cloturee:clôturée reouverte:réouverte cloture:clôture clotures:clôtures pesee:pesée pesees:pesées periode:période
depenses:dépenses depense:dépense reclamations:réclamations reclamation:réclamation parametres:paramètres parametre:paramètre
etats:états etat:état releves:relevés releve:relevé activite:activité societe:société echeance:échéance reference:référence
references:références designation:désignation unite:unité quantite:quantité numero:numéro cree:créé modifie:modifié
prenom:prénom categorie:catégorie gravite:gravité duree:durée debut:début deja:déjà beneficiaire:bénéficiaire apres:après
operation:opération operations:opérations operateur:opérateur detectee:détectée detecte:détecté theorique:théorique
systeme:système modele:modèle capacite:capacité proprietaire:propriétaire serie:série echecs:échecs derniere:dernière
integrite:intégrité verifiee:vérifiée emis:émis conteste:contesté resolution:résolution ecart:écart prevues:prévues
prevu:prévu prevue:prévue generation:génération genere:généré exonere:exonéré tolere:toléré toleree:tolérée defaut:défaut
reapprovisionnement:réapprovisionnement medicaux:médicaux evenement:événement scolarite:scolarité electrogene:électrogène
telephonique:téléphonique telephoniques:téléphoniques equipes:équipes materiel:matériel verrouillees:verrouillées
anterieures:antérieures recalcule:recalculé forcee:forcée affiche:affiché affichee:affichée ecran:écran
demonstration:démonstration reinitialisation:réinitialisation autorisee:autorisée deduites:déduites considerons:considérons
trouve:trouvé constate:constaté defaillant:défaillant depassement:dépassement retroviseur:rétroviseur eclatement:éclatement
jumelee:jumelée leger:léger telecom:télécom pieces:pièces piece:pièce immobilises:immobilisés detectees:détectées
reglement:règlement regle:réglé derniere:dernière premiere:première numerotation:numérotation coordonnees:coordonnées
fiscaux:fiscaux mentions:mentions facturees:facturées facture:facture factures:factures delai:délai delais:délais
prealable:préalable donnees:données fictives:fictives propre:propre prime:prime renseigne:renseigné plafond:plafond
reserve:réservé reservee:réservée chauffeur:chauffeur equipe:équipe ete:été decembre:décembre fevrier:février aout:août
methode:méthode categorie:catégorie suspendue:suspendue cuve:cuve jauge:jauge redevances:redevances lubrifiants:lubrifiants
refroidissement:refroidissement electricite:électricité accrochage:accrochage reforme:réformé remplace:remplacé
complete:complète journee:journée realisations:réalisations generale:générale general:général financier:financier
selectionne:sélectionné selection:sélection precedent:précédent detail:détail details:détails horodatage:horodatage
reception:réception refacturer:refacturer interlocuteur:interlocuteur sculpture:sculpture cumulees:cumulées
verifie:vérifié verifiees:vérifiées sauvegardes:sauvegardes conservees:conservées supprimee:supprimée
consommation:consommation reference:référence mecaniques:mécaniques securitaires:sécuritaires hebdomadaire:hebdomadaire
"""
_MAP = {}
for tok in _WORDS.split():
    a, b = tok.split(":")
    _MAP[a] = b
_RX = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _MAP), key=len, reverse=True)) + r")\b", re.IGNORECASE)


def acc(s):
    if not isinstance(s, str) or not s:
        return s

    def rep(m):
        w = m.group(0)
        out = _MAP[w.lower()]
        if w.isupper() and len(w) > 1:
            return out.upper()
        if w[0].isupper():
            return out[0].upper() + out[1:]
        return out
    s = _RX.sub(rep, s)
    s = s.replace("A déterminer", "À déterminer")
    return s
