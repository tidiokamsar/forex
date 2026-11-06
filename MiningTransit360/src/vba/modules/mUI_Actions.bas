Attribute VB_Name = "mUI_Actions"
'==============================================================================
' MINING TRANSIT 360 - mUI_Actions
' Repartiteur des actions des fenetres de module (barre d'outils de frmList).
' Chaque action : libelle, couleur, droit requis, besoin d'une selection, traitement.
' Aucun bouton n'est affiche sans traitement associe ici.
'==============================================================================
Option Explicit

Public Function Act_Caption(ByVal action As String) As String
    Select Case action
        Case "NEW": Act_Caption = "+ Nouveau"
        Case "EDIT": Act_Caption = "Modifier"
        Case "VIEW": Act_Caption = "Consulter"
        Case "DEL": Act_Caption = "Supprimer"
        Case "VALID": Act_Caption = ChrW(10004) & " Valider"
        Case "REJECT": Act_Caption = "Rejeter"
        Case "CANCEL": Act_Caption = "Annuler"
        Case "PRINT": Act_Caption = "Imprimer"
        Case "EXPORT": Act_Caption = "Exporter"
        Case "REFRESH": Act_Caption = "Actualiser"
        Case "ATTACH": Act_Caption = "Joindre justificatif"
        Case "OPENDOC": Act_Caption = "Ouvrir justificatif"
        Case "VALID_DAY": Act_Caption = "Valider une journée"
        Case "TRUCK_CARD": Act_Caption = "Fiche camion"
        Case "TRUCK_STATS": Act_Caption = "Statistiques"
        Case "DEACTIVATE": Act_Caption = "Désactiver"
        Case "DRV_CARD": Act_Caption = "Fiche chauffeur"
        Case "DRV_STATS": Act_Caption = "Performance"
        Case "AFF_END": Act_Caption = "Terminer"
        Case "PLAN_GEN": Act_Caption = "Générer programme"
        Case "FUEL_ANALYZE": Act_Caption = "Analyser anomalies"
        Case "RENT_GEN": Act_Caption = "Générer relevés"
        Case "RENT_STATEMENT": Act_Caption = "Relevé horaire"
        Case "TIRE_MOUNT": Act_Caption = "Monter"
        Case "TIRE_UNMOUNT": Act_Caption = "Démonter"
        Case "TIRE_TRANSFER": Act_Caption = "Transférer"
        Case "TIRE_REPAIR": Act_Caption = "Réparation"
        Case "TIRE_RETURN": Act_Caption = "Retour réparation"
        Case "TIRE_SCRAP": Act_Caption = "Réformer"
        Case "TIRE_HIST": Act_Caption = "Historique pneu"
        Case "STOCK_IN": Act_Caption = "Entrée stock"
        Case "STOCK_OUT": Act_Caption = "Sortie stock"
        Case "INC_PROGRESS": Act_Caption = "Prendre en charge"
        Case "INC_CLOSE": Act_Caption = "Clôturer"
        Case "MNT_DONE": Act_Caption = "Terminer OT"
        Case "ADV_REFUND": Act_Caption = "Remboursement"
        Case "INV_GEN": Act_Caption = "Générer factures"
        Case "INV_VALID": Act_Caption = ChrW(10004) & " Valider facture"
        Case "INV_PREVIEW": Act_Caption = "Aperçu facture"
        Case "INV_PDF": Act_Caption = "Facture PDF"
        Case "INV_CANCEL": Act_Caption = "Annuler facture"
        Case "INV_LITIGE": Act_Caption = "Litige"
        Case "PAY_ADD": Act_Caption = "Encaisser"
        Case "CLIENT_SIT": Act_Caption = "Situation client"
        Case "AGING": Act_Caption = "Balance âgée"
        Case "CLOSE_DAY": Act_Caption = "Clôturer journée"
        Case "CLOSE_MONTH": Act_Caption = "Clôturer mois"
        Case "REOPEN": Act_Caption = "Réouvrir"
        Case "COHERENCE": Act_Caption = "Contrôle cohérence"
        Case "PWD_RESET": Act_Caption = "Réinit. mot de passe"
        Case "RIGHTS": Act_Caption = "Droits par profil"
        Case "BACKUP": Act_Caption = "Sauvegarder"
        Case "RESTORE": Act_Caption = "Restaurer"
        Case "INTEGRITY": Act_Caption = "Vérifier intégrité"
        Case "DEMO_RESET": Act_Caption = "Réinitialiser démo"
        Case Else: Act_Caption = action
    End Select
End Function

Public Function Act_Color(ByVal action As String, ByVal accent As Long) As Long
    Select Case action
        Case "NEW", "PAY_ADD", "INV_GEN", "STOCK_IN", "BACKUP": Act_Color = CLR_GREEN()
        Case "DEL", "REJECT", "CANCEL", "INV_CANCEL", "TIRE_SCRAP", "DEACTIVATE", "DEMO_RESET", "RESTORE": Act_Color = CLR_RED()
        Case "VALID", "INV_VALID", "VALID_DAY", "INC_CLOSE", "CLOSE_DAY", "CLOSE_MONTH": Act_Color = CLR_BLUE()
        Case "PRINT", "EXPORT", "INV_PDF", "INV_PREVIEW", "RENT_STATEMENT", "TRUCK_CARD", "DRV_CARD", "CLIENT_SIT", "AGING": Act_Color = CLR_TEAL()
        Case "REFRESH", "VIEW": Act_Color = CLR_GREY()
        Case Else: Act_Color = accent
    End Select
End Function

' Droit requis par action
Public Function Act_Right(ByVal action As String) As String
    Select Case action
        Case "NEW", "STOCK_IN", "STOCK_OUT", "RENT_GEN", "PLAN_GEN", "INV_GEN", "PAY_ADD", "BACKUP": Act_Right = "C"
        Case "EDIT", "ATTACH", "AFF_END", "TIRE_MOUNT", "TIRE_UNMOUNT", "TIRE_TRANSFER", "TIRE_REPAIR", "TIRE_RETURN", "INC_PROGRESS", _
             "MNT_DONE", "ADV_REFUND", "INV_LITIGE", "DEACTIVATE", "PWD_RESET", "RIGHTS", "FUEL_ANALYZE", "CANCEL": Act_Right = "M"
        Case "VALID", "REJECT", "VALID_DAY", "INV_VALID", "INV_CANCEL", "TIRE_SCRAP", "INC_CLOSE", "CLOSE_DAY", "CLOSE_MONTH", "REOPEN", _
             "RESTORE", "DEMO_RESET": Act_Right = "V"
        Case "DEL": Act_Right = "S"
        Case "PRINT", "EXPORT", "INV_PREVIEW", "INV_PDF", "RENT_STATEMENT", "TRUCK_CARD", "TRUCK_STATS", "DRV_CARD", "DRV_STATS", _
             "CLIENT_SIT", "AGING", "TIRE_HIST": Act_Right = "I"
        Case Else: Act_Right = "L"
    End Select
End Function

Public Function Act_NeedsSelection(ByVal action As String) As Boolean
    Act_NeedsSelection = Not InList(action, "NEW|REFRESH|PRINT|EXPORT|VALID_DAY|PLAN_GEN|FUEL_ANALYZE|RENT_GEN|RENT_STATEMENT|INV_GEN|" & _
                                            "AGING|CLOSE_DAY|CLOSE_MONTH|COHERENCE|RIGHTS|BACKUP|RESTORE|DEMO_RESET|TRUCK_STATS|DRV_STATS|CLIENT_SIT")
End Function

' Execute une action. host = fenetre frmList appelante (ou Nothing). Retourne Vrai si la liste doit etre rechargee.
Public Function Act_Run(ByVal modKey As String, ByVal action As String, ByVal id As Long, ByVal host As Object) As Boolean
    Dim m As Object, tbl As String, reason As String, d1 As Date, d2 As Date, n As Long, errs As Collection, v As Variant, msg As String
    Set m = Mod_Get(modKey)
    tbl = m("TableName")
    Sec_Require modKey, Act_Right(action)
    If Act_NeedsSelection(action) And id = 0 Then Fail "Sélectionnez d'abord une ligne dans la liste."
    Act_Run = True
    Select Case action
        Case "NEW": Act_Run = (Rec_OpenForm(modKey, 0, "NEW") > 0)
        Case "EDIT": Act_Run = (Rec_OpenForm(modKey, id, "EDIT") > 0)
        Case "VIEW": Rec_OpenForm modKey, id, "VIEW": Act_Run = False
        Case "DEL"
            If UI_Confirm("Supprimer définitivement cet enregistrement ?" & vbLf & Rec_Summary(tbl, id), "Suppression", "Supprimer", True) Then
                Rec_Delete tbl, id
            Else
                Act_Run = False
            End If
        Case "VALID"
            Rec_SetStatus tbl, id, ST_VALIDEE
        Case "REJECT"
            reason = UI_AskReason("Motif du rejet :", "Rejet")
            If Len(reason) = 0 Then Act_Run = False Else Rec_SetStatus tbl, id, ST_REJETEE, reason
        Case "CANCEL"
            reason = UI_AskReason("Motif de l'annulation :", "Annulation")
            If Len(reason) = 0 Then
                Act_Run = False
            ElseIf tbl = "T_PAIEMENTS" Then
                Bill_CancelPayment id, reason
            Else
                Rec_SetStatus tbl, id, ST_ANNULEE, reason
            End If
        Case "PRINT": Rpt_PrintList modKey, host: Act_Run = False
        Case "EXPORT": Rpt_ExportList modKey, host: Act_Run = False
        Case "REFRESH"
        Case "ATTACH"
            Dim col As String
            col = IIf(tbl = "T_DOCUMENTS", "Chemin", "Justificatif")
            If Len(Doc_Attach(tbl, id, col)) = 0 Then Act_Run = False
        Case "OPENDOC"
            Dim pth As String
            pth = ToStr(DB_Value(tbl, id, IIf(tbl = "T_DOCUMENTS", "Chemin", "Justificatif")))
            If Len(pth) = 0 Then Fail "Aucun justificatif rattaché."
            OpenFile pth
            Act_Run = False
        Case "VALID_DAY"
            Dim dd As Date
            dd = App_Today() - 1
            Dim f As frmInput
            Set f = Dlg_New("Valider une journée", "Valide toutes les rotations « Saisie » conformes de la date choisie", CLR_BLUE())
            f.AddField "D", "Date", "DATE", dd, , True
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Valider la journée", CLR_BLUE(), True
            If f.ShowDialog = "OK" Then
                dd = f.FieldValue("D")
                f.Dispose
                n = Ops_ValidateDay(dd, errs)
                msg = n & " rotation(s) validée(s) le " & FmtDate(dd) & "."
                If errs.Count > 0 Then
                    msg = msg & vbLf & vbLf & errs.Count & " non validée(s) :" & vbLf
                    For Each v In errs
                        msg = msg & "• " & v & vbLf
                    Next v
                End If
                UI_Alert msg, IIf(errs.Count > 0, "warning", "success"), "Validation"
            Else
                f.Dispose
                Act_Run = False
            End If
        Case "TRUCK_CARD": Rpt_TruckCard id: Act_Run = False
        Case "TRUCK_STATS": Rpt_RunByKey "PRD_TRUCK": Act_Run = False
        Case "DRV_CARD": Rpt_DriverCard id: Act_Run = False
        Case "DRV_STATS": Rpt_RunByKey "PRD_DRIVER": Act_Run = False
        Case "DEACTIVATE": Fleet_Deactivate id
        Case "AFF_END"
            Set f = Dlg_New("Terminer l'affectation", "", CLR_BLUE())
            f.AddField "D", "Date de fin", "DATE", App_Today(), , True
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Terminer", CLR_BLUE(), True
            If f.ShowDialog = "OK" Then
                dd = f.FieldValue("D")
                f.Dispose
                Fleet_EndAssignment id, dd
            Else
                f.Dispose
                Act_Run = False
            End If
        Case "PLAN_GEN"
            Set f = Dlg_New("Générer le programme journalier", "À partir des affectations actives et des camions opérationnels", CLR_GREEN())
            f.AddField "D", "Date", "DATE", App_Today() + 1, , True
            f.AddField "N", "Rotations prévues par camion", "INT", "4", , True
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Générer", CLR_GREEN(), True
            If f.ShowDialog = "OK" Then
                dd = f.FieldValue("D"): n = f.FieldValue("N")
                f.Dispose
                n = Fleet_GeneratePlanning(dd, n)
                UI_Alert n & " ligne(s) de programme créée(s) pour le " & FmtDate(dd) & ".", "success", "Programme"
            Else
                f.Dispose
                Act_Run = False
            End If
        Case "FUEL_ANALYZE"
            If UI_AskPeriod(d1, d2, "Période à analyser") Then
                n = Fuel_Reanalyze(d1, d2)
                UI_Alert n & " distribution(s) présentent une anomalie sur la période. Filtrez la colonne « Anomalie » ou lancez le rapport Carburant > Anomalies.", "info", "Analyse carburant"
            End If
        Case "RENT_GEN"
            d1 = App_Today() - 1: d2 = App_Today()
            If UI_AskPeriod(d1, d2, "Générer les relevés manquants") Then
                n = Rent_Generate(d1, d2)
                UI_Alert n & " relevé(s) horaire(s) généré(s) (immobilisations reprises des incidents).", "success", "Location"
            End If
        Case "RENT_STATEMENT": Rpt_RunByKey "LOC_HOURS": Act_Run = False
        Case "TIRE_MOUNT", "TIRE_UNMOUNT", "TIRE_TRANSFER", "TIRE_REPAIR", "TIRE_RETURN", "TIRE_SCRAP": Tire_ActionDialog action, id
        Case "TIRE_HIST": Rpt_TireHistory id: Act_Run = False
        Case "STOCK_IN": Stock_MoveDialog "Entrée", id
        Case "STOCK_OUT": Stock_MoveDialog "Sortie", id
        Case "INC_PROGRESS": Mnt_SetInProgress id
        Case "INC_CLOSE": Mnt_CloseDialog id
        Case "MNT_DONE": Mnt_CompleteOT id
        Case "ADV_REFUND": Fin_AdvanceRefund id
        Case "INV_GEN": Bill_GenerateMonthDialog
        Case "INV_VALID"
            If UI_Confirm("Valider la facture ? Un numéro légal définitif sera attribué et la facture ne pourra plus être modifiée.", "Validation de facture", "Valider") Then
                Bill_Validate id
                UI_Alert "Facture validée : " & DB_Value("T_FACTURES", id, "NumFacture"), "success", "Facturation"
            Else
                Act_Run = False
            End If
        Case "INV_PREVIEW": Inv_Preview id: Act_Run = False
        Case "INV_PDF": Inv_ExportPDF id, True: Act_Run = False
        Case "INV_CANCEL"
            reason = UI_AskReason("Motif d'annulation de la facture " & DB_Value("T_FACTURES", id, "NumFacture") & " (les rotations seront libérées) :", "Annulation de facture", "Annuler la facture")
            If Len(reason) = 0 Then Act_Run = False Else Bill_Cancel id, reason
        Case "INV_LITIGE": Bill_ToggleDispute id
        Case "PAY_ADD"
            If tbl = "T_FACTURES" Then Bill_PaymentDialog id Else Bill_PaymentDialog 0
        Case "CLIENT_SIT"
            Dim cid As Long
            cid = 0
            If tbl = "T_CLIENTS" And id > 0 Then cid = id
            If cid = 0 Then
                Dim pick As String
                pick = UI_Pick("Situation client", "Client", Schema_FKChoices(Schema_Field("T_FACTURES", "ClientID")))
                If Len(pick) = 0 Then Exit Function
                cid = IdOf("T_CLIENTS", pick)
            End If
            Rpt_ClientStatement cid
            Act_Run = False
        Case "AGING": Rpt_RunByKey "FIN_AGING": Act_Run = False
        Case "CLOSE_DAY": Close_Day
        Case "CLOSE_MONTH": Close_Month
        Case "REOPEN": Close_Reopen id
        Case "COHERENCE"
            UI_ShowGrid "Contrôle de cohérence", "Intégrité des données au " & FmtDate(App_Today()), "Gravité|Contrôle|Détail", "80;200;440", Check_Coherence()
            Act_Run = False
        Case "PWD_RESET": Admin_ResetPassword id
        Case "RIGHTS": Admin_EditRights
        Case "BACKUP": Backup_Run "Manuelle"
        Case "RESTORE": Backup_RestoreDialog
        Case "INTEGRITY"
            Dim ok As Boolean
            ok = Backup_Verify(ToStr(DB_Value("T_SAUVEGARDES", id, "Fichier")))
            DB_SetValue "T_SAUVEGARDES", id, "Verifiee", IIf(ok, "Oui", "Non")
            UI_Alert IIf(ok, "Sauvegarde intègre (structure et volumétrie conformes).", "Sauvegarde NON conforme ou illisible."), IIf(ok, "success", "error"), "Vérification"
        Case "DEMO_RESET": Demo_ResetDialog
        Case Else
            Fail "Action non prise en charge : " & action
    End Select
End Function
