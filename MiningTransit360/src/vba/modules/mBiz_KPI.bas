Attribute VB_Name = "mBiz_KPI"
'==============================================================================
' MINING TRANSIT 360 - mBiz_KPI
' Indicateurs de pilotage et marge operationnelle estimee.
'
' DEFINITIONS (documentees dans docs/04_REGLES_DE_GESTION.md) :
'  CA reconnu        = tonnage VALIDE de la periode x tarif client (production valorisee)
'  CA facture        = montant HT des factures validees dont la periode demarre dans l'intervalle
'  Encaissements     = paiements non annules dates dans la periode
'  Creances          = soldes des factures validees / partielles (a date)
'  Location          = releves horaires (saisis + valides) de la periode
'  Carburant         = distributions (saisies + validees) de la periode  [pas les achats cuve]
'  Pneumatiques      = achats (entrees) + reparations de la periode
'  Consommables      = sorties de stock valorisees au PMP
'  Personnel         = salaires charges au prorata + primes par rotation validee
'  Depenses diverses = depenses validees a la charge de l'exploitant (hors exceptionnelles)
'  Exceptionnelles   = depenses validees marquees exceptionnelles
'  Charges Atiko     = POUR MEMOIRE, exclues de la marge
'  Marge             = CA reconnu - total des charges exploitant
'==============================================================================
Option Explicit

Public Function KPI_Period(ByVal d1 As Date, ByVal d2 As Date) As Object
    Dim k As Object, charges As Double
    Set k = NewDict()
    k("D1") = d1: k("D2") = d2
    k("Tonnage") = Ops_Tonnage(d1, d2)
    k("Rotations") = DB_CountIf("T_ROTATIONS", "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    k("RotationsAttente") = DB_CountIf("T_ROTATIONS", "Statut", "=", ST_SAISIE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    k("TonnageRejete") = DB_Sum("T_ROTATIONS", "PoidsNet", "Statut", "=", ST_REJETEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    k("CA") = Ops_RevenueFor("", Empty, d1, d2)
    k("CAFacture") = Bill_Invoiced(d1, d2)
    k("Encaissements") = Bill_Collected(d1, d2)
    k("Creances") = Bill_Receivables()
    k("Location") = Rent_Cost(d1, d2)
    k("HeuresLocation") = Rent_Hours(d1, d2)
    k("HeuresBrutes") = Rent_Hours(d1, d2, "HeuresBrutes")
    k("HeuresAttente") = Rent_Hours(d1, d2, "HeuresAttente")
    k("HeuresImmob") = Mnt_ImmobHours(d1, d2)
    k("Carburant") = Fuel_Cost(d1, d2)
    k("Litres") = Fuel_Liters(d1, d2)
    k("Pneus") = Tire_Cost(d1, d2)
    k("Consommables") = Stock_ConsumptionCost(d1, d2)
    k("Personnel") = Fin_PersonnelCost(d1, d2)
    k("Depenses") = Fin_Expenses(d1, d2, "Non")
    k("Exceptionnelles") = Fin_Expenses(d1, d2, "Oui")
    k("ChargesAtiko") = Fin_AtikoCharges(d1, d2)
    k("AutresCharges") = k("Consommables") + k("Personnel") + k("Depenses") + k("Exceptionnelles")
    charges = k("Location") + k("Carburant") + k("Pneus") + k("AutresCharges")
    k("Charges") = charges
    k("Marge") = k("CA") - charges
    If k("CA") > 0 Then k("TauxMarge") = k("Marge") / k("CA") Else k("TauxMarge") = 0
    If k("Tonnage") > 0 Then
        k("CoutTonne") = charges / k("Tonnage")
        k("CarburantTonne") = k("Carburant") / k("Tonnage")
        k("LitresTonne") = k("Litres") / k("Tonnage")
    Else
        k("CoutTonne") = 0: k("CarburantTonne") = 0: k("LitresTonne") = 0
    End If
    If k("HeuresBrutes") > 0 Then k("Disponibilite") = 1 - k("HeuresImmob") / k("HeuresBrutes") Else k("Disponibilite") = 0
    If k("HeuresBrutes") > 0 Then k("TauxAttente") = k("HeuresAttente") / k("HeuresBrutes") Else k("TauxAttente") = 0
    k("Objectif") = ToDbl(DB_Lookup("T_OBJECTIFS", "Periode", MonthKey(d1), "TonnageObjectif", 0))
    Set KPI_Period = k
End Function

' Etat de la flotte a date
Public Function KPI_Fleet() As Object
    Dim k As Object, data As Variant, r As Long, st As String, n As Long
    Set k = NewDict()
    k(CAM_DISPO) = 0: k(CAM_SERVICE) = 0: k(CAM_PANNE) = 0: k(CAM_IMMO) = 0: k(CAM_HS) = 0
    data = DB_Data("T_CAMIONS")
    For r = 1 To ArrayRows(data)
        If ToStr(data(r, DB_ColIdx("T_CAMIONS", "Actif"))) <> "Non" Or ToStr(data(r, DB_ColIdx("T_CAMIONS", "Statut"))) = CAM_HS Then
            st = ToStr(data(r, DB_ColIdx("T_CAMIONS", "Statut")))
            k(st) = k(st) + 1
            n = n + 1
        End If
    Next r
    k("Total") = n
    k("Actifs") = k(CAM_DISPO) + k(CAM_SERVICE)
    If n - k(CAM_HS) > 0 Then k("Taux") = k("Actifs") / (n - k(CAM_HS)) Else k("Taux") = 0
    Set KPI_Fleet = k
End Function

' Derniers jours ouvres (hors dimanche) jusqu'a la date incluse
Public Function KPI_LastWorkDays(ByVal d As Date, ByVal n As Long) As Variant
    Dim out() As Date, i As Long, x As Date
    ReDim out(1 To n)
    x = d
    i = n
    Do While i >= 1
        If Weekday(x, vbMonday) <> 7 Then
            out(i) = x
            i = i - 1
        End If
        x = x - 1
    Loop
    KPI_LastWorkDays = out
End Function

Public Function KPI_DayTonnage(ByVal d As Date) As Double
    KPI_DayTonnage = DB_Sum("T_ROTATIONS", "PoidsNet", "DateRotation", "=", d, "Statut", "IN", ST_VALIDEE & "|" & ST_SAISIE)
End Function

' Variation en % (texte avec fleche)
Public Function KPI_Trend(ByVal cur As Double, ByVal prev As Double) As String
    If prev = 0 Then KPI_Trend = "": Exit Function
    Dim v As Double
    v = (cur - prev) / Abs(prev)
    If v >= 0 Then KPI_Trend = ChrW(9650) & " +" & Format$(v * 100, "0") & " %" Else KPI_Trend = ChrW(9660) & " " & Format$(v * 100, "0") & " %"
End Function

' Marge des N dernieres semaines glissantes (courbe du tableau de bord).
' Calcul en une passe par table (regroupement par semaine) pour rester rapide.
Public Function KPI_WeeklyMargins(ByVal d As Date, ByVal weeks As Long) As Variant
    Dim out() As Variant, rev() As Double, chg() As Double, i As Long, start As Date
    Dim data As Variant, r As Long, w As Long, dt As Date, tarifs As Object, cli As Long
    start = d - 7 * weeks + 1
    ReDim rev(1 To weeks): ReDim chg(1 To weeks)
    Set tarifs = NewDict()
    data = DB_Data("T_ROTATIONS")
    For r = 1 To ArrayRows(data)
        If data(r, DB_ColIdx("T_ROTATIONS", "Statut")) = ST_VALIDEE Then
            dt = ToDate(data(r, DB_ColIdx("T_ROTATIONS", "DateRotation")))
            If dt >= start And dt <= d Then
                w = Int((dt - start) / 7) + 1
                cli = ToLng(data(r, DB_ColIdx("T_ROTATIONS", "ClientID")))
                If Not tarifs.Exists(cli) Then tarifs(cli) = ToDbl(DB_Value("T_CLIENTS", cli, "TarifTonne"))
                rev(w) = rev(w) + ToDbl(data(r, DB_ColIdx("T_ROTATIONS", "PoidsNet"))) * tarifs(cli)
            End If
        End If
    Next r
    BucketSum "T_LOCATION", "DateReleve", "Montant", start, d, weeks, chg, Array("Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)
    BucketSum "T_CARBURANT", "DateOp", "Montant", start, d, weeks, chg, Array("TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)
    BucketSum "T_PNEU_MVT", "DateMvt", "Cout", start, d, weeks, chg, Array("TypeMvt", "IN", "Entrée|Réparation|Retour réparation")
    BucketSum "T_STOCK_MVT", "DateMvt", "Montant", start, d, weeks, chg, Array("TypeMvt", "=", "Sortie")
    BucketSum "T_DEPENSES", "DateDep", "Montant", start, d, weeks, chg, Array("Statut", "=", ST_VALIDEE, "Responsable", "=", RESP_EXPL)
    ReDim out(1 To weeks, 1 To 3)
    For i = 1 To weeks
        chg(i) = chg(i) + Fin_PersonnelCost(start + 7 * (i - 1), start + 7 * i - 1)
        out(i, 1) = Format$(start + 7 * (i - 1), "dd/mm")
        out(i, 2) = Round(rev(i) - chg(i), 0)
        If rev(i) > 0 Then out(i, 3) = (rev(i) - chg(i)) / rev(i) Else out(i, 3) = 0
    Next i
    KPI_WeeklyMargins = out
End Function

Private Sub BucketSum(ByVal tableName As String, ByVal dateCol As String, ByVal valCol As String, ByVal start As Date, _
                      ByVal d As Date, ByVal weeks As Long, ByRef acc() As Double, ByVal crit As Variant)
    Dim rows As Collection, r As Variant, data As Variant, cD As Long, cV As Long, dt As Date, w As Long
    Set rows = DB_RowsA(tableName, crit)
    data = DB_Data(tableName)
    cD = DB_ColIdx(tableName, dateCol): cV = DB_ColIdx(tableName, valCol)
    For Each r In rows
        dt = ToDate(data(r, cD))
        If dt >= start And dt <= d Then
            w = Int((dt - start) / 7) + 1
            If w >= 1 And w <= weeks Then acc(w) = acc(w) + ToDbl(data(r, cV))
        End If
    Next r
End Sub
