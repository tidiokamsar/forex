Attribute VB_Name = "mRpt_Invoice"
'==============================================================================
' MINING TRANSIT 360 - mRpt_Invoice
' Document facture (feuille FACTURE) : logo, coordonnees, client, periode,
' designation, quantites, prix unitaires, totaux HT / TVA / TTC, montant en
' lettres, conditions, coordonnees bancaires, mentions, signature, annexe des
' rotations facturees. Filigrane BROUILLON / ANNULEE. Export PDF.
'==============================================================================
Option Explicit

Private Function InvSheet() As Worksheet
    Set InvSheet = ThisWorkbook.Worksheets("FACTURE")
End Function

Public Sub Inv_Render(ByVal invId As Long)
    Dim ws As Worksheet, f As Object, c As Object, r As Long, rows As Collection, lr As Variant, data As Variant, s As Shape, fl As String
    Dim i As Long, wm As String
    Set f = DB_Get("T_FACTURES", invId)
    If f Is Nothing Then Fail "Facture introuvable."
    Set c = DB_Get("T_CLIENTS", f("ClientID"))
    Set ws = InvSheet()
    ScreenOff
    ws.Unprotect
    ws.Cells.Clear
    UI_DeleteShapes ws
    ws.Cells.Font.Name = UI_FONT()
    ws.Cells.Font.size = 9.5
    ws.Columns("A").ColumnWidth = 2
    ws.Columns("B").ColumnWidth = 46
    ws.Columns("C").ColumnWidth = 12
    ws.Columns("D").ColumnWidth = 7
    ws.Columns("E").ColumnWidth = 14
    ws.Columns("F").ColumnWidth = 18
    ws.Columns("G").ColumnWidth = 2
    ' --- en-tete ---
    fl = Assets_Path() & "\logo.png"
    If FileExists(fl) Then
        Set s = ws.Shapes.AddPicture(fl, msoFalse, msoTrue, ws.Range("B1").Left, 4, 92, 58)
        s.Name = "inv_logo"
    End If
    ws.Range("B5").Value = Company()
    ws.Range("B5").Font.size = 13: ws.Range("B5").Font.Bold = True: ws.Range("B5").Font.Color = CLR_NAVY()
    ws.Range("B6").Value = Param_Get("SOCIETE_ADRESSE", "")
    ws.Range("B7").Value = "Tél. " & Param_Get("SOCIETE_TEL", "") & "  -  " & Param_Get("SOCIETE_EMAIL", "")
    ws.Range("B8").Value = Param_Get("SOCIETE_NIF", "")
    ws.Range("B6:B8").Font.Color = CLR_MUTED(): ws.Range("B6:B8").Font.size = 8.5
    ws.Range("E1:F1").Merge
    ws.Range("E1").Value = "FACTURE"
    ws.Range("E1").Font.size = 24: ws.Range("E1").Font.Bold = True: ws.Range("E1").Font.Color = CLR_NAVY()
    ws.Range("E1").HorizontalAlignment = xlRight
    ws.Rows(1).RowHeight = 34
    InvKV ws, 2, "N° facture", f("NumFacture")
    InvKV ws, 3, "Date", FmtDate(f("DateFacture"))
    InvKV ws, 4, "Échéance", FmtDate(f("Echeance"))
    InvKV ws, 5, "Contrat", ToStr(f("RefContrat"))
    ' --- client ---
    With ws.Range("D7:F7")
        .Merge
        .Value = "FACTURÉ À"
        .Interior.Color = CLR_NAVY(): .Font.Color = CLR_WHITE(): .Font.Bold = True
    End With
    ws.Range("D8").Value = c("RaisonSociale"): ws.Range("D8").Font.Bold = True
    ws.Range("D9").Value = c("Adresse")
    ws.Range("D10").Value = "NIF : " & c("NIF")
    ws.Range("D11").Value = "À l'attention de : " & c("Contact")
    ' --- objet ---
    r = 13
    ws.Range("B" & r).Value = "Objet : transport de bauxite - période du " & FmtDate(f("PeriodeDebut")) & " au " & FmtDate(f("PeriodeFin")) & _
                              " (" & f("NbRotations") & " rotations validées)"
    ws.Range("B" & r).Font.Bold = True
    r = 15
    ws.Range("B" & r & ":F" & r).Value = Array("Désignation", "Quantité", "Unité", "Prix unitaire", "Montant (GNF)")
    With ws.Range("B" & r & ":F" & r)
        .Interior.Color = CLR_NAVY2(): .Font.Color = CLR_WHITE(): .Font.Bold = True
        .HorizontalAlignment = xlCenter: .RowHeight = 22: .VerticalAlignment = xlCenter
    End With
    Set rows = DB_Rows("T_FACTURE_LIGNES", "FactureID", "=", invId)
    data = DB_Data("T_FACTURE_LIGNES")
    For Each lr In rows
        r = r + 1
        ws.Range("B" & r).Value = data(lr, DB_ColIdx("T_FACTURE_LIGNES", "Designation"))
        ws.Range("B" & r).WrapText = True
        ws.Range("C" & r).Value = data(lr, DB_ColIdx("T_FACTURE_LIGNES", "Quantite")): ws.Range("C" & r).NumberFormat = "#,##0.00"
        ws.Range("D" & r).Value = data(lr, DB_ColIdx("T_FACTURE_LIGNES", "Unite")): ws.Range("D" & r).HorizontalAlignment = xlCenter
        ws.Range("E" & r).Value = data(lr, DB_ColIdx("T_FACTURE_LIGNES", "PU")): ws.Range("E" & r).NumberFormat = "#,##0"
        ws.Range("F" & r).Value = data(lr, DB_ColIdx("T_FACTURE_LIGNES", "Montant")): ws.Range("F" & r).NumberFormat = "#,##0"
        ws.Range("B" & r & ":F" & r).Borders(xlEdgeBottom).Color = CLR_LINE()
        ws.Rows(r).RowHeight = 30
        ws.Range("B" & r & ":F" & r).VerticalAlignment = xlCenter
    Next lr
    ' --- totaux ---
    r = r + 2
    InvTotal ws, r, "Total HT", f("MontantHT"), False
    InvTotal ws, r + 1, "TVA (" & FmtPct(ToDbl(f("TauxTVA")), 0) & ")", f("MontantTVA"), False
    InvTotal ws, r + 2, "TOTAL TTC (GNF)", f("MontantTTC"), True
    If ToDbl(f("MontantPaye")) > 0 Then
        InvTotal ws, r + 3, "Déjà réglé", f("MontantPaye"), False
        InvTotal ws, r + 4, "Reste à payer", f("Solde"), True
    End If
    r = r + 6
    ws.Range("B" & r).Value = "Arrêtée la présente facture à la somme de : " & NumberToWordsFR(ToDbl(f("MontantTTC"))) & " francs guinéens."
    ws.Range("B" & r & ":F" & r).Merge
    ws.Range("B" & r).WrapText = True: ws.Range("B" & r).Font.Italic = True: ws.Rows(r).RowHeight = 30
    r = r + 2
    ws.Range("B" & r).Value = "Conditions de paiement : " & ToStr(f("ConditionsPaiement"))
    ws.Range("B" & r + 1).Value = "Règlement : " & Param_Get("SOCIETE_BANQUE", "")
    ws.Range("B" & r + 2).Value = ToStr(Nz(f("Mentions"), Param_Get("FACT_MENTIONS", "")))
    ws.Range("B" & r & ":B" & r + 2).Font.size = 8.5
    ws.Range("B" & r + 2).WrapText = False
    ' --- signature ---
    ws.Range("E" & r + 4).Value = Param_Get("FACT_SIGNATAIRE", "La Direction")
    ws.Range("E" & r + 4).Font.Bold = True
    ws.Range("E" & r + 5 & ":F" & r + 8).Borders(xlEdgeBottom).Color = CLR_GREY()
    ws.Range("B" & r + 4).Value = "Cachet de l'entreprise"
    ws.Range("B" & r + 4).Font.Color = CLR_MUTED()
    r = r + 10
    ' --- annexe : rotations facturees par jour ---
    Dim lastPage As Long
    lastPage = r
    ws.HPageBreaks.Add ws.Range("A" & r)
    ws.Range("B" & r).Value = "ANNEXE - Récapitulatif des rotations facturées (" & f("NumFacture") & ")"
    ws.Range("B" & r).Font.Bold = True: ws.Range("B" & r).Font.size = 11: ws.Range("B" & r).Font.Color = CLR_NAVY()
    r = r + 2
    ws.Range("B" & r & ":E" & r).Value = Array("Date", "Rotations", "", "Tonnage (t)")
    ws.Range("B" & r & ":E" & r).Interior.Color = CLR_NAVY2(): ws.Range("B" & r & ":E" & r).Font.Color = CLR_WHITE(): ws.Range("B" & r & ":E" & r).Font.Bold = True
    Dim d As Date, n As Long, t As Double, totN As Long, totT As Double
    For d = ToDate(f("PeriodeDebut")) To ToDate(f("PeriodeFin"))
        n = DB_CountIf("T_ROTATIONS", "FactureID", "=", invId, "DateRotation", "=", d)
        If n > 0 Then
            r = r + 1
            t = DB_Sum("T_ROTATIONS", "PoidsNet", "FactureID", "=", invId, "DateRotation", "=", d)
            ws.Range("B" & r).Value = d: ws.Range("B" & r).NumberFormat = "dddd dd/mm/yyyy": ws.Range("B" & r).HorizontalAlignment = xlLeft
            ws.Range("C" & r).Value = n
            ws.Range("E" & r).Value = t: ws.Range("E" & r).NumberFormat = "#,##0.00"
            totN = totN + n: totT = totT + t
        End If
    Next d
    r = r + 1
    ws.Range("B" & r).Value = "TOTAL"
    ws.Range("C" & r).Value = totN
    ws.Range("E" & r).Value = totT: ws.Range("E" & r).NumberFormat = "#,##0.00"
    ws.Range("B" & r & ":E" & r).Font.Bold = True
    ws.Range("B" & r & ":E" & r).Borders(xlEdgeTop).Color = CLR_ORANGE()
    ' --- filigrane ---
    Select Case f("Statut")
        Case FAC_BROUILLON: wm = "BROUILLON"
        Case FAC_ANNULEE: wm = "ANNULÉE"
    End Select
    If Param_Bool("DEMO_MODE", False) And Len(wm) = 0 Then wm = "DÉMO"
    If Len(wm) > 0 Then
        Set s = ws.Shapes.AddTextEffect(msoTextEffect1, wm, "Arial Black", 60, msoTrue, msoFalse, ws.Range("B20").Left, ws.Range("B20").Top)
        s.Name = "inv_wm"
        s.Rotation = -30
        s.Fill.ForeColor.RGB = IIf(wm = "DÉMO", RGB(255, 200, 150), RGB(255, 120, 120))
        s.Fill.Transparency = 0.7
        s.Line.Visible = msoFalse
    End If
    ' --- mise en page ---
    On Error Resume Next
    Application.PrintCommunication = False
    With ws.PageSetup
        .PrintArea = "A1:G" & r + 1
        .PaperSize = xlPaperA4
        .Orientation = xlPortrait
        .Zoom = False
        .FitToPagesWide = 1
        .FitToPagesTall = False
        .CenterHorizontally = True
        .LeftMargin = Application.CentimetersToPoints(1.2)
        .RightMargin = Application.CentimetersToPoints(1.2)
        .TopMargin = Application.CentimetersToPoints(1.2)
        .BottomMargin = Application.CentimetersToPoints(1.5)
        .CenterFooter = "&8" & Replace(Company(), "&", "&&") & " - " & Replace(ToStr(Param_Get("SOCIETE_NIF", "")), "&", "&&") & "   |   Page &P / &N"
    End With
    Application.PrintCommunication = True
    On Error GoTo 0
    Rpt_AddBackButton ws, 6
    ScreenOn
End Sub

Private Sub InvKV(ByVal ws As Worksheet, ByVal r As Long, ByVal k As String, ByVal v As String)
    ws.Range("E" & r).Value = k
    ws.Range("E" & r).Font.Color = CLR_MUTED()
    ws.Range("E" & r).HorizontalAlignment = xlRight
    ws.Range("F" & r).Value = "'" & v
    ws.Range("F" & r).Font.Bold = True
    ws.Range("F" & r).HorizontalAlignment = xlRight
End Sub

Private Sub InvTotal(ByVal ws As Worksheet, ByVal r As Long, ByVal label As String, ByVal v As Variant, ByVal strong As Boolean)
    ws.Range("D" & r & ":E" & r).Merge
    ws.Range("D" & r).Value = label
    ws.Range("D" & r).HorizontalAlignment = xlRight
    ws.Range("F" & r).Value = ToDbl(v)
    ws.Range("F" & r).NumberFormat = "#,##0"
    If strong Then
        ws.Range("D" & r & ":F" & r).Font.Bold = True
        ws.Range("D" & r & ":F" & r).Interior.Color = RGB(255, 241, 224)
        ws.Range("D" & r & ":F" & r).Borders(xlEdgeTop).Color = CLR_ORANGE()
    End If
End Sub

Public Sub Inv_Preview(ByVal invId As Long)
    Inv_Render invId
    Inv_ExportPDF invId, True, True
End Sub

Public Function Inv_ExportPDF(ByVal invId As Long, Optional ByVal openAfter As Boolean = True, Optional ByVal alreadyRendered As Boolean = False) As String
    Dim p As String
    If Not alreadyRendered Then Inv_Render invId
    p = Rpt_ExportFolder() & Application.PathSeparator & SafeFileName(ToStr(DB_Value("T_FACTURES", invId, "NumFacture"))) & "_" & _
        CodeOf("T_CLIENTS", DB_Value("T_FACTURES", invId, "ClientID")) & ".pdf"
    InvSheet().ExportAsFixedFormat Type:=xlTypePDF, Filename:=p, Quality:=xlQualityStandard, IncludeDocProperties:=True, _
                                    IgnorePrintAreas:=False, OpenAfterPublish:=openAfter
    Log_Audit "FACTURE-PDF", "T_FACTURES", invId, p
    Inv_ExportPDF = p
End Function

'------------------------------------------------------------------ Montant en lettres (francais)
Public Function NumberToWordsFR(ByVal v As Double) As String
    Dim n As Double, s As String, groups As Variant, names As Variant, i As Long, g As Long, part As String
    n = Int(Abs(Round(v, 0)))
    If n = 0 Then NumberToWordsFR = "zéro": Exit Function
    names = Array("", "mille", "million", "milliard")
    For i = 3 To 0 Step -1
        g = Int(n / (1000# ^ i)) - Int(n / (1000# ^ (i + 1))) * 1000
        If g > 0 Then
            If i = 1 Then
                If g = 1 Then part = "mille" Else part = Under1000(g, False) & " mille"
            ElseIf i = 0 Then
                part = Under1000(g, True)
            Else
                part = Under1000(g, False) & " " & names(i) & IIf(g > 1, "s", "")
            End If
            s = s & IIf(Len(s) > 0, " ", "") & part
        End If
    Next i
    NumberToWordsFR = s
End Function

Private Function Under1000(ByVal n As Long, ByVal isLast As Boolean) As String
    Dim c As Long, rest As Long, s As String
    c = n \ 100: rest = n Mod 100
    If c > 0 Then
        If c = 1 Then s = "cent" Else s = Units(c) & " cent"
        If c > 1 And rest = 0 And isLast Then s = s & "s"
    End If
    If rest > 0 Then s = s & IIf(Len(s) > 0, " ", "") & Under100(rest)
    Under1000 = s
End Function

Private Function Under100(ByVal n As Long) As String
    Dim t As Long, u As Long, tens As Variant
    tens = Array("", "dix", "vingt", "trente", "quarante", "cinquante", "soixante", "soixante", "quatre-vingt", "quatre-vingt")
    If n < 17 Then Under100 = Units(n): Exit Function
    t = n \ 10: u = n Mod 10
    Select Case t
        Case 1: Under100 = "dix-" & Units(u)
        Case 7, 9: Under100 = tens(t) & IIf(t = 7 And u = 1, " et ", "-") & Units(10 + u)
        Case 8: Under100 = "quatre-vingt" & IIf(u = 0, "s", "-" & Units(u))
        Case Else
            If u = 0 Then
                Under100 = tens(t)
            ElseIf u = 1 Then
                Under100 = tens(t) & " et un"
            Else
                Under100 = tens(t) & "-" & Units(u)
            End If
    End Select
End Function

Private Function Units(ByVal n As Long) As String
    Units = Choose(n + 1, "", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze", "treize", _
                   "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf")
End Function
