Attribute VB_Name = "mUI_Dashboard"
'==============================================================================
' MINING TRANSIT 360 - mUI_Dashboard
' Tableau de bord (feuille ACCUEIL) : reproduction de la maquette avec de
' vraies formes Excel, de vrais graphiques et des actions connectees.
'
'  Dash_Build   : dessine l'ecran (installation ou reparation)
'  Dash_Refresh : recalcule les indicateurs et met a jour textes / graphiques
'  UI_ShapeClick: gestionnaire unique des clics (nom de la forme -> action)
'
' Conventions de noms des formes :
'  nav_<MODULE>      menu lateral          kpi_<CLE>     cartes indicateurs
'  qa_<ACTION>       actions rapides       pnl_<MODULE>  en-tetes de panneaux
'  alv_<n>           bouton "Voir" alerte  dv_*          valeurs dynamiques
'  "__xxx" en suffixe = calque (icone, texte) du meme element cliquable
'==============================================================================
Option Explicit

Private Const SH As String = "ACCUEIL"
Private Const W_MOCK As Double = 1672
Private Const H_MOCK As Double = 906
Private mClockAt As Date

Public Function DashSheet() As Worksheet
    Set DashSheet = ThisWorkbook.Worksheets(SH)
End Function

Private Function CalcSheet() As Worksheet
    Set CalcSheet = ThisWorkbook.Worksheets("CALC")
End Function

'==============================================================================
' CONSTRUCTION
'==============================================================================
Public Sub Dash_Build()
    Dim ws As Worksheet
    Set ws = DashSheet()
    ScreenOff
    On Error GoTo EH
    ws.Unprotect
    UI_DeleteShapes ws
    Dim co As ChartObject
    For Each co In ws.ChartObjects
        co.Delete
    Next co
    ws.Cells.Clear
    ws.Cells.Interior.Color = CLR_BG()
    ws.Cells.ColumnWidth = 2
    ws.Cells.RowHeight = 12
    ActiveWindowSetup ws
    Calc_Prepare
    BuildHeader ws
    BuildSidebar ws
    BuildKpis ws
    BuildPanels ws
    BuildQuickActions ws
    BuildFooter ws
    ws.Protect DrawingObjects:=True, Contents:=True, UserInterfaceOnly:=True
    ScreenOn
    Dash_Refresh
    Exit Sub
EH:
    Log_Error "Dash_Build", Err.Number, Err.Description
End Sub

Private Sub ActiveWindowSetup(ByVal ws As Worksheet)
    On Error Resume Next
    ws.Activate
    ActiveWindow.DisplayGridlines = False
    ActiveWindow.DisplayHeadings = False
    ActiveWindow.DisplayWorkbookTabs = (Sec_CurrentProfile() = "Administrateur")
    ActiveWindow.ScrollRow = 1
    ActiveWindow.ScrollColumn = 1
End Sub

' Ajuste le zoom pour que la maquette tienne a l'ecran
Public Sub Dash_FitZoom()
    Dim z As Double
    On Error Resume Next
    If ActiveSheet.Name <> SH Then Exit Sub
    z = Application.WorksheetFunction.Min(ActiveWindow.UsableWidth / PX(W_MOCK + 8), ActiveWindow.UsableHeight / PX(H_MOCK + 8)) * 100
    If z < 40 Then z = 40
    If z > 160 Then z = 160
    ActiveWindow.Zoom = Int(z)
    ActiveWindow.ScrollRow = 1
    ActiveWindow.ScrollColumn = 1
End Sub

Private Sub BuildHeader(ByVal ws As Worksheet)
    Dim s As Shape, f As String
    UI_Box ws, "hdr_bg", 0, 0, W_MOCK, 145, CLR_NAVY(), 0, -1, RGB(8, 30, 64)
    f = Assets_Path() & "\banner.png"
    If FileExists(f) Then
        Set s = ws.Shapes.AddPicture(f, msoFalse, msoTrue, PX(420), 0, PX(975), PX(145))
        s.Name = "hdr_banner"
    End If
    f = Assets_Path() & "\logo.png"
    If FileExists(f) Then
        Set s = ws.Shapes.AddPicture(f, msoFalse, msoTrue, PX(18), PX(14), PX(180), PX(112))
        s.Name = "hdr_logo"
    End If
    Set s = UI_Text(ws, "hdr_app1", 206, 18, 220, 40, "MINING", 30, CLR_WHITE(), True)
    Set s = UI_Text(ws, "hdr_app2", 206, 52, 220, 44, "TRANSIT 360", 30, CLR_WHITE(), True)
    s.TextFrame2.TextRange.Characters(9, 3).Font.Fill.ForeColor.RGB = CLR_ORANGE()
    UI_Text ws, "hdr_tag", 178, 100, 240, 26, APP_TAGLINE(), 15, CLR_WHITE(), True, 1, True
    ' Societe / site
    UI_Text ws, "dv_company", 448, 22, 520, 38, "Société : " & Company(), 22, CLR_WHITE(), True
    UI_Text ws, "dv_site", 448, 60, 520, 30, "Site : " & Param_Get("SOCIETE_SITE", "") & "  |  " & Param_Get("SOCIETE_ACTIVITE", ""), 17, CLR_WHITE()
    If Param_Bool("DEMO_MODE", False) Then
        Set s = UI_Box(ws, "dv_demo", 448, 98, 190, 26, CLR_ORANGE(), 12)
        UI_ShapeText s, "ENVIRONNEMENT DÉMO", 12, CLR_WHITE(), True
    End If
    ' Bloc date / utilisateur
    UI_Box ws, "hdr_datebox", 1393, 8, 268, 122, RGB(14, 40, 82), 10, RGB(60, 90, 140), -1, True
    UI_Icon ws, "hdr_cal", "calendar-days", 1410, 22, 34
    UI_Text ws, "dv_date", 1462, 18, 196, 22, LongDateFR(App_Today()), 14, CLR_WHITE()
    UI_Text ws, "dv_clock", 1462, 40, 196, 32, Format$(Now, "hh:mm"), 26, CLR_WHITE(), True
    UI_Icon ws, "usr_menu__ico", "user", 1408, 86, 30
    Set s = UI_Text(ws, "usr_menu", 1448, 84, 205, 34, "Utilisateur : " & Sec_CurrentName() & "  " & ChrW(9662), 13, CLR_WHITE())
    UI_SetAction ws, "UI_ShapeClick", "usr_menu", "usr_menu__ico"
End Sub

Private Function NavItems() As Variant
    ' cle | libelle | icone | y (maquette)
    NavItems = Array( _
        "DASHBOARD|Tableau de Bord|house|150|46", _
        "HUB_EXPLOITATION|Exploitation|truck-ramp-box|200|36", _
        "CAMIONS|Camions|truck|240|36", _
        "CHAUFFEURS|Chauffeurs|id-card|279|36", _
        "HUB_ROTATIONS|Rotations et Pesées|route|317|36", _
        "CARBURANT|Carburant|gas-pump|354|36", _
        "LOCATION|Location Horaire|clock|391|36", _
        "HUB_PNEUS|Pneumatiques|circle-dot|428|36", _
        "HUB_MAINTENANCE|Incidents & Maintenance|screwdriver-wrench|465|36", _
        "HUB_DEPENSES|Dépenses|receipt|502|36", _
        "FACTURES|Facturation|file-invoice-dollar|557|32", _
        "HUB_RECOUVREMENT|Paiements & Recouvrement|money-bill-wave|590|32", _
        "RAPPORTS|Rapports et États|chart-column|623|32", _
        "HUB_PARAMETRES|Paramètres|gear|656|32", _
        "HUB_ADMIN|Administration|user-shield|689|32")
End Function

Private Sub BuildSidebar(ByVal ws As Worksheet)
    Dim it As Variant, p() As String, y As Double, h As Double, s As Shape, nm As String
    UI_Box ws, "side_bg", 0, 145, 262, 761, CLR_NAVY(), 0, -1, RGB(9, 33, 68)
    For Each it In NavItems()
        p = Split(it, "|")
        y = Val(p(3)): h = Val(p(4))
        nm = "nav_" & p(0)
        If p(0) = "DASHBOARD" Then
            Set s = UI_Box(ws, nm, 6, y, 246, h, CLR_ORANGE(), 6, -1, CLR_ORANGE_D(), True)
        Else
            Set s = UI_Box(ws, nm, 6, y, 246, h, CLR_NAVY(), 4)
            s.Fill.Transparency = 1
            UI_Box ws, nm & "__sep", 18, y + h, 232, 1, RGB(30, 60, 105)
        End If
        UI_Icon ws, nm & "__ico", p(2), 20, y + (h - 26) / 2, 26
        UI_Text ws, nm & "__txt", 72, y + (h - 30) / 2, 178, 30, p(1), 15, CLR_WHITE(), (p(0) = "DASHBOARD")
        UI_SetAction ws, "UI_ShapeClick", nm, nm & "__ico", nm & "__txt"
    Next it
    UI_Box ws, "side_sep", 16, 548, 232, 1.5, RGB(70, 100, 150)
    ' Etat systeme
    UI_Box ws, "sys_box", 10, 728, 242, 74, RGB(14, 40, 82), 8, RGB(40, 70, 120)
    Set s = ws.Shapes.AddShape(msoShapeOval, PX(24), PX(744), PX(40), PX(40))
    s.Name = "sys_ok": s.Fill.ForeColor.RGB = CLR_GREEN(): s.Line.Visible = msoFalse
    UI_ShapeText s, ChrW(10004), 20, CLR_WHITE(), True
    UI_Text ws, "sys_t1", 76, 738, 172, 20, "Système opérationnel", 13, CLR_WHITE(), True
    UI_Text ws, "dv_lastsave", 76, 758, 172, 36, "Dernière sauvegarde :", 11, RGB(200, 210, 230), False, 1, False, False
    Set s = UI_Button(ws, "act_LOGOUT", 26, 808, 210, 40, ChrW(10162) & "  Se Déconnecter", CLR_RED(), "UI_ShapeClick", 15, 20, RGB(190, 30, 30))
End Sub

Private Sub KpiCard(ByVal ws As Worksheet, ByVal key As String, ByVal x As Double, ByVal w As Double, ByVal c1 As Long, ByVal c2 As Long, _
                    ByVal icon As String, ByVal title As String)
    Dim nm As String
    nm = "kpi_" & key
    UI_Box ws, nm, x, 156, w, 112, c1, 10, -1, c2, True
    UI_Icon ws, nm & "__ico", icon, x + 16, 186, 62
    UI_Text ws, nm & "__ttl", x + 108, 166, w - 112, 26, title, 15, CLR_WHITE(), True
    UI_Text ws, "dv_" & key, x + 108, 190, w - 112, 44, "-", 28, CLR_WHITE(), True
    UI_Text ws, "dv_" & key & "_tr", x + 108, 236, w - 112, 24, "", 14, CLR_WHITE(), True
    UI_SetAction ws, "UI_ShapeClick", nm, nm & "__ico", nm & "__ttl", "dv_" & key, "dv_" & key & "_tr"
End Sub

Private Sub BuildKpis(ByVal ws As Worksheet)
    KpiCard ws, "TONNAGE", 274, 264, CLR_BLUE(), CLR_BLUE_D(), "truck", "Tonnage du Jour"
    KpiCard ws, "CA", 549, 262, CLR_GREEN(), CLR_GREEN_D(), "chart-line", "CA du Mois"
    KpiCard ws, "ENC", 822, 268, CLR_ORANGE(), CLR_ORANGE_D(), "coins", "Encaissements"
    KpiCard ws, "CREANCES", 1101, 282, CLR_PURPLE(), CLR_PURPLE_D(), "handshake", "Créances Clients"
    KpiCard ws, "CAMIONS", 1394, 266, CLR_TEAL(), CLR_TEAL_D(), "truck", "Camions Actifs"
End Sub

Private Sub Panel(ByVal ws As Worksheet, ByVal key As String, ByVal x As Double, ByVal y As Double, ByVal w As Double, ByVal h As Double, _
                  ByVal icon As String, ByVal title As String)
    Dim nm As String
    nm = "pnl_" & key
    UI_Box ws, nm & "__body", x, y, w, h, CLR_CARD(), 8, CLR_LINE(), -1, True
    UI_Box ws, nm, x, y, w, 44, CLR_NAVY(), 8, -1, CLR_NAVY2()
    UI_Box ws, nm & "__sq", x, y + 30, w, 14, CLR_NAVY2()
    UI_Icon ws, nm & "__ico", icon, x + 14, y + 8, 28
    UI_Text ws, nm & "__ttl", x + 54, y + 6, w - 70, 32, title, 18, CLR_WHITE(), True
    UI_SetAction ws, "UI_ShapeClick", nm, nm & "__sq", nm & "__ico", nm & "__ttl"
End Sub

Private Sub BuildPanels(ByVal ws As Worksheet)
    Dim co As ChartObject, calc As Worksheet, s As Shape, i As Long
    Set calc = CalcSheet()
    ' --- Production de la semaine ---
    Panel ws, "PROD", 274, 286, 504, 268, "chart-column", "Production de la Semaine"
    Set co = ws.ChartObjects.Add(PX(282), PX(334), PX(490), PX(214))
    co.Name = "dc_prod"
    With co.Chart
        .ChartType = xlColumnClustered
        .SetSourceData calc.Range("A1:B7")
        .HasTitle = False
        .HasLegend = False
        .ChartArea.Format.Fill.Visible = msoFalse
        .ChartArea.Format.Line.Visible = msoFalse
        .PlotArea.Format.Fill.Visible = msoFalse
        .ChartGroups(1).GapWidth = 55
        With .SeriesCollection(1)
            .Format.Fill.TwoColorGradient msoGradientHorizontal, 1
            .Format.Fill.ForeColor.RGB = RGB(52, 132, 235)
            .Format.Fill.BackColor.RGB = CLR_BLUE_D()
            .HasDataLabels = True
            .DataLabels.Position = xlLabelPositionOutsideEnd
            .DataLabels.NumberFormat = "#,##0"
            .DataLabels.Font.size = 9
            .DataLabels.Font.Bold = True
            .DataLabels.Font.Color = CLR_TEXT()
        End With
        .Axes(xlValue).HasMajorGridlines = True
        .Axes(xlValue).MajorGridlines.Format.Line.ForeColor.RGB = CLR_LINE()
        .Axes(xlValue).MajorGridlines.Format.Line.DashStyle = msoLineDash
        .Axes(xlValue).TickLabels.Font.size = 8
        .Axes(xlValue).TickLabels.NumberFormat = "#,##0"
        .Axes(xlValue).Format.Line.Visible = msoFalse
        .Axes(xlCategory).TickLabels.Font.size = 8
        .Axes(xlCategory).Format.Line.ForeColor.RGB = CLR_LINE()
    End With
    co.OnAction = "UI_ShapeClick"
    ' --- Repartition des charges ---
    Panel ws, "CHARGES", 789, 286, 423, 268, "chart-pie", "Répartition des Charges"
    Set co = ws.ChartObjects.Add(PX(796), PX(334), PX(206), PX(206))
    co.Name = "dc_charges"
    With co.Chart
        .ChartType = xlDoughnut
        .SetSourceData calc.Range("D1:E5")
        .HasTitle = False
        .HasLegend = False
        .ChartArea.Format.Fill.Visible = msoFalse
        .ChartArea.Format.Line.Visible = msoFalse
        .ChartGroups(1).DoughnutHoleSize = 52
        .SeriesCollection(1).Points(1).Format.Fill.ForeColor.RGB = CLR_BLUE()
        .SeriesCollection(1).Points(2).Format.Fill.ForeColor.RGB = CLR_ORANGE()
        .SeriesCollection(1).Points(3).Format.Fill.ForeColor.RGB = CLR_GREEN()
        .SeriesCollection(1).Points(4).Format.Fill.ForeColor.RGB = CLR_PURPLE()
        .SeriesCollection(1).Format.Line.ForeColor.RGB = CLR_WHITE()
    End With
    co.OnAction = "UI_ShapeClick"
    UI_Text ws, "dv_chg_total", 834, 410, 130, 56, "-", 22, CLR_TEXT(), True, 2
    Dim lbl As Variant, cols As Variant
    lbl = Array("Carburant", "Pneumatiques", "Location", "Autres Charges")
    cols = Array(CLR_BLUE(), CLR_ORANGE(), CLR_GREEN(), CLR_PURPLE())
    For i = 0 To 3
        UI_Box ws, "chg_sq" & i, 1012, 362 + i * 37, 16, 16, cols(i), 3
        UI_Text ws, "chg_lb" & i, 1036, 354 + i * 37, 112, 30, CStr(lbl(i)), 15, CLR_TEXT()
        UI_Text ws, "dv_chg" & i, 1140, 354 + i * 37, 66, 30, "-", 15, CLR_TEXT(), False, 3
    Next i
    ' --- Alertes ---
    Panel ws, "ALERTES", 1223, 286, 437, 268, "bell", "Alertes & Notifications"
    Set s = ws.Shapes.AddShape(msoShapeOval, PX(1612), PX(294), PX(32), PX(32))
    s.Name = "pnl_ALERTES__badge": s.Fill.ForeColor.RGB = CLR_ORANGE(): s.Line.ForeColor.RGB = CLR_WHITE()
    UI_ShapeText s, "0", 14, CLR_WHITE(), True
    s.OnAction = "UI_ShapeClick"
    ' --- Statut de la flotte ---
    Panel ws, "FLOTTE", 274, 566, 386, 206, "truck", "Statut de la Flotte"
    Set co = ws.ChartObjects.Add(PX(284), PX(612), PX(156), PX(156))
    co.Name = "dc_flotte"
    With co.Chart
        .ChartType = xlDoughnut
        .SetSourceData calc.Range("G1:H5")
        .HasTitle = False
        .HasLegend = False
        .ChartArea.Format.Fill.Visible = msoFalse
        .ChartArea.Format.Line.Visible = msoFalse
        .ChartGroups(1).DoughnutHoleSize = 72
        .SeriesCollection(1).Points(1).Format.Fill.ForeColor.RGB = CLR_GREEN()
        .SeriesCollection(1).Points(2).Format.Fill.ForeColor.RGB = CLR_ORANGE()
        .SeriesCollection(1).Points(3).Format.Fill.ForeColor.RGB = CLR_RED()
        .SeriesCollection(1).Points(4).Format.Fill.ForeColor.RGB = CLR_GREY()
        .SeriesCollection(1).Format.Line.Visible = msoFalse
    End With
    co.OnAction = "UI_ShapeClick"
    UI_Text ws, "dv_flt_pct", 304, 664, 116, 34, "-", 26, CLR_TEXT(), True, 2
    UI_Text ws, "dv_flt_lbl", 304, 696, 116, 22, "Disponible", 14, CLR_TEXT(), False, 2
    lbl = Array("Disponibles", "En panne", "Immobilisés", "Hors service")
    cols = Array(CLR_GREEN(), CLR_ORANGE(), CLR_RED(), CLR_GREY())
    For i = 0 To 3
        UI_Box ws, "flt_sq" & i, 472, 627 + i * 33, 16, 16, cols(i), 3
        UI_Text ws, "flt_lb" & i, 498, 620 + i * 33, 120, 30, CStr(lbl(i)), 15, CLR_TEXT()
        UI_Text ws, "dv_flt" & i, 600, 620 + i * 33, 40, 30, "-", 15, CLR_TEXT(), True, 3
    Next i
    UI_Icon ws, "flt_deco", "truck", 282, 734, 34
    ws.Shapes("flt_deco").Visible = msoFalse
    ' --- Factures du mois ---
    Panel ws, "FACTURES", 670, 566, 490, 206, "file-invoice-dollar", "Factures du Mois"
    Dim hx As Variant, hw As Variant
    hx = Array(680, 806, 908, 1052)
    hw = Array(126, 102, 144, 100)
    UI_Box ws, "inv_hdr", 678, 612, 474, 30, RGB(241, 244, 249), 0, CLR_LINE()
    lbl = Array("N° Facture", "Client", "Montant (GNF)", "Statut")
    For i = 0 To 3
        UI_Text ws, "inv_h" & i, hx(i), 612, hw(i), 30, CStr(lbl(i)), 14, CLR_TEXT(), True, 2
    Next i
    Set s = UI_Button(ws, "pnl_FACTURES__total", 678, 738, 474, 30, "Total Facturé : -", CLR_NAVY(), "UI_ShapeClick", 16, 2)
    ' --- Marge ---
    Panel ws, "MARGE", 1170, 566, 490, 206, "chart-line", "Marge d'Exploitation"
    UI_Text ws, "dv_marge", 1186, 610, 290, 40, "-", 28, CLR_GREEN(), True
    UI_Text ws, "dv_taux", 1186, 648, 290, 26, "Taux de marge : -", 17, CLR_TEXT(), True
    UI_Box ws, "mrg_sep", 1458, 616, 1, 54, CLR_LINE()
    UI_Text ws, "dv_mtr", 1470, 608, 180, 40, "-", 24, CLR_GREEN(), True, 2
    UI_Text ws, "dv_mtr_l", 1470, 646, 180, 24, "vs. mois précédent", 13, CLR_TEXT(), False, 2
    Set co = ws.ChartObjects.Add(PX(1180), PX(676), PX(472), PX(92))
    co.Name = "dc_marge"
    With co.Chart
        .ChartType = xlLineMarkers
        .SetSourceData calc.Range("J1:K9")
        .HasTitle = False
        .HasLegend = False
        .ChartArea.Format.Fill.Visible = msoFalse
        .ChartArea.Format.Line.Visible = msoFalse
        .PlotArea.Format.Fill.ForeColor.RGB = RGB(236, 248, 240)
        .HasAxis(xlValue) = False
        .HasAxis(xlCategory) = False
        With .SeriesCollection(1)
            .Format.Line.ForeColor.RGB = CLR_GREEN()
            .Format.Line.Weight = 2
            .MarkerStyle = xlMarkerStyleCircle
            .MarkerSize = 6
            .MarkerBackgroundColor = CLR_WHITE()
            .MarkerForegroundColor = CLR_GREEN()
        End With
    End With
    co.OnAction = "UI_ShapeClick"
    UI_SetAction ws, "UI_ShapeClick", "dv_marge", "dv_taux", "dv_mtr", "dv_mtr_l"
End Sub

Private Sub BuildQuickActions(ByVal ws As Worksheet)
    QA ws, "NEW_ROTATION", 286, 224, CLR_BLUE(), CLR_BLUE_D(), "plus", "Nouvelle" & vbLf & "Rotation"
    QA ws, "NEW_FUEL", 525, 223, CLR_GREEN(), CLR_GREEN_D(), "gas-pump", "Enregistrer" & vbLf & "Carburant"
    QA ws, "TIRES", 765, 210, CLR_ORANGE(), CLR_ORANGE_D(), "circle-dot", "Gérer" & vbLf & "Pneus"
    QA ws, "NEW_INVOICE", 990, 208, CLR_PURPLE(), CLR_PURPLE_D(), "file-lines", "Créer" & vbLf & "Facture"
    QA ws, "REPORTS", 1214, 222, CLR_TEAL(), CLR_TEAL_D(), "chart-column", "Voir les" & vbLf & "Rapports"
    QA ws, "SETTINGS", 1451, 202, CLR_NAVY2(), CLR_NAVY(), "gear", "Paramètres"
End Sub

Private Sub QA(ByVal ws As Worksheet, ByVal key As String, ByVal x As Double, ByVal w As Double, ByVal c1 As Long, ByVal c2 As Long, _
               ByVal icon As String, ByVal caption As String)
    Dim nm As String
    nm = "qa_" & key
    UI_Box ws, nm, x, 786, w, 70, c1, 10, -1, c2, True
    UI_Icon ws, nm & "__ico", icon, x + 22, 801, 40
    UI_Text ws, nm & "__txt", x + 80, 790, w - 88, 62, caption, 17, CLR_WHITE(), True
    UI_SetAction ws, "UI_ShapeClick", nm, nm & "__ico", nm & "__txt"
End Sub

Private Sub BuildFooter(ByVal ws As Worksheet)
    UI_Box ws, "ftr_line", 0, 868, W_MOCK, 3, CLR_ORANGE()
    UI_Box ws, "ftr_bg", 0, 871, W_MOCK, 36, CLR_NAVY()
    UI_Text ws, "ftr_l", 22, 874, 900, 30, APP_NAME & " v" & APP_VERSION & "   |   Développé pour une gestion efficace de votre exploitation", 14, CLR_WHITE()
    UI_Text ws, "ftr_r", 1300, 874, 352, 30, "Ensemble vers l'excellence...", 14, CLR_WHITE(), True, 3, True
End Sub

'==============================================================================
' DONNEES DES GRAPHIQUES (feuille CALC masquee)
'==============================================================================
Private Sub Calc_Prepare()
    Dim c As Worksheet
    Set c = CalcSheet()
    c.Cells.Clear
    c.Range("A1:B1").Value = Array("Jour", "Tonnage")
    c.Range("D1:E1").Value = Array("Charge", "Montant")
    c.Range("D2:D5").Value = Application.WorksheetFunction.Transpose(Array("Carburant", "Pneumatiques", "Location", "Autres charges"))
    c.Range("G1:H1").Value = Array("Statut", "Nombre")
    c.Range("G2:G5").Value = Application.WorksheetFunction.Transpose(Array("Disponibles", "En panne", "Immobilisés", "Hors service"))
    c.Range("J1:K1").Value = Array("Semaine", "Marge")
    c.Range("B2:B7").Value = 0: c.Range("E2:E5").Value = 1: c.Range("H2:H5").Value = 1: c.Range("K2:K9").Value = 0
End Sub

'==============================================================================
' RAFRAICHISSEMENT
'==============================================================================
Public Sub Dash_Refresh()
    Dim ws As Worksheet, c As Worksheet, t As Date, k As Object, prev As Object, fl As Object, days As Variant, i As Long
    Dim dToday As Double, dPrev As Double, mPrevEnd As Date, caPrev As Double, encPrev As Double, mPrev As Object
    On Error GoTo EH
    Set ws = DashSheet()
    If Not UI_ShapeExists(ws, "kpi_TONNAGE") Then Dash_Build: Exit Sub
    Set c = CalcSheet()
    ScreenOff
    ' la protection "UserInterfaceOnly" n'est pas conservee a la fermeture : on la reapplique
    ws.Protect DrawingObjects:=True, Contents:=True, UserInterfaceOnly:=True
    Status "Actualisation du tableau de bord..."
    DB_Invalidate
    t = App_Today()
    ' --- en-tete ---
    UI_SetText ws, "dv_company", "Société : " & Company()
    UI_SetText ws, "dv_site", "Site : " & Param_Get("SOCIETE_SITE", "") & "  |  " & Param_Get("SOCIETE_ACTIVITE", "")
    UI_SetText ws, "dv_date", LongDateFR(t)
    UI_SetText ws, "dv_clock", Format$(Now, "hh:mm")
    UI_SetText ws, "usr_menu", "Utilisateur : " & Sec_CurrentName() & "  " & ChrW(9662)
    UI_SetText ws, "dv_lastsave", "Dernière sauvegarde :" & vbLf & Replace(ToStr(Param_Get("SAUV_DERNIERE", "aucune")), " ", " à ")
    ' --- indicateurs ---
    Set k = KPI_Period(MonthStart(t), t)
    dToday = KPI_DayTonnage(t)
    days = KPI_LastWorkDays(t, 2)
    dPrev = KPI_DayTonnage(days(1))
    UI_SetText ws, "dv_TONNAGE", FmtNum(dToday, 0) & " t"
    UI_SetText ws, "dv_TONNAGE_tr", KPI_Trend(dToday, dPrev) & IIf(dPrev > 0, "  vs " & Format$(days(1), "dd/mm"), "")
    ' comparaison au meme nombre de jours du mois precedent
    mPrevEnd = DateSerial(Year(t), Month(t) - 1, Application.WorksheetFunction.Min(Day(t), Day(MonthEnd(DateSerial(Year(t), Month(t) - 1, 1)))))
    caPrev = Ops_RevenueFor("", Empty, MonthStart(mPrevEnd), mPrevEnd)
    encPrev = Bill_Collected(MonthStart(mPrevEnd), mPrevEnd)
    UI_SetText ws, "dv_CA", FmtMoneyCard(k("CA"))
    UI_SetText ws, "dv_CA_tr", KPI_Trend(k("CA"), caPrev)
    UI_SetText ws, "dv_ENC", FmtMoneyCard(k("Encaissements"))
    UI_SetText ws, "dv_ENC_tr", KPI_Trend(k("Encaissements"), encPrev)
    UI_SetText ws, "dv_CREANCES", FmtMoneyCard(k("Creances"))
    UI_SetText ws, "dv_CREANCES_tr", Bill_Overdue(t).Count & " facture(s) en retard"
    Set fl = KPI_Fleet()
    UI_SetText ws, "dv_CAMIONS", fl("Actifs") & " / " & fl("Total")
    UI_SetText ws, "dv_CAMIONS_tr", FmtPct(fl("Taux"), 0) & " disponibles"
    ' --- production de la semaine (6 derniers jours ouvres) ---
    days = KPI_LastWorkDays(t, 6)
    For i = 1 To 6
        c.Cells(1 + i, 1).Value = DayNameFR(days(i), True) & vbLf & Format$(days(i), "dd/mm")
        c.Cells(1 + i, 2).Value = Round(KPI_DayTonnage(days(i)), 0)
    Next i
    ' --- repartition des charges du mois ---
    Dim tot As Double
    c.Range("E2").Value = k("Carburant")
    c.Range("E3").Value = k("Pneus")
    c.Range("E4").Value = k("Location")
    c.Range("E5").Value = k("AutresCharges")
    tot = k("Charges")
    UI_SetText ws, "dv_chg_total", FmtShortMoney(tot)
    For i = 0 To 3
        If tot > 0 Then UI_SetText ws, "dv_chg" & i, FmtPct(c.Cells(2 + i, 5).Value / tot, 1) Else UI_SetText ws, "dv_chg" & i, "-"
    Next i
    If tot = 0 Then c.Range("E2:E5").Value = 1
    ' --- flotte ---
    c.Range("H2").Value = fl("Actifs")
    c.Range("H3").Value = fl(CAM_PANNE)
    c.Range("H4").Value = fl(CAM_IMMO)
    c.Range("H5").Value = fl(CAM_HS)
    UI_SetText ws, "dv_flt_pct", FmtPct(fl("Taux"), 0)
    UI_SetText ws, "dv_flt0", CStr(fl("Actifs"))
    UI_SetText ws, "dv_flt1", CStr(fl(CAM_PANNE))
    UI_SetText ws, "dv_flt2", CStr(fl(CAM_IMMO))
    UI_SetText ws, "dv_flt3", CStr(fl(CAM_HS))
    ' --- marge ---
    Set mPrev = Nothing
    Dim wm As Variant, mPrevRate As Double, kPrev As Object
    Set kPrev = KPI_Period(MonthStart(mPrevEnd), mPrevEnd)
    UI_SetText ws, "dv_marge", FmtMoney(k("Marge")), IIf(k("Marge") >= 0, CLR_GREEN(), CLR_RED())
    UI_SetText ws, "dv_taux", "Taux de marge : " & FmtPct(k("TauxMarge"), 1)
    If kPrev("TauxMarge") <> 0 Then
        UI_SetText ws, "dv_mtr", IIf(k("TauxMarge") >= kPrev("TauxMarge"), ChrW(9650) & " +", ChrW(9660) & " ") & _
                   Format$((k("TauxMarge") - kPrev("TauxMarge")) * 100, "0.0") & " pt", IIf(k("TauxMarge") >= kPrev("TauxMarge"), CLR_GREEN(), CLR_RED())
    Else
        UI_SetText ws, "dv_mtr", "-"
    End If
    wm = KPI_WeeklyMargins(t, 8)
    For i = 1 To 8
        c.Cells(1 + i, 10).Value = wm(i, 1)
        c.Cells(1 + i, 11).Value = wm(i, 2)
    Next i
    ' --- alertes et factures ---
    Dash_RenderAlerts ws
    Dash_RenderInvoices ws, t
    mClockAt = Now
    ScreenOn
    Status "Tableau de bord à jour (" & Format$(Now, "hh:mm:ss") & ")"
    Exit Sub
EH:
    Log_Error "Dash_Refresh", Err.Number, Err.Description
End Sub

Private Function FmtMoneyCard(ByVal v As Double) As String
    If Abs(v) >= 1000000000# Then FmtMoneyCard = FmtShortMoney(v) Else FmtMoneyCard = FmtMoney(v)
End Function

Private Sub Dash_RenderAlerts(ByVal ws As Worksheet)
    Dim c As Collection, i As Long, y As Double, a As Object, s As Shape, col As Long, glyph As String, n As Long
    UI_DeleteShapes ws, "al_"
    UI_DeleteShapes ws, "alv_"
    Set c = Alerts_Compute()
    UI_ShapeText ws.Shapes("pnl_ALERTES__badge"), CStr(Alerts_Count(c)), 14, CLR_WHITE(), True
    ' priorite : erreurs, avertissements, infos, puis succes (affiche en dernier)
    Dim ordered As New Collection, lvl As Variant
    For Each lvl In Array("error", "warning", "info", "success")
        For Each a In c
            If a("Niveau") = lvl Then ordered.Add a
        Next a
    Next lvl
    n = ordered.Count
    If n > 4 Then n = 4
    For i = 1 To n
        Set a = ordered(i)
        y = 338 + (i - 1) * 53
        Select Case a("Niveau")
            Case "error": col = CLR_RED(): glyph = "!"
            Case "warning": col = CLR_YELLOW(): glyph = "!"
            Case "success": col = CLR_GREEN(): glyph = ChrW(10004)
            Case Else: col = CLR_BLUE(): glyph = "i"
        End Select
        If a("Niveau") = "warning" Then
            Set s = ws.Shapes.AddShape(msoShapeIsoscelesTriangle, PX(1240), PX(y + 4), PX(36), PX(32))
        Else
            Set s = ws.Shapes.AddShape(msoShapeOval, PX(1240), PX(y + 2), PX(36), PX(36))
        End If
        s.Name = "al_ico" & i: s.Fill.ForeColor.RGB = col: s.Line.Visible = msoFalse
        UI_ShapeText s, glyph, 18, CLR_WHITE(), True
        If a("Niveau") = "warning" Then s.TextFrame2.MarginTop = PX(8)
        UI_Text ws, "al_t" & i, 1290, y, 290, 24, a("Titre"), 16, CLR_TEXT(), True
        UI_Text ws, "al_d" & i, 1290, y + 22, 290, 22, a("Detail"), 13, CLR_MUTED()
        If a("Niveau") <> "success" Then
            Set s = UI_Button(ws, "alv_" & i, 1584, y + 6, 62, 30, "Voir", RGB(214, 230, 250), "UI_ShapeClick", 15, 6, -1, CLR_BLUE_D())
            s.Shadow.Visible = msoFalse
            s.AlternativeText = a("Module")
        End If
        If i < n Then UI_Box ws, "al_sep" & i, 1236, y + 48, 412, 1, CLR_LINE()
    Next i
End Sub

Private Sub Dash_RenderInvoices(ByVal ws As Worksheet, ByVal t As Date)
    Dim rows As Collection, data As Variant, r As Variant, i As Long, y As Double, total As Double, s As Shape, st As String, col As Long
    Dim hx As Variant, hw As Variant, idx() As Variant, n As Long
    UI_DeleteShapes ws, "inv_r"
    hx = Array(680, 806, 908, 1052): hw = Array(126, 102, 144, 100)
    ' factures emises dans le mois (a defaut : les 3 dernieres)
    Set rows = DB_Rows("T_FACTURES", "DateFacture", ">=", MonthStart(t), "DateFacture", "<=", t, "Statut", "<>", FAC_ANNULEE)
    data = DB_Data("T_FACTURES")
    If rows.Count = 0 Then Set rows = DB_Rows("T_FACTURES", "Statut", "<>", FAC_ANNULEE)
    n = rows.Count
    If n = 0 Then
        UI_ShapeText ws.Shapes("pnl_FACTURES__total"), "Aucune facture", 16, CLR_WHITE(), True
        Exit Sub
    End If
    ReDim idx(1 To n, 1 To 2)
    For Each r In rows
        i = i + 1
        idx(i, 1) = ToLng(data(r, DB_ColIdx("T_FACTURES", "ID")))
        idx(i, 2) = r
    Next r
    SortArray2D idx, 1, True
    If n > 3 Then n = 3
    For i = 1 To n
        r = idx(i, 2)
        y = 642 + (i - 1) * 31
        UI_Text ws, "inv_r" & i & "_0", hx(0), y, hw(0), 30, ToStr(data(r, DB_ColIdx("T_FACTURES", "NumFacture"))), 14, CLR_TEXT(), False, 2
        UI_Text ws, "inv_r" & i & "_1", hx(1), y, hw(1), 30, CodeOf("T_CLIENTS", data(r, DB_ColIdx("T_FACTURES", "ClientID"))), 14, CLR_TEXT(), False, 2
        UI_Text ws, "inv_r" & i & "_2", hx(2), y, hw(2), 30, FmtMoney(data(r, DB_ColIdx("T_FACTURES", "MontantTTC")), False), 14, CLR_TEXT(), False, 2
        st = ToStr(data(r, DB_ColIdx("T_FACTURES", "Statut")))
        Select Case st
            Case FAC_PAYEE: col = CLR_GREEN()
            Case FAC_PARTIELLE: col = CLR_BLUE()
            Case FAC_BROUILLON: col = CLR_GREY()
            Case Else: col = CLR_YELLOW(): st = "En cours"
        End Select
        Set s = UI_Box(ws, "inv_r" & i & "_3", hx(3) + 10, y + 4, hw(3) - 16, 22, col, 11)
        UI_ShapeText s, st, 13, CLR_WHITE(), True
        UI_Box ws, "inv_r" & i & "_l", 678, y + 30, 474, 1, CLR_LINE()
        total = total + ToDbl(data(r, DB_ColIdx("T_FACTURES", "MontantTTC")))
    Next i
    For i = 1 To ArrayRows(idx)
        If i > 3 Then total = total + ToDbl(data(idx(i, 2), DB_ColIdx("T_FACTURES", "MontantTTC")))
    Next i
    UI_ShapeText ws.Shapes("pnl_FACTURES__total"), "Total Facturé : " & FmtMoney(total), 16, CLR_WHITE(), True
End Sub

' Horloge : rafraichie toutes les 30 s tant que le tableau de bord est affiche
Public Sub Dash_Tick()
    On Error Resume Next
    If ActiveWorkbook Is ThisWorkbook Then
        If ActiveSheet.Name = SH Then
            UI_SetText DashSheet(), "dv_clock", Format$(Now, "hh:mm")
            UI_SetText DashSheet(), "dv_date", LongDateFR(App_Today())
        End If
    End If
    Dash_ScheduleTick
End Sub

Public Sub Dash_ScheduleTick()
    On Error Resume Next
    mClockAt = Now + TimeSerial(0, 0, 30)
    Application.OnTime mClockAt, "Dash_Tick"
End Sub

Public Sub Dash_StopTick()
    On Error Resume Next
    If mClockAt > 0 Then Application.OnTime mClockAt, "Dash_Tick", , False
    mClockAt = 0
End Sub

'==============================================================================
' CLICS
'==============================================================================
Public Sub UI_ShapeClick()
    Dim nm As String, p As Long
    On Error GoTo EH
    nm = CStr(Application.Caller)
    p = InStr(nm, "__")
    If p > 0 Then nm = Left$(nm, p - 1)
    ' valeurs des cartes KPI -> carte correspondante
    If Left$(nm, 3) = "dv_" Then
        Dim kk As String
        kk = Split(Mid$(nm, 4), "_")(0)
        If InList(kk, "TONNAGE|CA|ENC|CREANCES|CAMIONS") Then nm = "kpi_" & kk
    End If
    Select Case True
        Case Left$(nm, 4) = "nav_": Nav_Open Mid$(nm, 5)
        Case Left$(nm, 4) = "kpi_"
            Select Case Mid$(nm, 5)
                Case "TONNAGE": Nav_Open "ROTATIONS"
                Case "CA": Rpt_RunByKey "DIR_KPI"
                Case "ENC": Nav_Open "PAIEMENTS"
                Case "CREANCES": Nav_Open "RECOUVREMENT"
                Case "CAMIONS": Nav_Open "CAMIONS"
            End Select
        Case Left$(nm, 3) = "qa_"
            Select Case Mid$(nm, 4)
                Case "NEW_ROTATION": Ops_QuickRotation: App_Persist: Dash_Refresh
                Case "NEW_FUEL": Rec_OpenForm "CARBURANT", 0, "NEW": App_Persist: Dash_Refresh
                Case "TIRES": Nav_Open "PNEUS"
                Case "NEW_INVOICE": Bill_GenerateMonthDialog: Nav_Open "FACTURES"
                Case "REPORTS": Nav_Open "RAPPORTS"
                Case "SETTINGS": Nav_Open "HUB_PARAMETRES"
            End Select
        Case Left$(nm, 4) = "pnl_"
            Select Case Mid$(nm, 5)
                Case "PROD": Nav_Open "ROTATIONS"
                Case "CHARGES": Rpt_RunByKey "DIR_RENTA"
                Case "ALERTES": Alerts_ShowAll
                Case "FLOTTE": Nav_Open "CAMIONS"
                Case "FACTURES": Nav_Open "FACTURES"
                Case "MARGE": Rpt_RunByKey "DIR_KPI"
            End Select
        Case nm = "dc_prod": Nav_Open "ROTATIONS"
        Case nm = "dc_charges": Rpt_RunByKey "DIR_RENTA"
        Case nm = "dc_flotte": Nav_Open "CAMIONS"
        Case nm = "dc_marge" Or Left$(nm, 4) = "dv_m" Or nm = "dv_taux": Rpt_RunByKey "DIR_KPI"
        Case Left$(nm, 4) = "alv_": Nav_Open DashSheet().Shapes(nm).AlternativeText
        Case nm = "act_LOGOUT": App_Logout
        Case nm = "usr_menu": App_UserMenu
        Case Left$(nm, 3) = "dv_": Dash_Refresh
    End Select
    Exit Sub
EH:
    Log_Error "UI_ShapeClick", Err.Number, Err.Description
End Sub
