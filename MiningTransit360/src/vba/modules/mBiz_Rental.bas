Attribute VB_Name = "mBiz_Rental"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Rental
' Location horaire des camions Atiko.
'
' Heures retenues = heures brutes
'                   - heures d'attente           (si LOC_DEDUIRE_ATTENTE = Oui)
'                   - immobilisation Atiko       (si LOC_DEDUIRE_IMMOB_ATIKO = Oui)
'                   - immobilisation exploitant  (si LOC_DEDUIRE_IMMOB_EXPLOITANT = Oui)
'                   puis plancher (minimum journalier) et plafond.
' Aucune exoneration n'est supposee : chaque deduction est un parametre
' contractuel explicite. Le tarif est fige sur le releve (historique).
'==============================================================================
Option Explicit

Public Function Rent_Compute(ByVal rec As Object) As Object
    Dim out As Object, brut As Double, att As Double, imA As Double, imE As Double, ret As Double
    Dim minH As Double, maxH As Double, tarif As Double, cam As Long
    Set out = NewDict()
    cam = ToLng(rec("CamionID"))
    brut = ShiftHours(TimeOr0(rec("HDebut")), TimeOr0(rec("HFin")))
    att = ToDbl(rec("HeuresAttente")): imA = ToDbl(rec("HeuresImmobAtiko")): imE = ToDbl(rec("HeuresImmobExploitant"))
    ret = brut
    If Param_Bool("LOC_DEDUIRE_ATTENTE", False) Then ret = ret - att
    If Param_Bool("LOC_DEDUIRE_IMMOB_ATIKO", True) Then ret = ret - imA
    If Param_Bool("LOC_DEDUIRE_IMMOB_EXPLOITANT", False) Then ret = ret - imE
    If ret < 0 Then ret = 0
    ' minimum journalier : uniquement si le camion n'est pas immobilise du fait d'Atiko
    minH = 0
    If cam > 0 Then minH = ToDbl(DB_Value("T_CAMIONS", cam, "HeuresMinJour"))
    If minH = 0 Then minH = Param_Num("LOC_HEURES_MIN_JOUR", 0)
    If minH > 0 And imA = 0 And ret < minH Then ret = minH
    maxH = Param_Num("LOC_HEURES_MAX_JOUR", 24)
    If ret > maxH Then ret = maxH
    If Not IsBlank(rec("TarifHoraire")) And ToDbl(rec("TarifHoraire")) > 0 Then
        tarif = ToDbl(rec("TarifHoraire"))
    ElseIf cam > 0 Then
        tarif = ToDbl(DB_Value("T_CAMIONS", cam, "TarifHoraire"))
    End If
    out("HeuresBrutes") = brut
    out("HeuresRetenues") = Round(ret, 2)
    out("TarifHoraire") = tarif
    out("Montant") = Round(ret * tarif, 0)
    Set Rent_Compute = out
End Function

Public Sub Rent_BeforeSave(ByVal rec As Object, ByVal isNew As Boolean)
    Dim calc As Object, cam As Long, d As Date
    cam = ToLng(rec("CamionID")): d = ToDate(rec("DateReleve"))
    If ToTime(rec("HDebut")) < 0 Or ToTime(rec("HFin")) < 0 Then Fail "Heures de début et de fin obligatoires (hh:mm)."
    If d > App_Today() Then Fail "Relevé daté dans le futur."
    If DB_CountIf("T_LOCATION", "CamionID", "=", cam, "DateReleve", "=", d, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "ID", "<>", ToLng(rec("ID"))) > 0 Then _
        Fail "Un relevé existe déjà pour le camion " & CodeOf("T_CAMIONS", cam) & " le " & FmtDate(d) & " (doublon)."
    If isNew Then rec("TarifHoraire") = Empty       ' tarif en vigueur fige a la creation
    Set calc = Rent_Compute(rec)
    If ToDbl(rec("HeuresAttente")) + ToDbl(rec("HeuresImmobAtiko")) + ToDbl(rec("HeuresImmobExploitant")) > calc("HeuresBrutes") + 0.001 Then _
        Fail "Attente + immobilisations (" & FmtNum(ToDbl(rec("HeuresAttente")) + ToDbl(rec("HeuresImmobAtiko")) + ToDbl(rec("HeuresImmobExploitant")), 2) & _
             " h) dépassent les heures brutes (" & FmtNum(calc("HeuresBrutes"), 2) & " h)."
    If calc("TarifHoraire") <= 0 Then Fail "Tarif horaire du camion non renseigné."
    rec("HeuresBrutes") = calc("HeuresBrutes")
    rec("HeuresRetenues") = calc("HeuresRetenues")
    rec("TarifHoraire") = calc("TarifHoraire")
    rec("Montant") = calc("Montant")
    If isNew Then rec("Statut") = ST_SAISIE
End Sub

' Heures d'immobilisation (Atiko / exploitant) d'un camion sur un poste [d+hStart, d+hEnd]
Public Sub Rent_ImmobHours(ByVal cam As Long, ByVal d As Date, ByVal hStart As Double, ByVal hEnd As Double, _
                           ByRef hAtiko As Double, ByRef hExpl As Double)
    Dim rows As Collection, r As Variant, data As Variant, s As Double, e As Double, a As Double, b As Double, ov As Double, resp As String
    hAtiko = 0: hExpl = 0
    Set rows = DB_Rows("T_INCIDENTS", "CamionID", "=", cam, "Immobilisation", "=", "Oui", "DebutImmob", "<=", d)
    data = DB_Data("T_INCIDENTS")
    a = CDbl(d) + hStart: b = CDbl(d) + hEnd
    If b <= a Then b = b + 1
    For Each r In rows
        s = ToDate(data(r, DB_ColIdx("T_INCIDENTS", "DebutImmob"))) + TimeOr0(data(r, DB_ColIdx("T_INCIDENTS", "HeureDebutImmob")))
        If IsBlank(data(r, DB_ColIdx("T_INCIDENTS", "DateReprise"))) Then
            e = CDbl(App_Today()) + 1
        Else
            e = ToDate(data(r, DB_ColIdx("T_INCIDENTS", "DateReprise"))) + TimeOr0(data(r, DB_ColIdx("T_INCIDENTS", "HeureReprise")))
        End If
        ov = Application.WorksheetFunction.Max(0, Application.WorksheetFunction.Min(b, e) - Application.WorksheetFunction.Max(a, s)) * 24
        resp = ToStr(data(r, DB_ColIdx("T_INCIDENTS", "Responsable")))
        If resp = RESP_ATIKO Then
            hAtiko = hAtiko + ov
        ElseIf resp = RESP_EXPL Then
            hExpl = hExpl + ov
        End If
    Next r
    hAtiko = Round(Application.WorksheetFunction.Min(hAtiko, (b - a) * 24), 2)
    hExpl = Round(Application.WorksheetFunction.Min(hExpl, (b - a) * 24 - hAtiko), 2)
End Sub

' Genere les releves manquants d'une periode a partir des affectations et des incidents
Public Function Rent_Generate(ByVal d1 As Date, ByVal d2 As Date) As Long
    Dim d As Date, data As Variant, r As Long, cam As Long, n As Long, rec As Object, hA As Double, hE As Double
    Dim hS As Double, hF As Double
    Sec_Require "LOCATION", "C"
    hS = TimeSerial(6, 30, 0): hF = TimeSerial(18, 30, 0)
    data = DB_Data("T_CAMIONS")
    For d = d1 To d2
        If Not Rec_IsDayClosed(d) Then
            For r = 1 To ArrayRows(data)
                cam = ToLng(data(r, DB_ColIdx("T_CAMIONS", "ID")))
                If ToStr(data(r, DB_ColIdx("T_CAMIONS", "Actif"))) <> "Non" Then
                    If DB_CountIf("T_LOCATION", "CamionID", "=", cam, "DateReleve", "=", d, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE) = 0 Then
                        If Fleet_ActiveAssignment(cam, d) > 0 Or DB_CountIf("T_ROTATIONS", "CamionID", "=", cam, "DateRotation", "=", d) > 0 _
                           Or ToStr(data(r, DB_ColIdx("T_CAMIONS", "Statut"))) = CAM_PANNE Or ToStr(data(r, DB_ColIdx("T_CAMIONS", "Statut"))) = CAM_IMMO Then
                            If Weekday(d, vbMonday) <> 7 Or DB_CountIf("T_ROTATIONS", "CamionID", "=", cam, "DateRotation", "=", d) > 0 Then
                                Rent_ImmobHours cam, d, hS, hF, hA, hE
                                Set rec = NewRecord("DateReleve", d, "CamionID", cam, "HDebut", hS, "HFin", hF, "HeuresAttente", 0, _
                                                    "HeuresImmobAtiko", hA, "HeuresImmobExploitant", hE, _
                                                    "Observations", "Relevé généré automatiquement" & IIf(hA + hE > 0, " (immobilisation reprise des incidents)", ""))
                                Rec_Save "T_LOCATION", rec
                                n = n + 1
                            End If
                        End If
                    End If
                End If
            Next r
        End If
    Next d
    Rent_Generate = n
End Function

Public Function Rent_Cost(ByVal d1 As Date, ByVal d2 As Date, Optional ByVal truckId As Long = 0) As Double
    If truckId = 0 Then
        Rent_Cost = DB_Sum("T_LOCATION", "Montant", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateReleve", ">=", d1, "DateReleve", "<=", d2)
    Else
        Rent_Cost = DB_Sum("T_LOCATION", "Montant", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateReleve", ">=", d1, "DateReleve", "<=", d2, "CamionID", "=", truckId)
    End If
End Function

Public Function Rent_Hours(ByVal d1 As Date, ByVal d2 As Date, Optional ByVal col As String = "HeuresRetenues") As Double
    Rent_Hours = DB_Sum("T_LOCATION", col, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateReleve", ">=", d1, "DateReleve", "<=", d2)
End Function
