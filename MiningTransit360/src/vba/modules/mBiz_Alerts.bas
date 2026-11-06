Attribute VB_Name = "mBiz_Alerts"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Alerts
' Alertes automatiques calculees a la volee (jamais stockees) :
' documents, permis, pneus, stocks, cuve, factures en retard, saisies en
' attente, anomalies carburant, immobilisations, clotures, sauvegardes, litiges.
' Chaque alerte : niveau (error/warning/info/success), titre, detail, module cible.
'==============================================================================
Option Explicit

Private Sub AddAlert(ByVal col As Collection, ByVal level As String, ByVal title As String, ByVal detail As String, ByVal modKey As String)
    Dim d As Object
    Set d = NewDict()
    d("Niveau") = level
    d("Titre") = title
    d("Detail") = detail
    d("Module") = modKey
    col.Add d
End Sub

Public Function Alerts_Compute() As Collection
    Dim res As New Collection, t As Date, horizon As Long, data As Variant, r As Long
    Dim expired As Long, soon As Long, minDate As Date, lst As String, n As Long, c As Collection, v As Variant
    t = App_Today()
    horizon = Param_Num("ALERTE_DOC_JOURS", 30)

    ' --- permis chauffeurs ---
    data = DB_Data("T_CHAUFFEURS")
    minDate = 0
    For r = 1 To ArrayRows(data)
        If ToStr(data(r, DB_ColIdx("T_CHAUFFEURS", "Statut"))) <> "Sorti" Then
            If ToDate(data(r, DB_ColIdx("T_CHAUFFEURS", "ExpPermis"))) < t Then
                expired = expired + 1
            ElseIf ToDate(data(r, DB_ColIdx("T_CHAUFFEURS", "ExpPermis"))) <= t + horizon Then
                soon = soon + 1
                If minDate = 0 Or ToDate(data(r, DB_ColIdx("T_CHAUFFEURS", "ExpPermis"))) < minDate Then minDate = ToDate(data(r, DB_ColIdx("T_CHAUFFEURS", "ExpPermis")))
            End If
        End If
    Next r
    If expired + soon > 0 Then
        AddAlert res, IIf(expired > 0, "error", "warning"), "Permis chauffeur " & IIf(expired > 0, "expiré", "à renouveler") & " (" & expired + soon & ")", _
                 IIf(expired > 0, expired & " expiré(s)", "") & IIf(expired > 0 And soon > 0, ", ", "") & IIf(soon > 0, "renouvellement avant le " & FmtDate(minDate), ""), "CHAUFFEURS"
    End If

    ' --- documents camions (assurance / visite technique) ---
    data = DB_Data("T_CAMIONS")
    lst = "": n = 0
    For r = 1 To ArrayRows(data)
        If ToStr(data(r, DB_ColIdx("T_CAMIONS", "Actif"))) <> "Non" Then
            If ToDate(data(r, DB_ColIdx("T_CAMIONS", "AssuranceExp"))) <= t + horizon Or ToDate(data(r, DB_ColIdx("T_CAMIONS", "VisiteTechExp"))) <= t + horizon Then
                n = n + 1
                lst = lst & IIf(Len(lst) > 0, ", ", "") & data(r, DB_ColIdx("T_CAMIONS", "Code"))
            End If
        End If
    Next r
    If n > 0 Then AddAlert res, "warning", "Assurance / visite à renouveler (" & n & ")", "Camion " & lst, "CAMIONS"

    ' --- pneus a remplacer ---
    Set c = Tire_ToReplace()
    If c.Count > 0 Then
        Dim trucks As Object
        Set trucks = NewDict()
        For Each v In c
            trucks(CodeOf("T_CAMIONS", v("CamionID"))) = 1
        Next v
        AddAlert res, "warning", "Pneu à remplacer (" & trucks.Count & ")", "Camion " & Join(trucks.Keys, ", ") & " - " & c.Count & " pneu(s)", "PNEUS"
    End If
    n = DB_CountIf("T_PNEUS", "Statut", "=", PN_STOCK)
    If n < Param_Num("PNEU_STOCK_MIN", 4) Then AddAlert res, "warning", "Stock pneus bas (" & n & ")", "Minimum : " & Param_Num("PNEU_STOCK_MIN", 4) & " pneus en stock", "PNEUS"

    ' --- factures en retard ---
    Set c = Bill_Overdue(t)
    If c.Count > 0 Then
        data = DB_Data("T_FACTURES")
        lst = ""
        For Each v In c
            lst = lst & IIf(Len(lst) > 0, ", ", "") & CodeOf("T_CLIENTS", data(v, DB_ColIdx("T_FACTURES", "ClientID")))
        Next v
        AddAlert res, "info", "Facture en retard (" & c.Count & ")", "Client : " & lst, "RECOUVREMENT"
    End If

    ' --- immobilisations en cours ---
    n = DB_CountIf("T_INCIDENTS", "Immobilisation", "=", "Oui", "DateReprise", "ISBLANK", "", "Statut", "<>", "Clôturé")
    If n > 0 Then AddAlert res, "error", "Camion immobilisé (" & n & ")", "Incidents ouverts avec immobilisation", "INCIDENTS"

    ' --- saisies en attente de validation (avant aujourd'hui) ---
    n = DB_CountIf("T_ROTATIONS", "Statut", "=", ST_SAISIE, "DateRotation", "<", t)
    If n > 0 Then AddAlert res, "warning", "Rotations à valider (" & n & ")", "Saisies antérieures à aujourd'hui non validées", "ROTATIONS"

    ' --- carburant ---
    n = DB_CountIf("T_CARBURANT", "Statut", "=", ST_SAISIE, "Anomalie", "NOTBLANK", "")
    If n > 0 Then AddAlert res, "warning", "Anomalie carburant (" & n & ")", "Distributions à contrôler avant validation", "CARBURANT"
    If Fuel_TankStock() < Param_Num("CARB_STOCK_ALERTE", 3000) Then AddAlert res, "warning", "Stock cuve bas", FmtNum(Fuel_TankStock(), 0) & " L théoriques", "CARBURANT"

    ' --- consommables ---
    Set c = Stock_BelowMin()
    If c.Count > 0 Then AddAlert res, "info", "Consommables sous le minimum (" & c.Count & ")", c(1)("Designation") & IIf(c.Count > 1, "...", ""), "ARTICLES"

    ' --- documents (GED) ---
    n = DB_CountIf("T_DOCUMENTS", "DateExpiration", "<=", t + horizon, "DateExpiration", ">=", t - 365, "Entite", "IN", "Contrat|Client|Fournisseur|Société")
    If n > 0 Then AddAlert res, "info", "Documents contractuels à échéance (" & n & ")", "Voir la gestion documentaire", "DOCUMENTS"

    ' --- litiges ---
    n = DB_CountIf("T_RECLAMATIONS", "Statut", "IN", "Ouverte|En traitement")
    If n > 0 Then AddAlert res, "info", "Réclamations ouvertes (" & n & ")", "Litiges en cours de traitement", "RECLAMATIONS"

    ' --- cloture journaliere ---
    Dim lastDay As Date
    lastDay = ToDate(Param_Get("CLOTURE_DERNIER_JOUR", ""))
    If lastDay > 0 And t - lastDay > 2 Then AddAlert res, "warning", "Clôture journalière en retard", "Dernier jour clôturé : " & FmtDate(lastDay), "CLOTURES"

    ' --- sauvegarde ---
    Dim lastSave As String
    lastSave = ToStr(Param_Get("SAUV_DERNIERE", ""))
    If Len(lastSave) = 0 Then
        AddAlert res, "warning", "Aucune sauvegarde effectuée", "Lancez une sauvegarde manuelle", "SAUVEGARDES"
    ElseIf Now - (ToDate(Left$(lastSave, 10)) + TimeOr0(Mid$(lastSave, 12, 5))) > Param_Num("SAUV_AUTO_HEURES", 24) / 24 + 1 Then
        AddAlert res, "warning", "Sauvegarde ancienne", "Dernière : " & lastSave, "SAUVEGARDES"
    Else
        AddAlert res, "success", "Sauvegarde effectuée", "le " & lastSave, "SAUVEGARDES"
    End If
    Set Alerts_Compute = res
End Function

' Nombre d'alertes hors "success"
Public Function Alerts_Count(ByVal c As Collection) As Long
    Dim v As Variant
    For Each v In c
        If v("Niveau") <> "success" Then Alerts_Count = Alerts_Count + 1
    Next v
End Function

Public Sub Alerts_ShowAll()
    Dim c As Collection, data() As Variant, i As Long, v As Variant, f As frmInput, r As String, sel As Long
    Set c = Alerts_Compute()
    If c.Count = 0 Then UI_Alert "Aucune alerte.", "success": Exit Sub
    ReDim data(1 To c.Count, 1 To 4)
    For Each v In c
        i = i + 1
        data(i, 1) = Choose(Application.Match(v("Niveau"), Array("error", "warning", "info", "success"), 0), "CRITIQUE", "ATTENTION", "INFO", "OK")
        data(i, 2) = v("Titre")
        data(i, 3) = v("Detail")
        data(i, 4) = Mod_Get(v("Module"))("Titre")
    Next v
    Set f = Dlg_New("Alertes et notifications", Alerts_Count(c) & " alerte(s) active(s) - double-cliquez pour ouvrir le module", CLR_ORANGE(), 820)
    f.AddField "G", "", "GRID", "Niveau|Alerte|Détail|Module", data, False, "70;230;330;150", 300
    f.AddAction "CLOSE", "Fermer", CLR_GREY(), False, True
    f.AddAction "OPEN", "Ouvrir le module", CLR_ORANGE(), True
    r = f.ShowDialog
    sel = f.GridRow("G")
    f.Dispose
    If r = "OPEN" And sel >= 0 Then Nav_Open c(sel + 1)("Module")
End Sub
