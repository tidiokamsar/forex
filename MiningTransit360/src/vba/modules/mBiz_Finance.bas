Attribute VB_Name = "mBiz_Finance"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Finance
' Depenses, caisse, avances, cout du personnel.
'
' Anti double comptabilisation :
'  - carburant, pneumatiques et location ont leurs modules dedies : une depense
'    a la charge de l'exploitant dans ces categories est refusee ;
'  - doublon fournisseur + reference facture refuse ;
'  - doublon date + montant + libelle refuse ;
'  - les depenses "Atiko (contrat)" ou "Refacturable client" sont tracees mais
'    exclues de la marge.
'==============================================================================
Option Explicit

Public Sub Fin_BeforeSaveExpense(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    Dim cat As String, myId As Long
    cat = ToStr(rec("Categorie"))
    myId = ToLng(rec("ID"))
    If ToDbl(rec("Montant")) <= 0 Then Fail "Montant strictement positif obligatoire."
    If ToDate(rec("DateDep")) > App_Today() Then Fail "Date de dépense dans le futur."
    If InList(cat, "Carburant|Pneumatiques|Location camions") And ToStr(rec("Responsable")) = RESP_EXPL Then
        Fail "Les dépenses « " & cat & " » à la charge de l'exploitant se saisissent dans le module dédié " & _
             "(Carburant, Pneumatiques ou Location horaire) afin d'éviter une double comptabilisation."
    End If
    If Not IsBlank(rec("RefFacture")) And Not IsBlank(rec("FournisseurID")) Then
        If DB_CountIf("T_DEPENSES", "FournisseurID", "=", ToLng(rec("FournisseurID")), "RefFacture", "=", ToStr(rec("RefFacture")), _
                      "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "ID", "<>", myId) > 0 Then
            Fail "Doublon : la facture fournisseur « " & rec("RefFacture") & " » de " & CodeOf("T_FOURNISSEURS", rec("FournisseurID")) & " est déjà enregistrée."
        End If
    End If
    If DB_CountIf("T_DEPENSES", "DateDep", "=", ToDate(rec("DateDep")), "Montant", "=", ToDbl(rec("Montant")), "Libelle", "=", ToStr(rec("Libelle")), _
                  "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "ID", "<>", myId) > 0 Then
        Fail "Doublon probable : une dépense identique (date, montant, libellé) existe déjà."
    End If
    If isNew Then rec("Statut") = ST_SAISIE
    If Not old Is Nothing Then
        If old("Statut") = ST_VALIDEE Then rec("Statut") = ST_SAISIE   ' revalidation requise
        If old("Statut") = ST_REJETEE Then rec("Statut") = ST_SAISIE
    End If
End Sub

' Depenses imputables a l'exploitant (marge) : validees, responsable Exploitant
Public Function Fin_Expenses(ByVal d1 As Date, ByVal d2 As Date, Optional ByVal exceptional As String = "Non") As Double
    Fin_Expenses = DB_Sum("T_DEPENSES", "Montant", "Statut", "=", ST_VALIDEE, "Responsable", "=", RESP_EXPL, _
                          "Exceptionnelle", "=", exceptional, "DateDep", ">=", d1, "DateDep", "<=", d2)
End Function

Public Function Fin_AtikoCharges(ByVal d1 As Date, ByVal d2 As Date) As Double
    Fin_AtikoCharges = DB_Sum("T_DEPENSES", "Montant", "Statut", "=", ST_VALIDEE, "Responsable", "=", RESP_ATIKO, "DateDep", ">=", d1, "DateDep", "<=", d2) + _
                       DB_Sum("T_MAINTENANCE", "Cout", "Responsable", "=", RESP_ATIKO, "DateOT", ">=", d1, "DateOT", "<=", d2, "Statut", "<>", "Annulée")
End Function

' Cout du personnel sur une periode : salaires charges (prorata calendaire) + primes par rotation validee
Public Function Fin_PersonnelCost(ByVal d1 As Date, ByVal d2 As Date) As Double
    Dim m As Date, total As Double, days As Long, dim1 As Long, a As Date, b As Date, rate As Double
    rate = 1 + Param_Num("CHARGES_SOCIALES_PCT", 18) / 100
    m = MonthStart(d1)
    Do While m <= d2
        a = m: b = MonthEnd(m)
        If a < d1 Then a = d1
        If b > d2 Then b = d2
        days = b - a + 1
        dim1 = Day(MonthEnd(m))
        total = total + (MonthlyPayroll(m) * rate) * days / dim1
        m = DateSerial(Year(m), Month(m) + 1, 1)
    Loop
    total = total + RotationBonuses(d1, d2)
    Fin_PersonnelCost = Round(total, 0)
End Function

Private Function MonthlyPayroll(ByVal m As Date) As Double
    Dim data As Variant, r As Long, t As Double, hire As Date
    data = DB_Data("T_CHAUFFEURS")
    For r = 1 To ArrayRows(data)
        hire = ToDate(data(r, DB_ColIdx("T_CHAUFFEURS", "DateEmbauche")))
        If ToStr(data(r, DB_ColIdx("T_CHAUFFEURS", "Statut"))) <> "Sorti" And (hire = 0 Or hire <= MonthEnd(m)) Then
            t = t + ToDbl(data(r, DB_ColIdx("T_CHAUFFEURS", "SalaireMensuel")))
        End If
    Next r
    data = DB_Data("T_PERSONNEL")
    For r = 1 To ArrayRows(data)
        hire = ToDate(data(r, DB_ColIdx("T_PERSONNEL", "DateEmbauche")))
        If ToStr(data(r, DB_ColIdx("T_PERSONNEL", "Statut"))) <> "Sorti" And (hire = 0 Or hire <= MonthEnd(m)) Then
            t = t + ToDbl(data(r, DB_ColIdx("T_PERSONNEL", "SalaireMensuel")))
        End If
    Next r
    MonthlyPayroll = t
End Function

Private Function RotationBonuses(ByVal d1 As Date, ByVal d2 As Date) As Double
    Dim data As Variant, r As Long, t As Double, primes As Object, ch As Variant
    data = DB_Data("T_ROTATIONS")
    If IsEmpty(data) Then Exit Function
    Set primes = NewDict()
    For r = 1 To UBound(data, 1)
        If data(r, DB_ColIdx("T_ROTATIONS", "Statut")) = ST_VALIDEE Then
            If ToDate(data(r, DB_ColIdx("T_ROTATIONS", "DateRotation"))) >= d1 And ToDate(data(r, DB_ColIdx("T_ROTATIONS", "DateRotation"))) <= d2 Then
                ch = ToLng(data(r, DB_ColIdx("T_ROTATIONS", "ChauffeurID")))
                If Not primes.Exists(ch) Then primes(ch) = ToDbl(DB_Value("T_CHAUFFEURS", ch, "PrimeRotation"))
                t = t + primes(ch)
            End If
        End If
    Next r
    RotationBonuses = t
End Function

'------------------------------------------------------------------ Caisse
Public Function Fin_CashBalance(Optional ByVal excludeId As Long = 0) As Double
    Fin_CashBalance = DB_Sum("T_CAISSE", "Montant", "Sens", "=", "Entrée", "ID", "<>", excludeId) - _
                      DB_Sum("T_CAISSE", "Montant", "Sens", "=", "Sortie", "ID", "<>", excludeId)
End Function

Public Sub Fin_BeforeSaveCash(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    If ToDbl(rec("Montant")) <= 0 Then Fail "Montant strictement positif obligatoire."
    If ToStr(rec("Sens")) = "Sortie" Then
        If ToDbl(rec("Montant")) > Fin_CashBalance(ToLng(rec("ID"))) Then
            Fail "Solde de caisse insuffisant : " & FmtMoney(Fin_CashBalance(ToLng(rec("ID")))) & " disponible."
        End If
    End If
End Sub

' Recalcule les soldes progressifs de la caisse (ordre chronologique)
Public Sub Fin_RecomputeCash()
    Dim lo As ListObject, data As Variant, n As Long, i As Long, idx() As Variant, solde As Double, cD As Long, cS As Long, cM As Long, cSo As Long, cId As Long
    data = DB_Data("T_CAISSE")
    n = ArrayRows(data)
    If n = 0 Then Exit Sub
    cD = DB_ColIdx("T_CAISSE", "DateOp"): cS = DB_ColIdx("T_CAISSE", "Sens"): cM = DB_ColIdx("T_CAISSE", "Montant")
    cSo = DB_ColIdx("T_CAISSE", "SoldeApres"): cId = DB_ColIdx("T_CAISSE", "ID")
    ReDim idx(1 To n, 1 To 2)
    For i = 1 To n
        idx(i, 1) = CDbl(ToDate(data(i, cD))) + ToLng(data(i, cId)) / 1000000#
        idx(i, 2) = i
    Next i
    SortArray2D idx, 1
    For i = 1 To n
        If data(idx(i, 2), cS) = "Entrée" Then solde = solde + ToDbl(data(idx(i, 2), cM)) Else solde = solde - ToDbl(data(idx(i, 2), cM))
        data(idx(i, 2), cSo) = solde
    Next i
    Dim col() As Variant
    ReDim col(1 To n, 1 To 1)
    For i = 1 To n
        col(i, 1) = data(i, cSo)
    Next i
    Set lo = DB_LO("T_CAISSE")
    lo.ListColumns(cSo).DataBodyRange.Value = col
    DB_Invalidate "T_CAISSE"
End Sub

'------------------------------------------------------------------ Avances
Public Sub Fin_BeforeSaveAdvance(ByVal rec As Object, ByVal isNew As Boolean)
    If ToDbl(rec("Montant")) <= 0 Then Fail "Montant de l'avance obligatoire."
    If ToDbl(rec("MontantRembourse")) > ToDbl(rec("Montant")) Then Fail "Le remboursement dépasse le montant de l'avance."
    If DB_RowOf("T_CHAUFFEURS", "Matricule", rec("Beneficiaire")) = 0 And DB_RowOf("T_PERSONNEL", "Matricule", rec("Beneficiaire")) = 0 Then
        Fail "Bénéficiaire inconnu : saisissez le matricule d'un chauffeur ou d'un membre du personnel."
    End If
    rec("Solde") = ToDbl(rec("Montant")) - ToDbl(rec("MontantRembourse"))
    If ToStr(rec("Statut")) <> ST_ANNULEE Then
        If rec("Solde") <= 0 Then rec("Statut") = "Soldée" Else rec("Statut") = "En cours"
    End If
End Sub

Public Sub Fin_AdvanceRefund(ByVal id As Long)
    Dim a As Object, f As frmInput, amt As Double
    Set a = DB_Get("T_AVANCES", id)
    If a("Statut") <> "En cours" Then Fail "Avance non en cours."
    Set f = Dlg_New("Remboursement d'avance", a("Beneficiaire") & " - reste dû : " & FmtMoney(a("Solde")), CLR_GREEN())
    f.AddField "M", "Montant remboursé (GNF)", "MONEY", ToStr(a("Solde")), , True
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "OK", "Enregistrer", CLR_GREEN(), True
    If f.ShowDialog = "OK" Then
        amt = f.FieldValue("M")
        f.Dispose
        If amt <= 0 Then Fail "Montant invalide."
        Rec_Save "T_AVANCES", NewRecord("MontantRembourse", ToDbl(a("MontantRembourse")) + amt), id
    Else
        f.Dispose
    End If
End Sub
