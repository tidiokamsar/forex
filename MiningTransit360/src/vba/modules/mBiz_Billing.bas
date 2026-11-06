Attribute VB_Name = "mBiz_Billing"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Billing
' Facturation mensuelle (tonnage valide), encaissements, creances, relances.
'
' Cycle facture : Brouillon -> Validee -> Partielle -> Payee   (ou Annulee)
'  - le brouillon reserve les rotations validees non facturees de la periode ;
'  - le numero legal (FAC-AAAA-0001, sequence sans trou) est attribue a la validation ;
'  - une facture validee est figee et scellee par une empreinte d'integrite :
'    toute modification silencieuse est detectee par le controle de coherence ;
'  - annulation uniquement sans encaissement, avec motif : les rotations sont liberees ;
'  - un paiement ne peut exceder le solde du (pas de trop-percu).
'==============================================================================
Option Explicit

Public Function Bill_Fingerprint(ByVal num As String, ByVal clientCode As String, ByVal tonnage As Double, ByVal ttc As Double) As String
    Dim payload As String
    payload = num & "|" & clientCode & "|" & Replace(Format$(Round(tonnage, 3), "0.000"), ",", ".") & "|" & Format$(Round(ttc, 0), "0")
    Bill_Fingerprint = MT_Hash(payload, 8)
End Function

Public Function Bill_VerifyFingerprint(ByVal invId As Long) As Boolean
    Dim f As Object
    Set f = DB_Get("T_FACTURES", invId)
    If f Is Nothing Then Exit Function
    If f("Statut") = FAC_BROUILLON Or f("Statut") = FAC_ANNULEE Then Bill_VerifyFingerprint = True: Exit Function
    Bill_VerifyFingerprint = (Bill_Fingerprint(f("NumFacture"), CodeOf("T_CLIENTS", f("ClientID")), ToDbl(f("Tonnage")), ToDbl(f("MontantTTC"))) = ToStr(f("Empreinte")))
End Function

' Rotations facturables : validees, non facturees, client, periode
Public Function Bill_BillableRows(ByVal clientId As Long, ByVal d1 As Date, ByVal d2 As Date) As Collection
    Set Bill_BillableRows = DB_Rows("T_ROTATIONS", "ClientID", "=", clientId, "Statut", "=", ST_VALIDEE, "FactureID", "ISBLANK", "", _
                                    "DateRotation", ">=", d1, "DateRotation", "<=", d2)
End Function

' Genere un brouillon de facture pour un client et un mois. Retourne l'ID facture.
Public Function Bill_GenerateDraft(ByVal clientId As Long, ByVal monthKeyStr As String, Optional ByVal silent As Boolean = False) As Long
    Dim d1 As Date, d2 As Date, rows As Collection, data As Variant, r As Variant, ton As Double, n As Long
    Dim cli As Object, tarif As Double, ht As Double, tva As Double, rec As Object, invId As Long, pending As Long
    Dim byDest As Object, k As Variant, destId As Long, cNet As Long, cDest As Long, cId As Long
    Sec_Require "FACTURES", "C"
    d1 = MonthFromKey(monthKeyStr): d2 = MonthEnd(d1)
    Set cli = DB_Get("T_CLIENTS", clientId)
    If cli Is Nothing Then Fail "Client introuvable."
    tarif = ToDbl(cli("TarifTonne"))
    If tarif <= 0 Then Fail "Tarif transport non renseigné pour le client " & cli("Code") & "."
    If DB_CountIf("T_FACTURES", "ClientID", "=", clientId, "PeriodeDebut", "<=", d2, "PeriodeFin", ">=", d1, "Statut", "<>", FAC_ANNULEE) > 0 Then
        Fail "Une facture (non annulée) existe déjà pour " & cli("Code") & " sur " & monthKeyStr & ". Annulez-la avant d'en générer une nouvelle."
    End If
    pending = DB_CountIf("T_ROTATIONS", "ClientID", "=", clientId, "Statut", "=", ST_SAISIE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    If pending > 0 And Not silent Then
        If Not UI_Confirm(pending & " rotation(s) de " & cli("Code") & " sur " & monthKeyStr & " sont encore au statut « Saisie » et ne seront PAS facturées." & vbLf & _
                          "Seules les rotations validées sont facturées. Continuer ?", "Rotations non validées", "Générer quand même") Then Fail "Génération annulée.", ERR_CANCEL
    End If
    Set rows = Bill_BillableRows(clientId, d1, d2)
    If rows.Count = 0 Then Fail "Aucune rotation validée non facturée pour " & cli("Code") & " sur " & monthKeyStr & "."
    data = DB_Data("T_ROTATIONS")
    cNet = DB_ColIdx("T_ROTATIONS", "PoidsNet"): cDest = DB_ColIdx("T_ROTATIONS", "DestinationID"): cId = DB_ColIdx("T_ROTATIONS", "ID")
    Set byDest = NewDict()
    Dim ids As New Collection
    For Each r In rows
        ton = ton + ToDbl(data(r, cNet))
        n = n + 1
        destId = ToLng(data(r, cDest))
        byDest(destId) = byDest(destId) + ToDbl(data(r, cNet))
        ids.Add ToLng(data(r, cId))
    Next r
    ton = Round(ton, 2)
    ht = Round(ton * tarif, 0)
    tva = Param_Num("TVA_TAUX", 0) / 100
    DB_BeginTx "T_FACTURES|T_FACTURE_LIGNES|T_ROTATIONS|T_SEQ|T_AUDIT"
    Set rec = NewRecord("NumFacture", "BROUILLON", "ClientID", clientId, "DateFacture", App_Today(), "PeriodeDebut", d1, "PeriodeFin", d2, _
                        "Echeance", App_Today() + ToLng(cli("DelaiPaiementJ")), "RefContrat", cli("RefContrat"), "NbRotations", n, _
                        "Tonnage", ton, "TarifTonne", tarif, "MontantHT", ht, "TauxTVA", tva, "MontantTVA", Round(ht * tva, 0), _
                        "MontantTTC", ht + Round(ht * tva, 0), "MontantPaye", 0, "Solde", ht + Round(ht * tva, 0), "Statut", FAC_BROUILLON, _
                        "Litige", "Non", "ConditionsPaiement", "Paiement à " & cli("DelaiPaiementJ") & " jours par virement", _
                        "Mentions", Param_Get("FACT_MENTIONS", ""))
    invId = DB_Insert("T_FACTURES", rec)
    DB_SetValue "T_FACTURES", invId, "NumFacture", "BROUILLON-" & invId
    For Each k In byDest.Keys
        DB_Insert "T_FACTURE_LIGNES", NewRecord("FactureID", invId, _
            "Designation", "Transport de bauxite vers " & DB_Value("T_LIEUX", k, "Nom") & " - période du " & FmtDate(d1) & " au " & FmtDate(d2), _
            "Quantite", Round(byDest(k), 2), "Unite", "t", "PU", tarif, "Montant", Round(Round(byDest(k), 2) * tarif, 0)), False
    Next k
    ' Ecart d'arrondi entre lignes et total : ajuste sur la derniere ligne
    Bill_FixRounding invId, ht
    Dim lo As ListObject, cF As Long, v As Variant, rr As Long
    Set lo = DB_LO("T_ROTATIONS")
    cF = DB_ColIdx("T_ROTATIONS", "FactureID")
    For Each v In ids
        rr = DB_RowOf("T_ROTATIONS", "ID", v)
        lo.ListRows(rr).Range.Cells(1, cF).Value = invId
    Next v
    DB_Invalidate "T_ROTATIONS"
    Log_Audit "FACTURE-BROUILLON", "T_FACTURES", invId, cli("Code") & " " & monthKeyStr & " : " & n & " rotations, " & FmtNum(ton, 2) & " t, " & FmtMoney(ht)
    DB_Commit
    Bill_GenerateDraft = invId
End Function

Private Sub Bill_FixRounding(ByVal invId As Long, ByVal ht As Double)
    Dim rows As Collection, data As Variant, s As Double, r As Variant, lastId As Long
    Set rows = DB_Rows("T_FACTURE_LIGNES", "FactureID", "=", invId)
    data = DB_Data("T_FACTURE_LIGNES")
    For Each r In rows
        s = s + ToDbl(data(r, DB_ColIdx("T_FACTURE_LIGNES", "Montant")))
        lastId = ToLng(data(r, DB_ColIdx("T_FACTURE_LIGNES", "ID")))
    Next r
    If lastId > 0 And s <> ht Then DB_SetValue "T_FACTURE_LIGNES", lastId, "Montant", ToDbl(DB_Value("T_FACTURE_LIGNES", lastId, "Montant")) + (ht - s)
End Sub

Public Sub Bill_Validate(ByVal invId As Long)
    Dim f As Object, num As String
    Sec_Require "FACTURES", "V"
    Set f = DB_Get("T_FACTURES", invId)
    If f Is Nothing Then Fail "Facture introuvable."
    If f("Statut") <> FAC_BROUILLON Then Fail "Seul un brouillon peut être validé."
    If ToDbl(f("MontantTTC")) <= 0 Then Fail "Montant nul."
    ' controle : les rotations reservees sont toujours validees
    If DB_CountIf("T_ROTATIONS", "FactureID", "=", invId, "Statut", "<>", ST_VALIDEE) > 0 Then Fail "Des rotations réservées ne sont plus validées : supprimez ce brouillon et regénérez."
    DB_BeginTx "T_FACTURES|T_SEQ|T_AUDIT"
    num = Seq_Number(ToStr(Param_Get("FACT_PREFIXE", "FAC")), ToDate(f("DateFacture")), 4)
    DB_Update "T_FACTURES", invId, NewRecord("NumFacture", num, "Statut", FAC_VALIDEE, "ValidePar", Sec_CurrentLogin(), "ValideLe", App_Today(), _
              "Empreinte", Bill_Fingerprint(num, CodeOf("T_CLIENTS", f("ClientID")), ToDbl(f("Tonnage")), ToDbl(f("MontantTTC"))))
    Log_Audit "FACTURE-VALIDATION", "T_FACTURES", invId, num & " " & FmtMoney(f("MontantTTC"))
    DB_Commit
End Sub

Public Sub Bill_Cancel(ByVal invId As Long, ByVal reason As String)
    Dim f As Object
    Sec_Require "FACTURES", "V"
    Set f = DB_Get("T_FACTURES", invId)
    If f("Statut") = FAC_ANNULEE Then Fail "Facture déjà annulée."
    If f("Statut") = FAC_BROUILLON Then Fail "Un brouillon se supprime (bouton Supprimer)."
    If DB_Sum("T_PAIEMENTS", "Montant", "FactureID", "=", invId, "Statut", "<>", "Annulé") > 0 Then _
        Fail "Cette facture a reçu des encaissements : annulez d'abord les encaissements (ou émettez un avoir hors application)."
    If Len(Trim$(reason)) = 0 Then Fail "Motif d'annulation obligatoire."
    DB_BeginTx "T_FACTURES|T_ROTATIONS|T_AUDIT|T_SEQ"
    Bill_ReleaseRotations invId
    DB_Update "T_FACTURES", invId, NewRecord("Statut", FAC_ANNULEE, "MotifAnnulation", reason, "Solde", 0)
    Log_Audit "FACTURE-ANNULATION", "T_FACTURES", invId, f("NumFacture") & " : " & reason
    DB_Commit
End Sub

Public Sub Bill_ReleaseRotations(ByVal invId As Long)
    Dim rows As Collection, r As Variant, lo As ListObject, cF As Long
    Set rows = DB_Rows("T_ROTATIONS", "FactureID", "=", invId)
    Set lo = DB_LO("T_ROTATIONS")
    cF = DB_ColIdx("T_ROTATIONS", "FactureID")
    For Each r In rows
        lo.ListRows(r).Range.Cells(1, cF).ClearContents
    Next r
    DB_Invalidate "T_ROTATIONS"
End Sub

Public Sub Bill_DeleteLines(ByVal invId As Long)
    Dim rows As Collection, i As Long, data As Variant
    Set rows = DB_Rows("T_FACTURE_LIGNES", "FactureID", "=", invId)
    data = DB_Data("T_FACTURE_LIGNES")
    For i = rows.Count To 1 Step -1
        DB_Delete "T_FACTURE_LIGNES", data(rows(i), DB_ColIdx("T_FACTURE_LIGNES", "ID"))
    Next i
End Sub

Public Sub Bill_ToggleDispute(ByVal invId As Long)
    Dim f As Object, reason As String
    Sec_Require "FACTURES", "M"
    Set f = DB_Get("T_FACTURES", invId)
    If f("Litige") = "Oui" Then
        If UI_Confirm("Lever le litige sur la facture " & f("NumFacture") & " ?", "Litige", "Lever le litige") Then
            DB_SetValue "T_FACTURES", invId, "Litige", "Non"
            Log_Audit "LITIGE-LEVE", "T_FACTURES", invId, ""
        End If
    Else
        reason = UI_AskReason("Objet du litige sur la facture " & f("NumFacture") & " :", "Déclarer un litige", "Déclarer")
        If Len(reason) = 0 Then Exit Sub
        DB_BeginTx "T_FACTURES|T_RECLAMATIONS|T_SEQ|T_AUDIT"
        DB_SetValue "T_FACTURES", invId, "Litige", "Oui"
        Rec_Save "T_RECLAMATIONS", NewRecord("DateRec", App_Today(), "Origine", "Client", "Objet", Left$(reason, 120), "FactureID", invId, _
                                             "Description", reason, "Statut", "Ouverte")
        Log_Audit "LITIGE", "T_FACTURES", invId, reason
        DB_Commit
    End If
End Sub

'------------------------------------------------------------------ Encaissements
Public Sub Bill_BeforeSavePayment(ByVal rec As Object, ByVal isNew As Boolean)
    Dim f As Object, due As Double
    Set f = DB_Get("T_FACTURES", rec("FactureID"))
    If f Is Nothing Then Fail "Facture introuvable."
    If f("Statut") = FAC_BROUILLON Or f("Statut") = FAC_ANNULEE Then Fail "Encaissement impossible sur une facture " & LCase$(f("Statut")) & "."
    If ToDbl(rec("Montant")) <= 0 Then Fail "Montant strictement positif obligatoire."
    If ToDate(rec("DatePaiement")) < ToDate(f("DateFacture")) Then Fail "Date d'encaissement antérieure à la date de facture."
    If ToDate(rec("DatePaiement")) > App_Today() Then Fail "Date d'encaissement dans le futur."
    due = ToDbl(f("MontantTTC")) - DB_Sum("T_PAIEMENTS", "Montant", "FactureID", "=", f("ID"), "Statut", "<>", "Annulé", "ID", "<>", ToLng(rec("ID")))
    If ToDbl(rec("Montant")) > due + 0.5 Then Fail "Montant supérieur au solde dû (" & FmtMoney(due) & ")."
    If Not IsBlank(rec("Reference")) Then
        If DB_CountIf("T_PAIEMENTS", "Reference", "=", rec("Reference"), "FactureID", "=", f("ID"), "Statut", "<>", "Annulé", "ID", "<>", ToLng(rec("ID"))) > 0 Then _
            Fail "Doublon : la référence « " & rec("Reference") & " » est déjà enregistrée sur cette facture."
    End If
    If isNew Then rec("Statut") = "Enregistré"
End Sub

' Recalcule montant regle, solde et statut d'une facture
Public Sub Bill_Recompute(ByVal invId As Long)
    Dim f As Object, paid As Double, ttc As Double, st As String
    Set f = DB_Get("T_FACTURES", invId)
    If f Is Nothing Then Exit Sub
    If f("Statut") = FAC_BROUILLON Or f("Statut") = FAC_ANNULEE Then Exit Sub
    paid = DB_Sum("T_PAIEMENTS", "Montant", "FactureID", "=", invId, "Statut", "<>", "Annulé")
    ttc = ToDbl(f("MontantTTC"))
    If paid >= ttc - 0.5 Then
        st = FAC_PAYEE
    ElseIf paid > 0 Then
        st = FAC_PARTIELLE
    Else
        st = FAC_VALIDEE
    End If
    DB_Update "T_FACTURES", invId, NewRecord("MontantPaye", paid, "Solde", ttc - paid, "Statut", st), "ID", False
End Sub

Public Function Bill_PaymentDialog(ByVal invId As Long) As Long
    Dim f As Object, dlg As frmInput, rec As Object
    Sec_Require "PAIEMENTS", "C"
    If invId = 0 Then
        Dim lst As Variant
        lst = Bill_OpenInvoicesList()
        If UBound(lst) < LBound(lst) Then Fail "Aucune facture en attente de paiement."
        Dim pick As String
        pick = UI_Pick("Nouvel encaissement", "Facture", lst)
        If Len(pick) = 0 Then Exit Function
        invId = IdOf("T_FACTURES", Trim$(Split(pick, "|")(0)))
    End If
    Set f = DB_Get("T_FACTURES", invId)
    If f("Statut") <> FAC_VALIDEE And f("Statut") <> FAC_PARTIELLE Then Fail "Cette facture n'attend pas de paiement (statut : " & f("Statut") & ")."
    Set dlg = Dlg_New("Encaissement - " & f("NumFacture"), CodeOf("T_CLIENTS", f("ClientID")) & " | TTC " & FmtMoney(f("MontantTTC")) & _
                      " | solde dû " & FmtMoney(f("Solde")), CLR_PURPLE(), 560)
    dlg.AddField "D", "Date d'encaissement", "DATE", App_Today(), , True
    dlg.AddField "M", "Montant (GNF)", "MONEY", Format$(f("Solde"), "0"), , True, "Paiement partiel autorisé ; jamais supérieur au solde dû"
    dlg.AddField "MODE", "Mode", "LIST", "Virement", Array("Virement", "Chèque", "Espèces", "Compensation"), True
    dlg.AddField "REF", "Référence bancaire", "TEXT", "", , True
    dlg.AddField "OBS", "Observations", "MEMO", ""
    dlg.AddAction "NO", "Annuler", CLR_GREY(), False, True
    dlg.AddAction "OK", "Enregistrer l'encaissement", CLR_PURPLE(), True
    If dlg.ShowDialog = "OK" Then
        Set rec = NewRecord("FactureID", invId, "DatePaiement", dlg.FieldValue("D"), "Montant", dlg.FieldValue("M"), _
                            "ModePaiement", dlg.FieldValue("MODE"), "Reference", dlg.FieldValue("REF"), "Observations", dlg.FieldValue("OBS"))
        dlg.Dispose
        Bill_PaymentDialog = Rec_Save("T_PAIEMENTS", rec)
    Else
        dlg.Dispose
    End If
End Function

Public Function Bill_OpenInvoicesList() As Variant
    Dim rows As Collection, r As Variant, data As Variant, out() As String, i As Long
    Set rows = DB_Rows("T_FACTURES", "Statut", "IN", FAC_VALIDEE & "|" & FAC_PARTIELLE)
    If rows.Count = 0 Then Bill_OpenInvoicesList = Array(): Exit Function
    data = DB_Data("T_FACTURES")
    ReDim out(0 To rows.Count - 1)
    For Each r In rows
        out(i) = data(r, DB_ColIdx("T_FACTURES", "NumFacture")) & " | " & CodeOf("T_CLIENTS", data(r, DB_ColIdx("T_FACTURES", "ClientID"))) & _
                 " | solde " & FmtMoney(data(r, DB_ColIdx("T_FACTURES", "Solde")))
        i = i + 1
    Next r
    Bill_OpenInvoicesList = out
End Function

Public Sub Bill_CancelPayment(ByVal payId As Long, ByVal reason As String)
    Dim p As Object
    Sec_Require "PAIEMENTS", "V"
    Set p = DB_Get("T_PAIEMENTS", payId)
    If p("Statut") = "Annulé" Then Fail "Encaissement déjà annulé."
    If Len(Trim$(reason)) = 0 Then Fail "Motif obligatoire."
    DB_BeginTx "T_PAIEMENTS|T_FACTURES|T_AUDIT"
    DB_Update "T_PAIEMENTS", payId, NewRecord("Statut", "Annulé", "Observations", Trim$(ToStr(p("Observations")) & " [Annulé : " & reason & "]"))
    Bill_Recompute ToLng(p("FactureID"))
    Log_Audit "ENCAISSEMENT-ANNULE", "T_PAIEMENTS", payId, FmtMoney(p("Montant")) & " : " & reason
    DB_Commit
End Sub

Public Sub Bill_BeforeSaveReminder(ByVal rec As Object)
    Dim f As Object
    Set f = DB_Get("T_FACTURES", rec("FactureID"))
    If ToDbl(f("Solde")) <= 0 Then Fail "Facture soldée : relance inutile."
End Sub

'------------------------------------------------------------------ Creances
Public Function Bill_Receivables(Optional ByVal asOf As Date = 0, Optional ByVal clientId As Long = 0) As Double
    If clientId = 0 Then
        Bill_Receivables = DB_Sum("T_FACTURES", "Solde", "Statut", "IN", FAC_VALIDEE & "|" & FAC_PARTIELLE)
    Else
        Bill_Receivables = DB_Sum("T_FACTURES", "Solde", "Statut", "IN", FAC_VALIDEE & "|" & FAC_PARTIELLE, "ClientID", "=", clientId)
    End If
End Function

Public Function Bill_Overdue(Optional ByVal asOf As Date = 0) As Collection
    If asOf = 0 Then asOf = App_Today()
    Set Bill_Overdue = DB_Rows("T_FACTURES", "Statut", "IN", FAC_VALIDEE & "|" & FAC_PARTIELLE, "Echeance", "<", asOf)
End Function

Public Function Bill_Collected(ByVal d1 As Date, ByVal d2 As Date) As Double
    Bill_Collected = DB_Sum("T_PAIEMENTS", "Montant", "Statut", "<>", "Annulé", "DatePaiement", ">=", d1, "DatePaiement", "<=", d2)
End Function

Public Function Bill_Invoiced(ByVal d1 As Date, ByVal d2 As Date) As Double
    Bill_Invoiced = DB_Sum("T_FACTURES", "MontantHT", "Statut", "IN", FAC_VALIDEE & "|" & FAC_PARTIELLE & "|" & FAC_PAYEE, "PeriodeDebut", ">=", d1, "PeriodeDebut", "<=", d2)
End Function

' Niveau de relance suggere selon le retard
Public Function Bill_SuggestedLevel(ByVal daysLate As Long) As String
    If daysLate >= Param_Num("RELANCE_J3", 45) Then
        Bill_SuggestedLevel = "3 - Mise en demeure"
    ElseIf daysLate >= Param_Num("RELANCE_J2", 21) Then
        Bill_SuggestedLevel = "2 - Relance"
    ElseIf daysLate >= Param_Num("RELANCE_J1", 7) Then
        Bill_SuggestedLevel = "1 - Rappel"
    End If
End Function

' Generation de brouillons pour tous les clients actifs d'un mois
Public Function Bill_GenerateMonthDialog() As Long
    Dim mk As String, f As frmInput, clients As Variant, c As String, n As Long, cid As Long, i As Long, errs As String
    If Not UI_AskMonth(mk, "Mois à facturer") Then Exit Function
    clients = Schema_FKChoices(Schema_Field("T_FACTURES", "ClientID"))
    c = UI_Pick("Génération de factures " & mk, "Client", Array_Prepend(clients, "(Tous les clients)"), "(Tous les clients)")
    If Len(c) = 0 Then Exit Function
    For i = LBound(clients) To UBound(clients)
        If c = "(Tous les clients)" Or c = clients(i) Then
            cid = IdOf("T_CLIENTS", CStr(clients(i)))
            On Error Resume Next
            Bill_GenerateDraft cid, mk, (c = "(Tous les clients)")
            If Err.Number <> 0 Then
                errs = errs & clients(i) & " : " & Err.Description & vbLf
                Err.Clear
            Else
                n = n + 1
            End If
            On Error GoTo 0
        End If
    Next i
    If n > 0 Then
        UI_Alert n & " brouillon(s) de facture généré(s) pour " & mk & "." & vbLf & "Vérifiez-les (Aperçu) puis validez-les pour leur attribuer un numéro légal." & _
                 IIf(Len(errs) > 0, vbLf & vbLf & "Non générées :" & vbLf & errs, ""), "success", "Facturation"
    Else
        UI_Alert "Aucune facture générée." & vbLf & errs, "warning", "Facturation"
    End If
    Bill_GenerateMonthDialog = n
End Function

Public Function Array_Prepend(ByVal arr As Variant, ByVal first As String) As Variant
    Dim out() As String, i As Long, n As Long
    n = 0
    If IsArray(arr) Then
        If UBound(arr) >= LBound(arr) Then n = UBound(arr) - LBound(arr) + 1
    End If
    ReDim out(0 To n)
    out(0) = first
    For i = 1 To n
        out(i) = arr(LBound(arr) + i - 1)
    Next i
    Array_Prepend = out
End Function
