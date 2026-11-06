VERSION 5.00
Begin {C62A69F0-16DC-11CE-9E98-00AA00574A4F} frmInput
   Caption         =   "MINING TRANSIT 360"
   ClientHeight    =   6000
   ClientWidth     =   9600
   StartUpPosition =   1  'CenterOwner
End
Attribute VB_Name = "frmInput"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - frmInput
' Fenetre de dialogue generique construite a l'execution :
'   alertes, confirmations, saisie de motif, selection de periode, saisies
'   metier courtes (mouvement de pneu, paiement, generation de facture...),
'   fiches de consultation (blocs INFO + grilles) et menus (hubs).
' Utilisation : voir mUI_Dialogs.
'==============================================================================
Option Explicit

Private mHandlers As Collection
Private mFields As Object          ' cle -> dictionnaire (type, ctl, required, label)
Private mOrder As Collection
Private mActions As Collection     ' cles d'actions
Private mValidate As Object        ' action -> valide les champs ?
Private mDefaultAction As String
Private mCancelAction As String
Private mResult As String
Private mY As Single
Private mW As Single
Private mLabelW As Single
Private mAccent As Long
Private mMsg As Object
Private mTiles As Long
Private mSeq As Long

Private Function NextName(ByVal prefix As String) As String
    mSeq = mSeq + 1
    NextName = prefix & mSeq
End Function

Private Sub UserForm_Initialize()
    Set mHandlers = New Collection
    Set mFields = NewDict()
    Set mOrder = New Collection
    Set mActions = New Collection
    Set mValidate = NewDict()
    Me.BackColor = CLR_BG()
    Me.Caption = APP_NAME
End Sub

Public Sub Setup(ByVal title As String, Optional ByVal subtitle As String = "", Optional ByVal accent As Long = -1, _
                 Optional ByVal width As Single = 520, Optional ByVal labelWidth As Single = 170)
    If accent = -1 Then accent = CLR_ORANGE()
    mAccent = accent
    mW = width
    mLabelW = labelWidth
    Me.Caption = APP_NAME & " - " & title
    Kit_Header Me, title, subtitle, accent, mW
    mY = 68
End Sub

Public Sub AddMessage(ByVal text As String, Optional ByVal kind As String = "info")
    Dim glyph As String, col As Long, lines As Long, h As Single
    Select Case kind
        Case "error": glyph = "!": col = CLR_RED()
        Case "warning": glyph = "!": col = CLR_YELLOW()
        Case "success": glyph = ChrW(10003): col = CLR_GREEN()
        Case "question": glyph = "?": col = CLR_BLUE()
        Case Else: glyph = "i": col = CLR_BLUE()
    End Select
    lines = Len(text) \ 70 + UBound(Split(text, vbLf)) + 1
    h = 16 * lines + 8
    If h < 44 Then h = 44
    Kit_Panel Me, NextName("msg_bg"), PT_PAD, mY, mW - 2 * PT_PAD, h + 12, CLR_WHITE(), CLR_LINE()
    Kit_Label Me, NextName("msg_ico"), glyph, PT_PAD + 10, mY + 8, 34, 34, 20, True, CLR_WHITE(), col, 2
    Kit_Label Me, NextName("msg_txt"), text, PT_PAD + 56, mY + 8, mW - 2 * PT_PAD - 66, h, 10, False, CLR_TEXT()
    mY = mY + h + 22
End Sub

Public Sub AddSection(ByVal title As String)
    Kit_Label Me, NextName("sec_"), UCase$(title), PT_PAD, mY, mW - 2 * PT_PAD, 16, 9, True, mAccent
    Kit_Panel Me, NextName("secl_"), PT_PAD, mY + 16, mW - 2 * PT_PAD, 1, CLR_LINE()
    mY = mY + 24
End Sub

' ftype : TEXT MEMO DATE TIME NUM INT MONEY LIST COMBO PASSWORD CHECK INFO GRID
' Pour GRID : dflt = en-tetes "A|B|C", choices = tableau 2D (0 ou 1-based), help = largeurs "60;80;..."
Public Sub AddField(ByVal key As String, ByVal label As String, ByVal ftype As String, _
                    Optional ByVal dflt As Variant = "", Optional ByVal choices As Variant, _
                    Optional ByVal required As Boolean = False, Optional ByVal help As String = "", _
                    Optional ByVal gridHeight As Single = 180)
    Dim d As Object, c As Object, x As Single, w As Single, h As Single, lblTxt As String
    Set d = NewDict()
    d("type") = UCase$(ftype)
    d("label") = label
    d("required") = required
    x = PT_PAD + mLabelW
    w = mW - x - PT_PAD
    h = 20
    lblTxt = label
    If required Then lblTxt = lblTxt & " *"
    Select Case d("type")
        Case "GRID"
            Dim hdr() As String, i As Long, colX As Single, widths() As String, nCols As Long
            hdr = Split(CStr(dflt), "|")
            widths = Split(help, ";")
            nCols = UBound(hdr) + 1
            If Len(label) > 0 Then
                Kit_Label Me, "lbl_" & key, label, PT_PAD, mY, mW - 2 * PT_PAD, 16, 9, True, CLR_TEXT()
                mY = mY + 18
            End If
            colX = PT_PAD + 3
            For i = 0 To nCols - 1
                Kit_Label Me, "gh_" & key & i, hdr(i), colX, mY + 3, Val(widths(i)) - 2, 14, 8, True, CLR_WHITE(), -1
                colX = colX + Val(widths(i))
            Next i
            Kit_Panel(Me, "ghb_" & key, PT_PAD, mY, mW - 2 * PT_PAD, 20, CLR_NAVY2()).ZOrder 1
            Set c = Kit_ListBox(Me, mHandlers, key, PT_PAD, mY + 20, mW - 2 * PT_PAD, gridHeight, nCols, Replace(help, ";", " pt;") & " pt")
            If Not IsMissing(choices) Then
                If ArrayRows(choices) > 0 Then c.List = choices
            End If
            Set d("ctl") = c
            mY = mY + gridHeight + 30
        Case "INFO"
            Kit_Label Me, "lbl_" & key, label, PT_PAD, mY + 2, mLabelW - 6, 16, 9, False, CLR_MUTED()
            Set c = Kit_Label(Me, "val_" & key, ToStr(dflt), x, mY + 2, w, 16, 10, True, CLR_TEXT())
            Set d("ctl") = c
            mY = mY + 22
        Case "CHECK"
            Set c = Kit_Check(Me, mHandlers, key, label, x, mY, w, 20, CBool(Nz(dflt, False)))
            Set d("ctl") = c
            mY = mY + 26
        Case Else
            Kit_Label Me, "lbl_" & key, lblTxt, PT_PAD, mY + 3, mLabelW - 6, 16, 9, required, CLR_TEXT()
            Select Case d("type")
                Case "LIST", "COMBO"
                    Set c = Kit_Combo(Me, mHandlers, key, x, mY, w, h, choices, ToStr(dflt), (d("type") = "LIST"))
                Case "MEMO"
                    h = 54
                    Set c = Kit_TextBox(Me, mHandlers, key, x, mY, w, h, ToStr(dflt), True)
                Case "PASSWORD"
                    Set c = Kit_TextBox(Me, mHandlers, key, x, mY, w, h, ToStr(dflt), False, False, ChrW(9679))
                Case "DATE"
                    If VarType(dflt) = vbDate Then dflt = Format$(dflt, "dd/mm/yyyy")
                    Set c = Kit_TextBox(Me, mHandlers, key, x, mY, 110, h, ToStr(dflt))
                    Kit_Label Me, "hint_" & key, "jj/mm/aaaa", x + 118, mY + 3, 100, 14, 8, False, CLR_MUTED()
                Case "TIME"
                    If VarType(dflt) = vbDouble Or VarType(dflt) = vbDate Then dflt = FmtTime(dflt)
                    Set c = Kit_TextBox(Me, mHandlers, key, x, mY, 80, h, ToStr(dflt))
                    Kit_Label Me, "hint_" & key, "hh:mm", x + 88, mY + 3, 80, 14, 8, False, CLR_MUTED()
                Case Else
                    Set c = Kit_TextBox(Me, mHandlers, key, x, mY, w, h, ToStr(dflt))
            End Select
            Set d("ctl") = c
            mY = mY + h + 8
            If Len(help) > 0 Then
                Kit_Label Me, "help_" & key, help, x, mY - 6, w, 14, 8, False, CLR_MUTED()
                mY = mY + 12
            End If
    End Select
    mFields.Add key, d
    mOrder.Add key
End Sub

' Tuile de menu (hub) : grand bouton colore
Public Sub AddTile(ByVal key As String, ByVal caption As String, ByVal subtitle As String, ByVal color As Long)
    Dim col As Long, row As Long, tw As Single, th As Single, l As Single, t As Single
    tw = (mW - 3 * PT_PAD) / 2
    th = 52
    col = mTiles Mod 2
    row = mTiles \ 2
    l = PT_PAD + col * (tw + PT_PAD)
    t = mY + row * (th + 10)
    Kit_Button Me, mHandlers, "act_" & key, caption & vbLf & subtitle, l, t, tw, th, color, CLR_WHITE(), 10
    mActions.Add key
    mValidate(key) = False
    mTiles = mTiles + 1
End Sub

Public Sub EndTiles()
    mY = mY + ((mTiles + 1) \ 2) * 62 + 4
    mTiles = 0
End Sub

Public Sub AddAction(ByVal key As String, ByVal caption As String, Optional ByVal color As Long = -1, _
                     Optional ByVal isDefault As Boolean = False, Optional ByVal isCancel As Boolean = False, _
                     Optional ByVal validates As Boolean = True)
    If color = -1 Then color = CLR_BLUE()
    mActions.Add key
    mValidate(key) = validates And Not isCancel
    If isDefault Then mDefaultAction = key
    If isCancel Then mCancelAction = key
    mFields("__act_" & key) = caption & "|" & color
End Sub

Public Function ShowDialog() As String
    Dim k As Variant, n As Long, i As Long, bw As Single, x As Single, p() As String, btnKeys As New Collection
    ' message d'erreur de validation
    Set mMsg = Kit_Label(Me, "lbl_err", "", PT_PAD, mY, mW - 2 * PT_PAD, 28, 9, True, CLR_RED())
    mY = mY + 30
    ' barre d'actions
    Kit_Panel Me, "act_bar", 0, mY, mW, 1, CLR_LINE()
    For Each k In mFields.Keys
        If Left$(k, 6) = "__act_" Then btnKeys.Add Mid$(k, 7)
    Next k
    n = btnKeys.Count
    bw = 120
    x = mW - PT_PAD - n * (bw + 8) + 8
    For i = 1 To n
        p = Split(mFields("__act_" & btnKeys(i)), "|")
        Kit_Button Me, mHandlers, "act_" & btnKeys(i), p(0), x, mY + 12, bw, 30, CLng(p(1))
        x = x + bw + 8
    Next i
    If n > 0 Then mY = mY + 54 Else mY = mY + 12
    Kit_SizeForm Me, mW, mY
    mResult = ""
    FocusFirst
    Me.Show vbModal
    ShowDialog = mResult
End Function

Private Sub FocusFirst()
    Dim k As Variant, d As Object
    On Error Resume Next
    For Each k In mOrder
        Set d = mFields(k)
        If InStr("|INFO|GRID|", "|" & d("type") & "|") = 0 Then
            d("ctl").SetFocus
            Exit Sub
        End If
    Next k
End Sub

Public Sub OnUIEvent(ByVal key As String, ByVal evt As String)
    If Left$(key, 4) = "act_" And evt = "Click" Then
        DoAction Mid$(key, 5)
    ElseIf evt = "Enter" And Len(mDefaultAction) > 0 Then
        If mFields.Exists(key) Then
            If mFields(key)("type") <> "MEMO" Then DoAction mDefaultAction
        End If
    ElseIf evt = "DblClick" And Len(mDefaultAction) > 0 Then
        DoAction mDefaultAction
    End If
End Sub

Private Sub DoAction(ByVal act As String)
    Dim msg As String
    If mValidate.Exists(act) Then
        If mValidate(act) Then
            msg = ValidateAll()
            If Len(msg) > 0 Then
                mMsg.Caption = msg
                Exit Sub
            End If
        End If
    End If
    mResult = act
    Me.Hide
End Sub

Private Function ValidateAll() As String
    Dim k As Variant, d As Object, txt As String
    For Each k In mOrder
        Set d = mFields(k)
        Select Case d("type")
            Case "INFO", "GRID", "CHECK"
            Case Else
                txt = Trim$(ToStr(d("ctl").Text))
                If d("required") And Len(txt) = 0 Then
                    ValidateAll = "Le champ « " & d("label") & " » est obligatoire."
                    d("ctl").SetFocus
                    Exit Function
                End If
                If Len(txt) > 0 Then
                    Select Case d("type")
                        Case "DATE"
                            If ToDate(txt) = 0 Then ValidateAll = "« " & d("label") & " » : date invalide (jj/mm/aaaa).": Exit Function
                        Case "TIME"
                            If ToTime(txt) < 0 Then ValidateAll = "« " & d("label") & " » : heure invalide (hh:mm).": Exit Function
                        Case "NUM", "INT", "MONEY"
                            If ToDbl(txt, -987654321#) = -987654321# Then ValidateAll = "« " & d("label") & " » : nombre attendu.": Exit Function
                    End Select
                End If
        End Select
    Next k
End Function

' Valeur convertie d'un champ
Public Function FieldValue(ByVal key As String) As Variant
    Dim d As Object, txt As String
    If Not mFields.Exists(key) Then FieldValue = Empty: Exit Function
    Set d = mFields(key)
    Select Case d("type")
        Case "CHECK": FieldValue = CBool(d("ctl").Value)
        Case "INFO": FieldValue = d("ctl").Caption
        Case "GRID": FieldValue = d("ctl").ListIndex
        Case Else
            txt = Trim$(ToStr(d("ctl").Text))
            Select Case d("type")
                Case "DATE": If Len(txt) = 0 Then FieldValue = Empty Else FieldValue = ToDate(txt)
                Case "TIME": If Len(txt) = 0 Then FieldValue = Empty Else FieldValue = ToTime(txt)
                Case "NUM", "MONEY": If Len(txt) = 0 Then FieldValue = Empty Else FieldValue = ToDbl(txt)
                Case "INT": If Len(txt) = 0 Then FieldValue = Empty Else FieldValue = ToLng(txt)
                Case Else: FieldValue = txt
            End Select
    End Select
End Function

Public Function GridRow(ByVal key As String) As Long
    GridRow = -1
    If mFields.Exists(key) Then GridRow = mFields(key)("ctl").ListIndex
End Function

Public Function GetControl(ByVal key As String) As Object
    If mFields.Exists(key) Then Set GetControl = mFields(key)("ctl")
End Function

Private Sub UserForm_QueryClose(Cancel As Integer, CloseMode As Integer)
    If CloseMode = 0 Then        ' vbFormControlMenu (croix)
        mResult = mCancelAction
        Cancel = True
        Me.Hide
    End If
End Sub

Private Sub UserForm_Terminate()
    Kit_ReleaseAll mHandlers
    Set mHandlers = Nothing
End Sub

Public Sub Dispose()
    Kit_ReleaseAll mHandlers
    Set mHandlers = Nothing
    Unload Me
End Sub
