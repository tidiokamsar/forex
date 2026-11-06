Attribute VB_Name = "mRpt_Engine"
'==============================================================================
' MINING TRANSIT 360 - mRpt_Engine
' Rendu des etats sur la feuille RAPPORT (modele A4 professionnel) :
'  logo, en-tete societe, titre, periode, filtres, blocs, totaux, signatures,
'  pied de page (pagination, date de generation, preparateur), orientation
'  automatique portrait / paysage, ajustement en largeur.
' Sorties : aperçu (PDF ouvert), impression, export PDF, export Excel, consultation.
'==============================================================================
Option Explicit

Private Const RPT_SHEET As String = "RAPPORT"

Public Function RptSheet() As Worksheet
    Set RptSheet = ThisWorkbook.Worksheets(RPT_SHEET)
End Function

Public Sub Rpt_Render(ByVal rep As cReport, Optional ByVal targetSheet As Worksheet)
    Dim ws As Worksheet, r As Long, nCols As Long, b As Object, s As Shape, f As String, wasProt As Boolean
    If targetSheet Is Nothing Then Set ws = RptSheet() Else Set ws = targetSheet
    ScreenOff
    ws.Unprotect
    ws.Cells.Clear
    UI_DeleteShapes ws
    ws.Cells.Font.Name = UI_FONT()
    ws.Cells.Font.size = 9
    nCols = rep.MaxCols()
    ws.Columns(1).ColumnWidth = 1.5
    ' --- en-tete ---
    f = Assets_Path() & "\logo.png"
    If FileExists(f) Then
        Set s = ws.Shapes.AddPicture(f, msoFalse, msoTrue, ws.Cells(1, 2).Left, 2, 62, 40)
        s.Name = "rpt_logo"
    End If
    With ws.Cells(1, 4)
        .Value = Company()
        .Font.size = 13: .Font.Bold = True: .Font.Color = CLR_NAVY()
    End With
    ws.Cells(2, 4).Value = Param_Get("SOCIETE_ADRESSE", "")
    ws.Cells(3, 4).Value = "Tél. " & Param_Get("SOCIETE_TEL", "") & "  -  " & Param_Get("SOCIETE_EMAIL", "")
    ws.Range(ws.Cells(2, 4), ws.Cells(3, 4)).Font.Color = CLR_MUTED()
    ws.Cells(1, nCols + 1).Value = APP_NAME
    ws.Cells(1, nCols + 1).HorizontalAlignment = xlRight
    ws.Cells(1, nCols + 1).Font.Color = CLR_ORANGE(): ws.Cells(1, nCols + 1).Font.Bold = True
    ws.Cells(2, nCols + 1).Value = rep.Category
    ws.Cells(2, nCols + 1).HorizontalAlignment = xlRight
    ws.Cells(2, nCols + 1).Font.Color = CLR_MUTED()
    If Param_Bool("DEMO_MODE", False) Then
        ws.Cells(3, nCols + 1).Value = "DONNÉES DE DÉMONSTRATION FICTIVES"
        ws.Cells(3, nCols + 1).HorizontalAlignment = xlRight
        ws.Cells(3, nCols + 1).Font.Color = CLR_RED(): ws.Cells(3, nCols + 1).Font.Bold = True
    End If
    r = 5
    With ws.Range(ws.Cells(r, 2), ws.Cells(r, nCols + 1))
        .Interior.Color = CLR_NAVY()
        .Font.Color = CLR_WHITE()
        .Font.size = 14
        .Font.Bold = True
        .RowHeight = 26
        .VerticalAlignment = xlCenter
    End With
    ws.Cells(r, 2).Value = "  " & UCase$(rep.Title)
    ws.Range(ws.Cells(r + 1, 2), ws.Cells(r + 1, nCols + 1)).Interior.Color = CLR_ORANGE()
    ws.Rows(r + 1).RowHeight = 3
    r = r + 2
    ws.Cells(r, 2).Value = rep.Subtitle
    ws.Cells(r, 2).Font.Bold = True: ws.Cells(r, 2).Font.size = 10
    If Len(rep.Filters) > 0 Then
        r = r + 1
        ws.Cells(r, 2).Value = "Filtres : " & rep.Filters
        ws.Cells(r, 2).Font.Color = CLR_MUTED()
    End If
    r = r + 2
    ' --- blocs ---
    For Each b In rep.Blocks()
        Select Case b("Kind")
            Case "INFO": r = RenderInfo(ws, b, r, nCols)
            Case "TABLE": r = RenderTable(ws, b, r)
            Case "TEXT"
                ws.Cells(r, 2).Value = b("Text")
                If b("Style") = "title" Then
                    ws.Cells(r, 2).Font.Bold = True: ws.Cells(r, 2).Font.size = 11: ws.Cells(r, 2).Font.Color = CLR_NAVY()
                ElseIf b("Style") = "note" Then
                    ws.Cells(r, 2).Font.Italic = True: ws.Cells(r, 2).Font.Color = CLR_MUTED()
                End If
                r = r + 1
        End Select
        r = r + 1
    Next b
    ' --- signatures ---
    If rep.Signatures Then r = RenderSignatures(ws, r + 1, nCols)
    ' --- largeur des colonnes ---
    AutoWidths ws, nCols
    ' --- mise en page ---
    SetupPage ws, rep, r, nCols
    Rpt_AddBackButton ws, nCols
    ScreenOn
End Sub

' Bouton de retour (non imprime) pour la consultation a l'ecran
Public Sub Rpt_AddBackButton(ByVal ws As Worksheet, ByVal nCols As Long)
    Dim s As Shape
    Set s = ws.Shapes.AddShape(msoShapeRoundedRectangle, ws.Cells(1, nCols + 3).Left, 4, 150, 26)
    s.Name = "btn_back"
    s.Fill.ForeColor.RGB = CLR_ORANGE()
    s.Line.Visible = msoFalse
    UI_ShapeText s, ChrW(9664) & "  Tableau de bord", 13, CLR_WHITE(), True
    s.OnAction = "Nav_Home"
    On Error Resume Next
    s.DrawingObject.PrintObject = False      ' bouton non imprime
    On Error GoTo 0
End Sub

Private Function RenderInfo(ByVal ws As Worksheet, ByVal b As Object, ByVal r As Long, ByVal nCols As Long) As Long
    Dim p As Variant, i As Long, c As Long, half As Long
    If Len(b("Title")) > 0 Then
        ws.Cells(r, 2).Value = b("Title")
        ws.Cells(r, 2).Font.Bold = True: ws.Cells(r, 2).Font.size = 11: ws.Cells(r, 2).Font.Color = CLR_NAVY()
        ws.Range(ws.Cells(r, 2), ws.Cells(r, nCols + 1)).Borders(xlEdgeBottom).Color = CLR_ORANGE()
        r = r + 1
    End If
    p = b("Pairs")
    half = 2 + Application.WorksheetFunction.Max(2, nCols \ 2)
    For i = LBound(p) To UBound(p) - 1 Step 2
        If ((i - LBound(p)) \ 2) Mod 2 = 0 Then c = 2 Else c = half
        ws.Cells(r, c).Value = p(i)
        ws.Cells(r, c).Font.Color = CLR_MUTED()
        ws.Cells(r, c + 1).Value = "'" & ToStr(p(i + 1))
        ws.Cells(r, c + 1).Font.Bold = True
        If c = half Or i + 2 > UBound(p) Then r = r + 1
    Next i
    RenderInfo = r
End Function

Private Function RenderTable(ByVal ws As Worksheet, ByVal b As Object, ByVal r As Long) As Long
    Dim h As Variant, t As Variant, data As Variant, tot As Variant, n As Long, m As Long, i As Long, j As Long, r0 As Long
    Dim sums() As Double, rng As Range, out() As Variant
    h = b("Headers"): t = b("Types"): data = b("Data"): tot = b("Totals")
    m = UBound(h) - LBound(h) + 1
    If Len(b("Title")) > 0 Then
        ws.Cells(r, 2).Value = b("Title")
        ws.Cells(r, 2).Font.Bold = True: ws.Cells(r, 2).Font.size = 11: ws.Cells(r, 2).Font.Color = CLR_NAVY()
        r = r + 1
    End If
    For j = 0 To m - 1
        ws.Cells(r, 2 + j).Value = h(j)
    Next j
    With ws.Range(ws.Cells(r, 2), ws.Cells(r, 1 + m))
        .Interior.Color = CLR_NAVY2()
        .Font.Color = CLR_WHITE()
        .Font.Bold = True
        .WrapText = True
        .VerticalAlignment = xlCenter
        .HorizontalAlignment = xlCenter
        .RowHeight = 28
    End With
    r0 = r + 1
    n = ArrayRows(data)
    ReDim sums(0 To m - 1)
    If n = 0 Then
        ws.Cells(r0, 2).Value = "Aucune donnée sur la période / les filtres sélectionnés."
        ws.Cells(r0, 2).Font.Italic = True: ws.Cells(r0, 2).Font.Color = CLR_MUTED()
        RenderTable = r0 + 1
        Exit Function
    End If
    ReDim out(1 To n, 1 To m)
    For i = 1 To n
        For j = 0 To m - 1
            out(i, j + 1) = data(LBound(data, 1) + i - 1, LBound(data, 2) + j)
            If t(j) = "M" Or Left$(t(j), 1) = "N" Then
                If IsNumOrDate(out(i, j + 1)) Then sums(j) = sums(j) + CDbl(out(i, j + 1))
            End If
        Next j
    Next i
    Set rng = ws.Range(ws.Cells(r0, 2), ws.Cells(r0 + n - 1, 1 + m))
    rng.Value = out
    For j = 0 To m - 1
        With rng.Columns(j + 1)
            Select Case t(j)
                Case "D": .NumberFormat = "dd/mm/yyyy": .HorizontalAlignment = xlCenter
                Case "H": .NumberFormat = "hh:mm": .HorizontalAlignment = xlCenter
                Case "M": .NumberFormat = "#,##0"
                Case "N0": .NumberFormat = "#,##0"
                Case "N1": .NumberFormat = "#,##0.0"
                Case "N2": .NumberFormat = "#,##0.00"
                Case "P": .NumberFormat = "0.0%"
                Case Else: .NumberFormat = "@": .HorizontalAlignment = xlLeft
            End Select
        End With
    Next j
    ' lignes alternees + bordures
    For i = 2 To n Step 2
        rng.Rows(i).Interior.Color = CLR_ROWALT()
    Next i
    rng.Borders(xlInsideHorizontal).Color = CLR_LINE()
    rng.Borders(xlEdgeBottom).Color = CLR_LINE()
    r = r0 + n
    ' totaux
    If Not IsEmpty(tot) Then
        For j = 0 To m - 1
            If j <= UBound(tot) Then
                If tot(j) = "1" Then ws.Cells(r, 2 + j).Value = sums(j)
                If tot(j) = "L" Then ws.Cells(r, 2 + j).Value = "TOTAL"
            End If
            ws.Cells(r, 2 + j).NumberFormat = rng.Cells(1, j + 1).NumberFormat
            If t(j) = "P" Then ws.Cells(r, 2 + j).ClearContents
        Next j
        With ws.Range(ws.Cells(r, 2), ws.Cells(r, 1 + m))
            .Font.Bold = True
            .Interior.Color = RGB(255, 241, 224)
            .Borders(xlEdgeTop).Color = CLR_ORANGE()
            .Borders(xlEdgeTop).Weight = xlMedium
        End With
        r = r + 1
    End If
    ws.Cells(r, 2).Value = n & " ligne(s)"
    ws.Cells(r, 2).Font.size = 7: ws.Cells(r, 2).Font.Color = CLR_MUTED()
    RenderTable = r + 1
End Function

Private Function RenderSignatures(ByVal ws As Worksheet, ByVal r As Long, ByVal nCols As Long) As Long
    Dim labels As Variant, i As Long, c As Long, span As Long
    labels = Array("Établi par", "Vérifié par", "Approuvé par")
    span = Application.WorksheetFunction.Max(1, nCols \ 3)
    For i = 0 To 2
        c = 2 + i * span
        ws.Cells(r, c).Value = labels(i)
        ws.Cells(r, c).Font.Bold = True
        If i = 0 Then ws.Cells(r + 1, c).Value = Sec_CurrentName()
        With ws.Range(ws.Cells(r + 1, c), ws.Cells(r + 4, c + span - 1))
            .Borders(xlEdgeBottom).Color = CLR_GREY()
        End With
        ws.Cells(r + 5, c).Value = "Date et signature"
        ws.Cells(r + 5, c).Font.size = 7: ws.Cells(r + 5, c).Font.Color = CLR_MUTED()
    Next i
    RenderSignatures = r + 6
End Function

Private Sub AutoWidths(ByVal ws As Worksheet, ByVal nCols As Long)
    Dim j As Long, w As Double
    For j = 2 To nCols + 1
        ws.Columns(j).AutoFit
        w = ws.Columns(j).ColumnWidth
        If w < 9 Then w = 9
        If w > 42 Then w = 42
        ws.Columns(j).ColumnWidth = w
    Next j
    ws.Columns(2).ColumnWidth = Application.WorksheetFunction.Max(ws.Columns(2).ColumnWidth, 12)
End Sub

Private Sub SetupPage(ByVal ws As Worksheet, ByVal rep As cReport, ByVal lastRow As Long, ByVal nCols As Long)
    On Error Resume Next
    Application.PrintCommunication = False
    With ws.PageSetup
        .PrintArea = ws.Range(ws.Cells(1, 1), ws.Cells(lastRow, nCols + 1)).Address
        .PaperSize = xlPaperA4
        .Orientation = IIf(rep.IsLandscape(), xlLandscape, xlPortrait)
        .Zoom = False
        .FitToPagesWide = 1
        .FitToPagesTall = False
        .LeftMargin = Application.CentimetersToPoints(1)
        .RightMargin = Application.CentimetersToPoints(1)
        .TopMargin = Application.CentimetersToPoints(1.2)
        .BottomMargin = Application.CentimetersToPoints(1.4)
        .HeaderMargin = Application.CentimetersToPoints(0.5)
        .FooterMargin = Application.CentimetersToPoints(0.6)
        .CenterHorizontally = True
        .LeftFooter = "&8" & APP_NAME & " - " & Replace(rep.Title, "&", "&&")
        .CenterFooter = "&8Page &P / &N"
        .RightFooter = "&8Généré le " & Format$(Now, "dd/mm/yyyy hh:nn") & " par " & Replace(Sec_CurrentName(), "&", "&&")
    End With
    Application.PrintCommunication = True
End Sub

'------------------------------------------------------------------ Sorties
Public Function Rpt_ExportFolder() As String
    Rpt_ExportFolder = SubFolder("DOSSIER_EXPORTS", "Exports")
End Function

Private Function OutName(ByVal rep As cReport, ByVal ext As String) As String
    Dim stem As String
    stem = rep.FileStem
    If Len(stem) = 0 Then stem = rep.Title
    OutName = Rpt_ExportFolder() & Application.PathSeparator & SafeFileName(Replace(stem, " ", "_")) & "_" & Format$(Now, "yyyymmdd_hhnnss") & ext
End Function

Public Function Rpt_ToPDF(ByVal rep As cReport, Optional ByVal openAfter As Boolean = True, Optional ByVal ws As Worksheet) As String
    Dim p As String
    If ws Is Nothing Then Set ws = RptSheet()
    p = OutName(rep, ".pdf")
    ws.ExportAsFixedFormat Type:=xlTypePDF, Filename:=p, Quality:=xlQualityStandard, IncludeDocProperties:=True, _
                           IgnorePrintAreas:=False, OpenAfterPublish:=openAfter
    Log_Audit "EXPORT-PDF", "", "", rep.Title & " -> " & p
    Rpt_ToPDF = p
End Function

Public Function Rpt_ToXLSX(ByVal rep As cReport, Optional ByVal openAfter As Boolean = True) As String
    Dim wb As Workbook, p As String, prevAlerts As Boolean
    p = OutName(rep, ".xlsx")
    RptSheet().Copy
    Set wb = ActiveWorkbook
    prevAlerts = Application.DisplayAlerts
    Application.DisplayAlerts = False
    wb.SaveAs Filename:=p, FileFormat:=xlOpenXMLWorkbook
    Application.DisplayAlerts = prevAlerts
    If Not openAfter Then wb.Close SaveChanges:=False Else ThisWorkbook.Activate
    Log_Audit "EXPORT-EXCEL", "", "", rep.Title & " -> " & p
    Rpt_ToXLSX = p
End Function

Public Sub Rpt_Print(ByVal rep As cReport)
    RptSheet().PrintOut Copies:=1, Collate:=True
    Log_Audit "IMPRESSION", "", "", rep.Title
End Sub

' Sortie choisie par l'utilisateur : PREVIEW (PDF ouvert), PRINT, PDF, XLSX, SCREEN
Public Sub Rpt_Output(ByVal rep As cReport, ByVal mode As String)
    Dim p As String
    Rpt_Render rep
    Select Case mode
        Case "PREVIEW"
            Rpt_ToPDF rep, True
        Case "PRINT"
            Rpt_Print rep
            UI_Toast "Impression envoyée : " & rep.Title
        Case "PDF"
            p = Rpt_ToPDF(rep, True)
            UI_Alert "PDF enregistré :" & vbLf & p, "success", "Export PDF"
        Case "XLSX"
            p = Rpt_ToXLSX(rep, True)
            UI_Alert "Classeur Excel enregistré :" & vbLf & p & vbLf & "(ouvert dans une nouvelle fenêtre)", "success", "Export Excel"
        Case "SCREEN"
            RptSheet().Activate
            ActiveWindow.DisplayGridlines = False
            ActiveWindow.Zoom = 100
            ActiveWindow.ScrollRow = 1
    End Select
End Sub

' Choix de la sortie (fenetre)
Public Function Rpt_AskOutput(ByVal title As String) As String
    Dim f As frmInput
    Set f = Dlg_New("Sortie de l'état", title, CLR_TEAL(), 520)
    f.AddTile "PREVIEW", "Aperçu", "Ouvre l'aperçu PDF", CLR_TEAL()
    f.AddTile "PRINT", "Imprimer", "Imprimante par défaut", CLR_BLUE()
    f.AddTile "PDF", "Exporter en PDF", "Dossier Exports", CLR_RED()
    f.AddTile "XLSX", "Exporter en Excel", "Classeur .xlsx", CLR_GREEN()
    f.EndTiles
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    Rpt_AskOutput = f.ShowDialog
    f.Dispose
    If Rpt_AskOutput = "NO" Then Rpt_AskOutput = ""
End Function

'------------------------------------------------------------------ Listes de module
Private Function ListReport(ByVal modKey As String, ByVal host As Object) As cReport
    Dim rep As cReport, f As cFieldDef, hdr As String, typ As String, tot As String, data As Variant, i As Long, first As Boolean
    Set rep = New cReport
    rep.Title = "Liste - " & Mod_Get(modKey)("Titre")
    rep.Category = "Liste"
    rep.Subtitle = "Situation au " & FmtDate(App_Today())
    rep.Signatures = False
    If host Is Nothing Then Fail "Liste indisponible."
    rep.Filters = host.FilterText()
    first = True
    For Each f In host.VisibleFields()
        hdr = hdr & IIf(first, "", "|") & f.Label
        typ = typ & IIf(first, "", "|") & "T"
        tot = tot & IIf(first, "L", "|0")
        first = False
    Next f
    data = host.VisibleData()
    rep.AddTable "", hdr, typ, data
    Set ListReport = rep
End Function

Public Sub Rpt_PrintList(ByVal modKey As String, ByVal host As Object)
    Dim mode As String, rep As cReport
    Set rep = ListReport(modKey, host)
    mode = Rpt_AskOutput(rep.Title)
    If Len(mode) > 0 Then Rpt_Output rep, mode
End Sub

Public Sub Rpt_ExportList(ByVal modKey As String, ByVal host As Object)
    Rpt_Output ListReport(modKey, host), "XLSX"
End Sub
