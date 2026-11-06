Attribute VB_Name = "mUI_Kit"
'==============================================================================
' MINING TRANSIT 360 - mUI_Kit
' Construction des controles des fenetres (UserForms) a l'execution, dans la
' charte graphique de la maquette : bandeau marine, boutons colores plats,
' cartes blanches, police Segoe UI.
' Les fenetres passent "Me" (As Object) et leur collection de relais d'evenements.
'==============================================================================
Option Explicit

Public Const PT_PAD As Single = 12

Private Sub StyleFont(ByVal c As Object, ByVal size As Single, Optional ByVal bold As Boolean = False)
    c.Font.Name = UI_FONT()
    c.Font.size = size
    c.Font.bold = bold
End Sub

Public Function Kit_Label(ByVal frm As Object, ByVal name As String, ByVal caption As String, _
                          ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                          Optional ByVal size As Single = 9, Optional ByVal bold As Boolean = False, _
                          Optional ByVal foreColor As Long = -1, Optional ByVal backColor As Long = -1, _
                          Optional ByVal align As Long = 1) As Object
    Dim c As Object
    Set c = frm.Controls.Add("Forms.Label.1", name, True)
    c.Left = l: c.Top = t: c.Width = w: c.Height = h
    c.Caption = caption
    c.WordWrap = True
    StyleFont c, size, bold
    If foreColor = -1 Then c.foreColor = CLR_TEXT() Else c.foreColor = foreColor
    If backColor = -1 Then
        c.BackStyle = 0          ' fmBackStyleTransparent
    Else
        c.BackStyle = 1
        c.backColor = backColor
    End If
    c.TextAlign = align          ' 1 gauche, 2 centre, 3 droite
    Set Kit_Label = c
End Function

' Panneau (rectangle de fond) : label vide colore, avec bordure optionnelle
Public Function Kit_Panel(ByVal frm As Object, ByVal name As String, ByVal l As Single, ByVal t As Single, _
                          ByVal w As Single, ByVal h As Single, ByVal backColor As Long, _
                          Optional ByVal borderColor As Long = -1) As Object
    Dim c As Object
    Set c = Kit_Label(frm, name, "", l, t, w, h, 9, False, -1, backColor)
    If borderColor <> -1 Then
        c.BorderStyle = 1
        c.borderColor = borderColor
    End If
    Set Kit_Panel = c
End Function

Public Function Kit_Button(ByVal frm As Object, ByVal handlers As Collection, ByVal key As String, ByVal caption As String, _
                           ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                           Optional ByVal backColor As Long = -1, Optional ByVal foreColor As Long = -1, _
                           Optional ByVal size As Single = 9) As Object
    Dim c As Object, ev As cCtlEvents
    Set c = frm.Controls.Add("Forms.CommandButton.1", "btn_" & key, True)
    c.Left = l: c.Top = t: c.Width = w: c.Height = h
    c.Caption = caption
    c.WordWrap = True
    StyleFont c, size, True
    If backColor = -1 Then backColor = CLR_BLUE()
    If foreColor = -1 Then foreColor = CLR_WHITE()
    c.backColor = backColor
    c.foreColor = foreColor
    c.TakeFocusOnClick = False
    Set ev = New cCtlEvents
    ev.Bind frm, key, c
    handlers.Add ev
    Set Kit_Button = c
End Function

' Label cliquable (lien / onglet)
Public Function Kit_Link(ByVal frm As Object, ByVal handlers As Collection, ByVal key As String, ByVal caption As String, _
                         ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                         Optional ByVal size As Single = 9, Optional ByVal bold As Boolean = False, _
                         Optional ByVal foreColor As Long = -1, Optional ByVal backColor As Long = -1, _
                         Optional ByVal align As Long = 1) As Object
    Dim c As Object, ev As cCtlEvents
    Set c = Kit_Label(frm, "lnk_" & key, caption, l, t, w, h, size, bold, foreColor, backColor, align)
    Set ev = New cCtlEvents
    ev.Bind frm, key, c
    handlers.Add ev
    Set Kit_Link = c
End Function

Public Function Kit_TextBox(ByVal frm As Object, ByVal handlers As Collection, ByVal key As String, _
                            ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                            Optional ByVal value As String = "", Optional ByVal multiline As Boolean = False, _
                            Optional ByVal locked As Boolean = False, Optional ByVal pwdChar As String = "") As Object
    Dim c As Object, ev As cCtlEvents
    Set c = frm.Controls.Add("Forms.TextBox.1", "txt_" & key, True)
    c.Left = l: c.Top = t: c.Width = w: c.Height = h
    StyleFont c, 10
    c.SpecialEffect = 0            ' plat
    c.BorderStyle = 1
    c.borderColor = CLR_LINE()
    If multiline Then
        c.multiline = True
        c.WordWrap = True
        c.EnterKeyBehavior = True
        c.ScrollBars = 2
    End If
    If Len(pwdChar) > 0 Then c.PasswordChar = pwdChar
    c.Text = value
    Kit_SetLocked c, locked
    Set ev = New cCtlEvents
    ev.Bind frm, key, c
    handlers.Add ev
    Set Kit_TextBox = c
End Function

Public Sub Kit_SetLocked(ByVal c As Object, ByVal locked As Boolean)
    c.locked = locked
    If locked Then
        c.backColor = RGB(241, 244, 249)
        c.foreColor = CLR_MUTED()
    Else
        c.backColor = CLR_WHITE()
        c.foreColor = CLR_TEXT()
    End If
End Sub

Public Function Kit_Combo(ByVal frm As Object, ByVal handlers As Collection, ByVal key As String, _
                          ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                          ByVal items As Variant, Optional ByVal value As String = "", _
                          Optional ByVal strict As Boolean = True, Optional ByVal locked As Boolean = False) As Object
    Dim c As Object, ev As cCtlEvents, i As Long
    Set c = frm.Controls.Add("Forms.ComboBox.1", "cbo_" & key, True)
    c.Left = l: c.Top = t: c.Width = w: c.Height = h
    StyleFont c, 10
    c.SpecialEffect = 0
    c.BorderStyle = 1
    c.borderColor = CLR_LINE()
    c.MatchEntry = 1               ' fmMatchEntryComplete
    c.ListRows = 14
    If IsArray(items) Then
        For i = LBound(items) To UBound(items)
            c.AddItem CStr(items(i))
        Next i
    End If
    If strict Then c.Style = 2 Else c.Style = 0      ' 2 = fmStyleDropDownList
    On Error Resume Next
    If Len(value) > 0 Then c.value = value
    On Error GoTo 0
    Kit_SetLocked c, locked
    Set ev = New cCtlEvents
    ev.Bind frm, key, c
    handlers.Add ev
    Set Kit_Combo = c
End Function

Public Function Kit_ListBox(ByVal frm As Object, ByVal handlers As Collection, ByVal key As String, _
                            ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                            ByVal colCount As Long, ByVal colWidths As String) As Object
    Dim c As Object, ev As cCtlEvents
    Set c = frm.Controls.Add("Forms.ListBox.1", "lst_" & key, True)
    c.Left = l: c.Top = t: c.Width = w: c.Height = h
    StyleFont c, 9
    c.SpecialEffect = 0
    c.BorderStyle = 1
    c.borderColor = CLR_LINE()
    c.ColumnCount = colCount
    c.ColumnWidths = colWidths
    c.IntegralHeight = False
    Set ev = New cCtlEvents
    ev.Bind frm, key, c
    handlers.Add ev
    Set Kit_ListBox = c
End Function

Public Function Kit_Check(ByVal frm As Object, ByVal handlers As Collection, ByVal key As String, ByVal caption As String, _
                          ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                          Optional ByVal checked As Boolean = False) As Object
    Dim c As Object, ev As cCtlEvents
    Set c = frm.Controls.Add("Forms.CheckBox.1", "chk_" & key, True)
    c.Left = l: c.Top = t: c.Width = w: c.Height = h
    c.Caption = caption
    StyleFont c, 9
    c.BackStyle = 0
    c.value = checked
    Set ev = New cCtlEvents
    ev.Bind frm, key, c
    handlers.Add ev
    Set Kit_Check = c
End Function

' Bandeau superieur commun a toutes les fenetres (identite de la maquette)
Public Sub Kit_Header(ByVal frm As Object, ByVal title As String, ByVal subtitle As String, _
                      ByVal accent As Long, ByVal width As Single, Optional ByVal height As Single = 54)
    Kit_Panel frm, "hdr_bg", 0, 0, width, height, CLR_NAVY()
    Kit_Panel frm, "hdr_accent", 0, height - 4, width, 4, accent
    Kit_Label frm, "hdr_logo", "MT360", PT_PAD, 10, 54, 30, 13, True, CLR_ORANGE()
    Kit_Label frm, "hdr_title", title, PT_PAD + 62, 7, width - 300, 22, 14, True, CLR_WHITE()
    Kit_Label frm, "hdr_sub", subtitle, PT_PAD + 62, 29, width - 300, 16, 9, False, RGB(190, 205, 230)
    Kit_Label frm, "hdr_user", Sec_CurrentName() & "  |  " & Format$(App_Today(), "dd/mm/yyyy"), _
              width - 240, 18, 228, 16, 8, False, RGB(190, 205, 230), -1, 3
End Sub

Public Sub Kit_ReleaseAll(ByVal handlers As Collection)
    Dim ev As cCtlEvents
    If handlers Is Nothing Then Exit Sub
    For Each ev In handlers
        ev.Unbind
    Next ev
End Sub

' Couleur plus sombre (survol / bordures)
Public Function Darken(ByVal c As Long, Optional ByVal pct As Double = 0.15) As Long
    Dim r As Long, g As Long, b As Long
    r = c Mod 256: g = (c \ 256) Mod 256: b = (c \ 65536) Mod 256
    Darken = RGB(r * (1 - pct), g * (1 - pct), b * (1 - pct))
End Function

' Ajuste la hauteur interieure d'un UserForm (InsideHeight non modifiable directement)
Public Sub Kit_SizeForm(ByVal frm As Object, ByVal insideW As Single, ByVal insideH As Single)
    frm.Width = insideW + (frm.Width - frm.InsideWidth)
    frm.Height = insideH + (frm.Height - frm.InsideHeight)
End Sub
