Attribute VB_Name = "mBiz_Fuel"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Fuel
' Carburant : approvisionnements de la cuve, distributions aux camions,
' detection d'anomalies (surconsommation, doublons, quantites aberrantes,
' compteur incoherent, stock cuve insuffisant), stock theorique de la cuve.
'
' Charge d'exploitation = distributions (consommation). Les approvisionnements
' alimentent le stock : ils ne sont PAS comptes en charge (pas de double compte).
'==============================================================================
Option Explicit

Public Sub Fuel_BeforeSave(ByVal rec As Object, ByVal isNew As Boolean)
    Dim typ As String, cam As Long, liters As Double, d As Date, prev As Object, anomalies As String
    Dim ref As Double, tol As Double, hrs As Double, conso As Double
    typ = ToStr(rec("TypeOp"))
    liters = ToDbl(rec("Litres"))
    d = ToDate(rec("DateOp"))
    If liters <= 0 Then Fail "La quantité doit être strictement positive."
    If ToDbl(rec("PrixLitre")) <= 0 Then Fail "Le prix unitaire doit être renseigné."
    If d > App_Today() Then Fail "Date future interdite."
    rec("Montant") = Round(liters * ToDbl(rec("PrixLitre")), 0)
    rec("NumBon") = UCase$(Trim$(ToStr(rec("NumBon"))))
    If typ = "Approvisionnement" Then
        If IsBlank(rec("FournisseurID")) Then Fail "Un approvisionnement doit préciser le fournisseur."
        rec("CamionID") = Empty: rec("ChauffeurID") = Empty
        rec("HeuresDepuisPlein") = Empty: rec("ConsoLH") = Empty
        rec("Anomalie") = ""
    Else
        cam = ToLng(rec("CamionID"))
        If cam = 0 Then Fail "Une distribution doit préciser le camion."
        If IsBlank(rec("ChauffeurID")) Then rec("ChauffeurID") = Fleet_DriverFor(cam, d)
        If ToLng(rec("ChauffeurID")) = 0 Then rec("ChauffeurID") = Empty
        ' --- analyse ---
        If liters > Param_Num("CARB_MAX_LITRES", 600) Then anomalies = anomalies & "Quantité > " & Param_Num("CARB_MAX_LITRES", 600) & " L ; "
        Set prev = Fuel_PreviousFill(cam, d, ToDbl(rec("CompteurH")), ToLng(rec("ID")))
        If Not IsBlank(rec("CompteurH")) Then
            If Not prev Is Nothing Then
                If ToDbl(rec("CompteurH")) < ToDbl(prev("CompteurH")) Then
                    Fail "Compteur incohérent : " & FmtNum(rec("CompteurH"), 1) & " h est inférieur au relevé précédent (" & _
                         FmtNum(prev("CompteurH"), 1) & " h le " & FmtDate(prev("DateOp")) & ")."
                End If
                hrs = Round(ToDbl(rec("CompteurH")) - ToDbl(prev("CompteurH")), 1)
                rec("HeuresDepuisPlein") = hrs
                If hrs > 0 Then
                    conso = Round(liters / hrs, 2)
                    rec("ConsoLH") = conso
                    ref = ToDbl(DB_Value("T_CAMIONS", cam, "ConsoRefLH"))
                    tol = Param_Num("CARB_TOLERANCE_PCT", 25) / 100
                    If ref > 0 And conso > ref * (1 + tol) Then anomalies = anomalies & "Surconsommation " & FmtPct(conso / ref - 1, 0) & " vs référence ; "
                    If hrs < Param_Num("CARB_DOUBLON_HEURES", 6) Then anomalies = anomalies & "Doublon potentiel (" & FmtNum(hrs, 1) & " h depuis le dernier plein) ; "
                Else
                    anomalies = anomalies & "Doublon potentiel (compteur identique) ; "
                End If
            End If
        Else
            anomalies = anomalies & "Compteur non renseigné ; "
        End If
        If DB_CountIf("T_CARBURANT", "CamionID", "=", cam, "DateOp", "=", d, "TypeOp", "=", "Distribution", _
                      "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "ID", "<>", ToLng(rec("ID"))) > 0 Then
            If InStr(anomalies, "Doublon") = 0 Then anomalies = anomalies & "Deuxième plein le même jour ; "
        End If
        If isNew And liters > Fuel_TankStock() Then anomalies = anomalies & "Stock cuve théorique insuffisant ; "
        If Len(anomalies) > 2 Then anomalies = Left$(anomalies, Len(anomalies) - 3)
        rec("Anomalie") = anomalies
    End If
    If isNew Then rec("Statut") = ST_SAISIE
End Sub

' Plein precedent (meme camion) selon le compteur ou la date
Public Function Fuel_PreviousFill(ByVal cam As Long, ByVal d As Date, ByVal counter As Double, ByVal excludeId As Long) As Object
    Dim rows As Collection, r As Variant, data As Variant, best As Long, bestC As Double, c As Double, cC As Long, cD As Long
    Set rows = DB_Rows("T_CARBURANT", "CamionID", "=", cam, "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateOp", "<=", d)
    data = DB_Data("T_CARBURANT")
    cC = DB_ColIdx("T_CARBURANT", "CompteurH"): cD = DB_ColIdx("T_CARBURANT", "DateOp")
    bestC = -1
    For Each r In rows
        If ToLng(data(r, DB_ColIdx("T_CARBURANT", "ID"))) <> excludeId And Not IsBlank(data(r, cC)) Then
            c = ToDbl(data(r, cC))
            If (counter = 0 Or c <= counter Or ToDate(data(r, cD)) < d) And c > bestC Then
                bestC = c: best = r
            End If
        End If
    Next r
    If best > 0 Then Set Fuel_PreviousFill = DB_RowDict("T_CARBURANT", best)
End Function

' Stock theorique de la cuve = approvisionnements - distributions (hors rejets / annulations)
Public Function Fuel_TankStock() As Double
    Fuel_TankStock = DB_Sum("T_CARBURANT", "Litres", "TypeOp", "=", "Approvisionnement", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE) - _
                     DB_Sum("T_CARBURANT", "Litres", "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)
End Function

Public Sub Fuel_RefreshTank()
    Param_Set "CARB_STOCK_CUVE", CStr(Round(Fuel_TankStock(), 0)), "Stock théorique de la cuve (L)", "Carburant", "Nombre"
End Sub

' Re-analyse toutes les distributions d'une periode (apres changement de parametres)
Public Function Fuel_Reanalyze(ByVal d1 As Date, ByVal d2 As Date) As Long
    Dim rows As Collection, r As Variant, data As Variant, rec As Object, ids As New Collection, v As Variant, before As String, n As Long
    Set rows = DB_Rows("T_CARBURANT", "TypeOp", "=", "Distribution", "DateOp", ">=", d1, "DateOp", "<=", d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)
    data = DB_Data("T_CARBURANT")
    For Each r In rows
        ids.Add ToLng(data(r, DB_ColIdx("T_CARBURANT", "ID")))
    Next r
    For Each v In ids
        Set rec = DB_Get("T_CARBURANT", v)
        before = ToStr(rec("Anomalie"))
        Fuel_BeforeSave rec, False
        If ToStr(rec("Anomalie")) <> before Or ToDbl(rec("ConsoLH")) <> ToDbl(DB_Value("T_CARBURANT", v, "ConsoLH")) Then
            DB_Update "T_CARBURANT", v, NewRecord("Anomalie", rec("Anomalie"), "ConsoLH", rec("ConsoLH"), "HeuresDepuisPlein", rec("HeuresDepuisPlein")), "ID", False
        End If
        If Len(ToStr(rec("Anomalie"))) > 0 Then n = n + 1
    Next v
    Log_Audit "ANALYSE-CARBURANT", "T_CARBURANT", "", "Période " & FmtDate(d1) & " - " & FmtDate(d2) & " : " & n & " anomalie(s)"
    Fuel_Reanalyze = n
End Function

Public Function Fuel_Cost(ByVal d1 As Date, ByVal d2 As Date, Optional ByVal truckId As Long = 0) As Double
    If truckId = 0 Then
        Fuel_Cost = DB_Sum("T_CARBURANT", "Montant", "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateOp", ">=", d1, "DateOp", "<=", d2)
    Else
        Fuel_Cost = DB_Sum("T_CARBURANT", "Montant", "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateOp", ">=", d1, "DateOp", "<=", d2, "CamionID", "=", truckId)
    End If
End Function

Public Function Fuel_Liters(ByVal d1 As Date, ByVal d2 As Date) As Double
    Fuel_Liters = DB_Sum("T_CARBURANT", "Litres", "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateOp", ">=", d1, "DateOp", "<=", d2)
End Function
