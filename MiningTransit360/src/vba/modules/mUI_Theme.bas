Attribute VB_Name = "mUI_Theme"
'==============================================================================
' MINING TRANSIT 360 - mUI_Theme
' Primitives graphiques pour les feuilles (formes Excel) : rectangles arrondis,
' degrades, textes, icones, boutons. Les coordonnees sont exprimees dans le
' repere de la maquette (pixels, 1672 x 906 hors barre de titre) et converties
' en points (x 0,75) : la fidelite des proportions est ainsi garantie.
'==============================================================================
Option Explicit

Public Const MOCK_SCALE As Double = 0.75

Public Function PX(ByVal v As Double) As Double
    PX = v * MOCK_SCALE
End Function

Public Sub UI_DeleteShapes(ByVal ws As Worksheet, Optional ByVal prefix As String = "")
    Dim i As Long
    For i = ws.Shapes.Count To 1 Step -1
        If Len(prefix) = 0 Or Left$(ws.Shapes(i).Name, Len(prefix)) = prefix Then ws.Shapes(i).Delete
    Next i
End Sub

Public Function UI_ShapeExists(ByVal ws As Worksheet, ByVal name As String) As Boolean
    Dim s As Shape
    On Error Resume Next
    Set s = ws.Shapes(name)
    UI_ShapeExists = Not s Is Nothing
End Function

' Rectangle (arrondi si radius > 0), coordonnees maquette
Public Function UI_Box(ByVal ws As Worksheet, ByVal name As String, ByVal x As Double, ByVal y As Double, ByVal w As Double, ByVal h As Double, _
                       ByVal fillColor As Long, Optional ByVal radius As Double = 0, Optional ByVal lineColor As Long = -1, _
                       Optional ByVal gradTo As Long = -1, Optional ByVal shadow As Boolean = False) As Shape
    Dim s As Shape
    If radius > 0 Then
        Set s = ws.Shapes.AddShape(msoShapeRoundedRectangle, PX(x), PX(y), PX(w), PX(h))
        On Error Resume Next
        s.Adjustments.Item(1) = Application.WorksheetFunction.Min(0.5, radius / Application.WorksheetFunction.Min(w, h))
        On Error GoTo 0
    Else
        Set s = ws.Shapes.AddShape(msoShapeRectangle, PX(x), PX(y), PX(w), PX(h))
    End If
    s.Name = name
    s.Fill.Solid
    s.Fill.ForeColor.RGB = fillColor
    If gradTo <> -1 Then
        s.Fill.TwoColorGradient msoGradientVertical, 1
        s.Fill.ForeColor.RGB = fillColor
        s.Fill.BackColor.RGB = gradTo
    End If
    If lineColor = -1 Then
        s.Line.Visible = msoFalse
    Else
        s.Line.Visible = msoTrue
        s.Line.ForeColor.RGB = lineColor
        s.Line.Weight = 0.75
    End If
    If shadow Then
        s.Shadow.Visible = msoTrue
        s.Shadow.OffsetX = 0
        s.Shadow.OffsetY = 1.5
        s.Shadow.Blur = 4
        s.Shadow.Transparency = 0.82
        s.Shadow.ForeColor.RGB = RGB(20, 40, 80)
    Else
        s.Shadow.Visible = msoFalse
    End If
    s.Placement = xlFreeFloating
    Set UI_Box = s
End Function

' Zone de texte transparente. align : 1 gauche, 2 centre, 3 droite
Public Function UI_Text(ByVal ws As Worksheet, ByVal name As String, ByVal x As Double, ByVal y As Double, ByVal w As Double, ByVal h As Double, _
                        ByVal txt As String, Optional ByVal sizePx As Double = 14, Optional ByVal color As Long = 0, _
                        Optional ByVal bold As Boolean = False, Optional ByVal align As Long = 1, Optional ByVal italic As Boolean = False, _
                        Optional ByVal vCenter As Boolean = True) As Shape
    Dim s As Shape
    Set s = ws.Shapes.AddTextbox(msoTextOrientationHorizontal, PX(x), PX(y), PX(w), PX(h))
    s.Name = name
    s.Fill.Visible = msoFalse
    s.Line.Visible = msoFalse
    With s.TextFrame2
        .MarginLeft = 0: .MarginRight = 0: .MarginTop = 0: .MarginBottom = 0
        .WordWrap = msoTrue
        .AutoSize = msoAutoSizeNone
        If vCenter Then .VerticalAnchor = msoAnchorMiddle Else .VerticalAnchor = msoAnchorTop
        .TextRange.Text = txt
        .TextRange.Font.Name = UI_FONT()
        .TextRange.Font.size = PX(sizePx)
        .TextRange.Font.bold = IIf(bold, msoTrue, msoFalse)
        .TextRange.Font.italic = IIf(italic, msoTrue, msoFalse)
        .TextRange.Font.Fill.ForeColor.RGB = color
        Select Case align
            Case 2: .TextRange.ParagraphFormat.Alignment = msoAlignCenter
            Case 3: .TextRange.ParagraphFormat.Alignment = msoAlignRight
            Case Else: .TextRange.ParagraphFormat.Alignment = msoAlignLeft
        End Select
    End With
    s.Placement = xlFreeFloating
    Set UI_Text = s
End Function

' Ecrit un texte dans une forme existante (si absente : ignore)
Public Sub UI_SetText(ByVal ws As Worksheet, ByVal name As String, ByVal txt As String, Optional ByVal color As Long = -1)
    Dim s As Shape
    On Error Resume Next
    Set s = ws.Shapes(name)
    On Error GoTo 0
    If s Is Nothing Then Exit Sub
    s.TextFrame2.TextRange.Text = txt
    If color <> -1 Then s.TextFrame2.TextRange.Font.Fill.ForeColor.RGB = color
End Sub

' Texte dans une forme pleine (bouton, pastille)
Public Sub UI_ShapeText(ByVal s As Shape, ByVal txt As String, ByVal sizePx As Double, ByVal color As Long, _
                        Optional ByVal bold As Boolean = True, Optional ByVal align As Long = 2)
    With s.TextFrame2
        .MarginLeft = PX(6): .MarginRight = PX(6): .MarginTop = 0: .MarginBottom = 0
        .VerticalAnchor = msoAnchorMiddle
        .WordWrap = msoTrue
        .TextRange.Text = txt
        .TextRange.Font.Name = UI_FONT()
        .TextRange.Font.size = PX(sizePx)
        .TextRange.Font.bold = IIf(bold, msoTrue, msoFalse)
        .TextRange.Font.Fill.ForeColor.RGB = color
        Select Case align
            Case 1: .TextRange.ParagraphFormat.Alignment = msoAlignLeft
            Case 3: .TextRange.ParagraphFormat.Alignment = msoAlignRight
            Case Else: .TextRange.ParagraphFormat.Alignment = msoAlignCenter
        End Select
    End With
End Sub

' Icone PNG (assets\icons\<nom>.png) ; repli : copie depuis la feuille ASSETS ; sinon pastille texte
Public Function UI_Icon(ByVal ws As Worksheet, ByVal name As String, ByVal iconName As String, ByVal x As Double, ByVal y As Double, _
                        ByVal size As Double) As Shape
    Dim s As Shape, f As String, src As Shape
    f = Assets_Path() & "\icons\" & iconName & ".png"
    If FileExists(f) Then
        Set s = ws.Shapes.AddPicture(f, msoFalse, msoTrue, PX(x), PX(y), PX(size), PX(size))
    Else
        On Error Resume Next
        Set src = ThisWorkbook.Worksheets("ASSETS").Shapes("ico_" & iconName)
        On Error GoTo 0
        If Not src Is Nothing Then
            src.Copy
            ws.Paste
            Set s = ws.Shapes(ws.Shapes.Count)
            s.Left = PX(x): s.Top = PX(y): s.Width = PX(size): s.Height = PX(size)
        Else
            Set s = UI_Text(ws, name, x, y, size, size, ChrW(9632), size * 0.8, CLR_WHITE(), True, 2)
        End If
    End If
    s.Name = name
    s.LockAspectRatio = msoTrue
    s.Placement = xlFreeFloating
    Set UI_Icon = s
End Function

' Bouton (forme cliquable avec macro)
Public Function UI_Button(ByVal ws As Worksheet, ByVal name As String, ByVal x As Double, ByVal y As Double, ByVal w As Double, ByVal h As Double, _
                          ByVal caption As String, ByVal color As Long, ByVal macro As String, Optional ByVal sizePx As Double = 16, _
                          Optional ByVal radius As Double = 10, Optional ByVal gradTo As Long = -1, Optional ByVal textColor As Long = -1) As Shape
    Dim s As Shape
    Set s = UI_Box(ws, name, x, y, w, h, color, radius, -1, gradTo, True)
    If textColor = -1 Then textColor = CLR_WHITE()
    UI_ShapeText s, caption, sizePx, textColor, True, 2
    s.OnAction = macro
    Set UI_Button = s
End Function

' Dossier des ressources (assets) : a cote du classeur ou dans le dossier parent
Public Function Assets_Path() As String
    Dim p As Variant
    For Each p In Array(AppFolder() & "\assets", AppFolder() & "\..\assets")
        If FSO().FolderExists(CStr(p)) Then Assets_Path = CStr(p): Exit Function
    Next p
    Assets_Path = AppFolder() & "\assets"
End Function

' Groupe de formes cliquables : meme macro pour plusieurs formes
Public Sub UI_SetAction(ByVal ws As Worksheet, ByVal macro As String, ParamArray names() As Variant)
    Dim n As Variant
    For Each n In names
        On Error Resume Next
        ws.Shapes(CStr(n)).OnAction = macro
        On Error GoTo 0
    Next n
End Sub
