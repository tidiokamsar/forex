VERSION 5.00
Begin {C62A69F0-16DC-11CE-9E98-00AA00574A4F} frmRecord
   Caption         =   "MINING TRANSIT 360"
   ClientHeight    =   9000
   ClientWidth     =   14400
   StartUpPosition =   1  'CenterOwner
End
Attribute VB_Name = "frmRecord"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - frmRecord
' Fiche generique de creation / modification / consultation d'un enregistrement,
' construite a partir du schema (SYS_SCHEMA). Aucune regle metier ici :
' l'enregistrement passe par mBiz_Records.Rec_Save (controles + calculs + audit).
' Calculs en direct (poids net, montants, heures) via Biz_LivePreview.
'==============================================================================
Option Explicit

Private mHandlers As Collection
Private mMod As String
Private mTable As String
Private mId As Long
Private mMode As String               ' NEW / EDIT / VIEW
Private mCtl As Object                ' champ -> controle
Private mFields As Collection         ' cFieldDef affiches
Private mSavedId As Long
Private mErr As Object
Private mAccent As Long
Private mW As Single
Private mLoading As Boolean

Private Sub UserForm_Initialize()
    Set mHandlers = New Collection
    Set mCtl = NewDict()
    Me.BackColor = CLR_BG()
End Sub

Public Property Get SavedId() As Long
    SavedId = mSavedId
End Property

Public Sub Init(ByVal modKey As String, ByVal id As Long, ByVal mode As String)
    Dim m As Object, f As cFieldDef, rec As Object, title As String, nEdit As Long, twoCols As Boolean
    Dim x As Single, y As Single, col As Long, lblW As Single, ctlW As Single, colW As Single, h As Single, yCol(1) As Single
    Dim v As Variant, c As Object, ro As Boolean, txt As String, maxY As Single
    mLoading = True
    mMod = modKey: mId = id: mMode = mode
    Set m = Mod_Get(modKey)
    mTable = m("TableName")
    mAccent = HexToRGB(m("Couleur"))
    If mode = "NEW" Then
        Set rec = Rec_Defaults(mTable)
        title = "Nouveau - " & Schema_TableLabel(mTable)
    Else
        Set rec = DB_Get(mTable, id)
        If rec Is Nothing Then Fail "Enregistrement introuvable."
        title = IIf(mode = "EDIT", "Modifier - ", "Consulter - ") & Schema_TableLabel(mTable)
    End If
    ' champs affiches
    Set mFields = New Collection
    For Each f In Schema_Fields(mTable)
        If Not f.Hidden Then
            If Not (mode = "NEW" And (f.FType = "ID" Or (f.ReadOnly And Len(f.SeqPrefix) > 0))) Then mFields.Add f
            If Not f.ReadOnly Then nEdit = nEdit + 1
        End If
    Next f
    twoCols = (mFields.Count > 9)
    lblW = 140: ctlW = 190
    colW = lblW + ctlW + 16
    If twoCols Then mW = 2 * colW + 2 * PT_PAD + 8 Else mW = colW + 2 * PT_PAD + 20
    Kit_Header Me, title, Rec_Subtitle(rec), mAccent, mW
    yCol(0) = 66: yCol(1) = 66
    col = 0
    For Each f In mFields
        If f.FType = "MEMO" And twoCols Then
            ' memo : pleine largeur en fin de formulaire, traite apres
        Else
            If twoCols Then
                col = IIf(yCol(0) <= yCol(1), 0, 1)
            End If
            x = PT_PAD + col * (colW + 8)
            y = yCol(col)
            h = AddFieldCtl(f, rec, x, y, lblW, ctlW)
            yCol(col) = y + h + 6
        End If
    Next f
    maxY = IIf(yCol(0) > yCol(1), yCol(0), yCol(1))
    If twoCols Then
        For Each f In mFields
            If f.FType = "MEMO" Then
                h = AddFieldCtl(f, rec, PT_PAD, maxY, lblW, mW - 2 * PT_PAD - lblW - 8)
                maxY = maxY + h + 6
            End If
        Next f
    End If
    ' message d'erreur + boutons
    Set mErr = Kit_Label(Me, "lbl_err", "", PT_PAD, maxY + 2, mW - 2 * PT_PAD, 30, 9, True, CLR_RED())
    maxY = maxY + 36
    Kit_Panel Me, "bar", 0, maxY, mW, 1, CLR_LINE()
    If mode = "VIEW" Then
        Kit_Button Me, mHandlers, "close", "Fermer", mW - PT_PAD - 110, maxY + 12, 110, 30, CLR_NAVY()
        If Sec_Can(mMod, "M") And Mod_HasAction(mMod, "EDIT") Then Kit_Button Me, mHandlers, "toedit", "Modifier", mW - PT_PAD - 228, maxY + 12, 110, 30, mAccent
    Else
        Kit_Button Me, mHandlers, "close", "Annuler", mW - PT_PAD - 236, maxY + 12, 110, 30, CLR_GREY()
        Kit_Button Me, mHandlers, "save", "Enregistrer", mW - PT_PAD - 118, maxY + 12, 118, 30, CLR_GREEN()
    End If
    Kit_Label Me, "lbl_req", IIf(mode = "VIEW", "", "* champ obligatoire"), PT_PAD, maxY + 20, 200, 14, 8, False, CLR_MUTED()
    maxY = maxY + 54
    If maxY > 600 Then
        Kit_SizeForm Me, mW + 14, 600
        Me.ScrollBars = 2
        Me.ScrollHeight = maxY
    Else
        Kit_SizeForm Me, mW, maxY
    End If
    Me.Caption = APP_NAME & " - " & title
    mLoading = False
    LivePreview
End Sub

Private Function Rec_Subtitle(ByVal rec As Object) As String
    Dim f As cFieldDef, s As String, n As Long
    If mMode = "NEW" Then Rec_Subtitle = "Les champs calculés sont mis à jour automatiquement": Exit Function
    For Each f In Schema_Fields(mTable)
        If f.InList And f.FType <> "ID" And n < 3 Then
            s = s & IIf(Len(s) > 0, "  |  ", "") & f.Display(rec(f.Name))
            n = n + 1
        End If
    Next f
    Rec_Subtitle = s
End Function

' Cree le label et le controle d'un champ ; retourne la hauteur utilisee
Private Function AddFieldCtl(ByVal f As cFieldDef, ByVal rec As Object, ByVal x As Single, ByVal y As Single, _
                             ByVal lblW As Single, ByVal ctlW As Single) As Single
    Dim v As Variant, ro As Boolean, c As Object, txt As String, items As Variant, h As Single, lbl As String
    v = Empty
    If rec.Exists(f.Name) Then v = rec(f.Name)
    ro = (mMode = "VIEW") Or f.ReadOnly
    lbl = f.Label
    If f.Required And Not ro Then lbl = lbl & " *"
    Kit_Label Me, "l_" & f.Name, lbl, x, y + 3, lblW, 26, 8.5, (f.Required And Not ro), IIf(ro And mMode <> "VIEW", CLR_MUTED(), CLR_TEXT())
    h = 20
    Select Case f.FType
        Case "LIST", "BOOL", "FK"
            If ro Then
                Set c = Kit_TextBox(Me, mHandlers, "f_" & f.Name, x + lblW, y, ctlW, h, f.Display(v), False, True)
            Else
                items = f.Choices()
                txt = f.Display(v)
                Set c = Kit_Combo(Me, mHandlers, "f_" & f.Name, x + lblW, y, ctlW, h, items, "", True)
                If Len(txt) > 0 Then
                    On Error Resume Next
                    c.Value = txt
                    If Err.Number <> 0 Or c.ListIndex < 0 Then
                        Err.Clear
                        c.AddItem txt
                        c.Value = txt
                    End If
                    On Error GoTo 0
                End If
            End If
        Case "MEMO"
            h = 44
            Set c = Kit_TextBox(Me, mHandlers, "f_" & f.Name, x + lblW, y, ctlW, h, ToStr(v), True, ro)
        Case "FILE"
            Set c = Kit_TextBox(Me, mHandlers, "f_" & f.Name, x + lblW, y, ctlW - 58, h, ToStr(v), False, True)
            If mMode <> "VIEW" And mMode <> "NEW" Then Kit_Button Me, mHandlers, "pick_" & f.Name, "...", x + lblW + ctlW - 54, y, 24, h, mAccent
            If Len(ToStr(v)) > 0 Then Kit_Button Me, mHandlers, "open_" & f.Name, "Ouvrir", x + lblW + ctlW - 28, y, 28, h, CLR_TEAL(), CLR_WHITE(), 7
        Case Else
            If f.FType = "MNY" Or f.FType = "NUM" Or f.FType = "INT" Or f.FType = "PCT" Then
                If ro Then txt = f.Display(v) Else txt = EditText(f, v)
            ElseIf f.FType = "DATE" Then
                txt = FmtDate(v)
            ElseIf f.FType = "TIME" Then
                txt = FmtTime(v)
            Else
                txt = ToStr(v)
            End If
            Set c = Kit_TextBox(Me, mHandlers, "f_" & f.Name, x + lblW, y, IIf(f.FType = "DATE" Or f.FType = "TIME", 96, ctlW), h, txt, False, ro)
            If f.FType = "DATE" And Not ro Then Kit_Label Me, "h_" & f.Name, "jj/mm/aaaa", x + lblW + 102, y + 4, 80, 14, 8, False, CLR_MUTED()
            If f.FType = "TIME" And Not ro Then Kit_Label Me, "h_" & f.Name, "hh:mm", x + lblW + 102, y + 4, 80, 14, 8, False, CLR_MUTED()
            If f.IsNumericType And Not ro Then c.TextAlign = 3
    End Select
    mCtl.Add f.Name, c
    AddFieldCtl = h
End Function

' Valeur numerique editable sans separateur de milliers
Private Function EditText(ByVal f As cFieldDef, ByVal v As Variant) As String
    If IsBlank(v) Then Exit Function
    Select Case f.FType
        Case "MNY", "INT": EditText = CStr(Round(ToDbl(v), 0))
        Case "PCT": EditText = CStr(Round(ToDbl(v) * 100, 2)) & "%"
        Case Else: EditText = CStr(Round(ToDbl(v), 3))
    End Select
End Function

Private Function CtlText(ByVal fieldName As String) As String
    If mCtl.Exists(fieldName) Then CtlText = Trim$(ToStr(mCtl(fieldName).Text))
End Function

' Lecture souple pour les calculs en direct (aucune erreur)
Private Function LooseValues() As Object
    Dim d As Object, f As cFieldDef
    Set d = NewDict()
    On Error Resume Next
    If mMode <> "NEW" Then Set d = DB_Get(mTable, mId)
    For Each f In mFields
        If Not f.ReadOnly Then
            d(f.Name) = Empty
            d(f.Name) = f.Parse(CtlText(f.Name))
            If Err.Number <> 0 Then d(f.Name) = Empty: Err.Clear
        End If
    Next f
    Set LooseValues = d
End Function

Private Sub LivePreview()
    Dim vals As Object, out As Object, k As Variant, f As cFieldDef, fill As Object
    If mLoading Or mMode = "VIEW" Then Exit Sub
    On Error Resume Next
    Set vals = LooseValues()
    Set out = Biz_LivePreview(mTable, vals)
    For Each k In out.Keys
        If mCtl.Exists(k) Then
            Set f = Schema_Field(mTable, CStr(k))
            mCtl(k).Text = f.Display(out(k))
        End If
    Next k
    ' remplissage automatique (chauffeur affecte, client de la destination)
    Set fill = Biz_AutoFill(mTable, vals)
    For Each k In fill.Keys
        If mCtl.Exists(k) Then
            If Len(CtlText(CStr(k))) = 0 Then
                mLoading = True
                mCtl(k).Value = fill(k)
                mLoading = False
            End If
        End If
    Next k
End Sub

Public Sub OnUIEvent(ByVal key As String, ByVal evt As String)
    Dim fname As String, p As Variant
    On Error GoTo EH
    Select Case True
        Case key = "close" And evt = "Click": Me.Hide
        Case key = "save" And evt = "Click": DoSave
        Case key = "toedit" And evt = "Click"
            mMode = "EDIT"
            Me.Hide
            mSavedId = Rec_OpenForm(mMod, mId, "EDIT")
        Case Left$(key, 5) = "pick_" And evt = "Click"
            fname = Mid$(key, 6)
            p = Doc_Attach(mTable, mId, fname)
            If Len(p) > 0 Then mCtl(fname).Text = p
        Case Left$(key, 5) = "open_" And evt = "Click"
            OpenFile CtlText(Mid$(key, 6))
        Case Left$(key, 2) = "f_" And evt = "Change"
            LivePreview
    End Select
    Exit Sub
EH:
    mErr.Caption = Err.Description
    If Not IsBizError(Err.Number) Then Log_Error "frmRecord", Err.Number, Err.Description, True
End Sub

Private Sub DoSave()
    Dim rec As Object, f As cFieldDef, v As Variant, failedField As String
    On Error GoTo EH
    mErr.Caption = ""
    Set rec = NewDict()
    For Each f In mFields
        If Not f.ReadOnly And f.FType <> "FILE" Then
            failedField = f.Name
            v = f.Parse(CtlText(f.Name))
            rec(f.Name) = v
        End If
    Next f
    failedField = ""
    mSavedId = Rec_Save(mTable, rec, mId)
    Me.Hide
    UI_Toast "Enregistrement effectué (" & Schema_TableLabel(mTable) & " #" & mSavedId & ")"
    Exit Sub
EH:
    Dim eNum As Long, eMsg As String
    eNum = Err.Number: eMsg = Err.Description
    mErr.Caption = eMsg
    If DB_InTx() Then DB_Rollback
    If Not IsBizError(eNum) And eNum <> 0 Then Log_Error "frmRecord.Save", eNum, eMsg, True
    If Len(failedField) > 0 Then
        On Error Resume Next
        mCtl(failedField).SetFocus
    End If
End Sub

Private Sub UserForm_QueryClose(Cancel As Integer, CloseMode As Integer)
    If CloseMode = 0 Then
        Cancel = True
        Me.Hide
    End If
End Sub

Private Sub UserForm_Terminate()
    Kit_ReleaseAll mHandlers
    Set mHandlers = Nothing
End Sub
