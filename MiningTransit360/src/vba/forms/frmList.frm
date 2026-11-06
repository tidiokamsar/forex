VERSION 5.00
Begin {C62A69F0-16DC-11CE-9E98-00AA00574A4F} frmList
   Caption         =   "MINING TRANSIT 360"
   ClientHeight    =   12000
   ClientWidth     =   20400
   StartUpPosition =   1  'CenterOwner
End
Attribute VB_Name = "frmList"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - frmList
' Fenetre de module : liste filtrable / triable des enregistrements d'une table,
' barre d'actions issue du registre des modules (filtree par les droits),
' totaux, double-clic = modification (ou consultation).
'==============================================================================
Option Explicit

Private Const FW As Single = 1010
Private Const FH As Single = 610
Private Const MAX_ROWS As Long = 3000

Private mHandlers As Collection
Private mMod As String
Private mTable As String
Private mModInfo As Object
Private mFields As Collection        ' cFieldDef affiches (ID en premier)
Private mDateCol As String
Private mHasStatus As Boolean
Private mHasActif As Boolean
Private mAccent As Long
Private mList As Object
Private mIds() As Long
Private mSortCol As Long
Private mSortDesc As Boolean
Private mLblCount As Object
Private mLblTotals As Object
Private mDefaultDbl As String

Private Sub UserForm_Initialize()
    Set mHandlers = New Collection
    Me.BackColor = CLR_BG()
End Sub

Public Sub Init(ByVal modKey As String)
    Dim f As cFieldDef, x As Single, y As Single, colW As String, n As Long, a As Variant, acts As Collection, w As Single, cap As String
    mMod = modKey
    Set mModInfo = Mod_Get(modKey)
    mTable = mModInfo("TableName")
    mAccent = HexToRGB(mModInfo("Couleur"))
    mDateCol = mModInfo("DateCol")
    Me.Caption = APP_NAME & " - " & mModInfo("Titre")
    Kit_SizeForm Me, FW, FH
    Kit_Header Me, mModInfo("Titre"), Schema_TableLabel(mTable), mAccent, FW
    ' colonnes affichees
    Set mFields = New Collection
    For Each f In Schema_Fields(mTable)
        If f.Name = "ID" Then mFields.Add f
    Next f
    For Each f In Schema_Fields(mTable)
        If f.InList And f.Name <> "ID" Then mFields.Add f
        If f.Name = "Statut" Then mHasStatus = True
        If f.Name = "Actif" Then mHasActif = True
    Next f
    ' --- barre de filtres ---
    y = 62
    Kit_Label Me, "lbl_s", "Rechercher", PT_PAD, y + 3, 60, 14, 8, True, CLR_MUTED()
    Kit_TextBox Me, mHandlers, "f_search", PT_PAD + 62, y, 200, 20
    x = PT_PAD + 272
    If Len(mDateCol) > 0 Then
        Kit_Label Me, "lbl_d1", "Du", x, y + 3, 18, 14, 8, True, CLR_MUTED()
        Kit_TextBox Me, mHandlers, "f_d1", x + 20, y, 74, 20, Format$(App_Today() - 30, "dd/mm/yyyy")
        Kit_Label Me, "lbl_d2", "au", x + 100, y + 3, 16, 14, 8, True, CLR_MUTED()
        Kit_TextBox Me, mHandlers, "f_d2", x + 118, y, 74, 20, Format$(App_Today(), "dd/mm/yyyy")
        x = x + 200
    End If
    If mHasStatus Then
        Kit_Label Me, "lbl_st", "Statut", x, y + 3, 34, 14, 8, True, CLR_MUTED()
        Kit_Combo Me, mHandlers, "f_status", x + 36, y, 120, 20, Array_Prepend(Split(Schema_Field(mTable, "Statut").Vals, "|"), "(Tous)"), "(Tous)"
        x = x + 166
    End If
    If mHasActif Then
        Kit_Check Me, mHandlers, "f_actif", "Actifs seulement", x, y + 1, 100, 18, True
        x = x + 106
    End If
    Kit_Button Me, mHandlers, "f_apply", "Filtrer", x, y - 1, 64, 22, mAccent
    Kit_Button Me, mHandlers, "f_clear", "Effacer", x + 70, y - 1, 64, 22, CLR_GREY()
    ' --- barre d'actions (2 lignes max) ---
    Set acts = New Collection
    For Each a In Split(mModInfo("Actions"), "|")
        If Len(a) > 0 Then
            If Sec_Can(mMod, Act_Right(CStr(a))) Then acts.Add CStr(a)
        End If
    Next a
    x = PT_PAD: y = 92
    For Each a In acts
        cap = Act_Caption(CStr(a))
        w = Len(cap) * 6 + 22
        If w < 70 Then w = 70
        If x + w > FW - PT_PAD Then x = PT_PAD: y = y + 28
        Kit_Button Me, mHandlers, "act_" & a, cap, x, y, w, 24, Act_Color(CStr(a), mAccent), CLR_WHITE(), 8.5
        x = x + w + 5
        If a = "EDIT" Then mDefaultDbl = "EDIT"
        If a = "VIEW" And Len(mDefaultDbl) = 0 Then mDefaultDbl = "VIEW"
    Next a
    y = y + 34
    ' --- en-tetes de colonnes ---
    Kit_Panel Me, "grid_hdr", PT_PAD, y, FW - 2 * PT_PAD, 22, CLR_NAVY2()
    x = PT_PAD + 4
    n = 0
    For Each f In mFields
        w = ColWidth(f)
        Kit_Link Me, mHandlers, "col_" & n, f.Label, x, y + 4, w - 2, 16, 8, True, CLR_WHITE(), -1, 1
        colW = colW & IIf(Len(colW) > 0, ";", "") & w & " pt"
        x = x + w
        n = n + 1
    Next f
    Set mList = Kit_ListBox(Me, mHandlers, "grid", PT_PAD, y + 22, FW - 2 * PT_PAD, FH - y - 22 - 40, mFields.Count, colW)
    ' --- pied ---
    Set mLblCount = Kit_Label(Me, "lbl_count", "", PT_PAD, FH - 32, 260, 16, 9, True, CLR_TEXT())
    Set mLblTotals = Kit_Label(Me, "lbl_totals", "", PT_PAD + 270, FH - 32, FW - 400, 16, 9, False, CLR_TEXT())
    Kit_Button Me, mHandlers, "close", "Fermer", FW - PT_PAD - 100, FH - 36, 100, 26, CLR_NAVY()
    ' tri par defaut
    mSortCol = -1
    If Len(mModInfo("TriDefaut")) > 0 Then
        Dim sc As String, i As Long
        sc = mModInfo("TriDefaut")
        mSortDesc = (Left$(sc, 1) = "-")
        sc = Replace(sc, "-", "")
        i = 0
        For Each f In mFields
            If f.Name = sc Then mSortCol = i
            i = i + 1
        Next f
    End If
    LoadData
End Sub

Private Function ColWidth(ByVal f As cFieldDef) As Single
    If f.ListWidth > 0 Then ColWidth = f.ListWidth * 1.15 Else ColWidth = 70
    If f.Name = "ID" Then ColWidth = 34
End Function

Public Sub LoadData()
    Dim data As Variant, n As Long, r As Long, c As Long, f As cFieldDef, cols() As Long, nC As Long
    Dim d1 As Date, d2 As Date, cDate_ As Long, st As String, cSt As Long, cAct As Long, q As String
    Dim keep() As Long, nk As Long, disp() As Variant, raw() As Variant, rowTxt As String, i As Long, j As Long
    Dim fkMaps As Object, sums() As Double, idx() As Variant, out() As Variant
    On Error GoTo EH
    Status "Chargement de " & mModInfo("Titre") & "..."
    data = DB_Data(mTable)
    n = ArrayRows(data)
    nC = mFields.Count
    ReDim cols(0 To nC - 1)
    Set fkMaps = NewDict()
    i = 0
    For Each f In mFields
        cols(i) = DB_ColIdx(mTable, f.Name)
        If f.FType = "FK" And Not fkMaps.Exists(f.Name) Then fkMaps.Add f.Name, FKMap(f)
        i = i + 1
    Next f
    ' filtres
    If Len(mDateCol) > 0 Then
        d1 = ToDate(GetText("f_d1")): d2 = ToDate(GetText("f_d2"))
        cDate_ = DB_ColIdx(mTable, mDateCol)
    End If
    If mHasStatus Then
        st = GetText("f_status")
        If st = "(Tous)" Then st = ""
        cSt = DB_ColIdx(mTable, "Statut")
    End If
    If mHasActif Then
        If Me.Controls("chk_f_actif").Value Then cAct = DB_ColIdx(mTable, "Actif")
    End If
    q = LCase$(Trim$(GetText("f_search")))
    ReDim keep(1 To IIf(n = 0, 1, n))
    For r = 1 To n
        If cDate_ > 0 Then
            If d1 > 0 And ToDate(data(r, cDate_)) < d1 And Not IsBlank(data(r, cDate_)) Then GoTo NextRow
            If d2 > 0 And ToDate(data(r, cDate_)) > d2 Then GoTo NextRow
        End If
        If Len(st) > 0 Then
            If ToStr(data(r, cSt)) <> st Then GoTo NextRow
        End If
        If cAct > 0 Then
            If ToStr(data(r, cAct)) = "Non" Then GoTo NextRow
        End If
        nk = nk + 1
        keep(nk) = r
NextRow:
    Next r
    ' affichage + recherche
    ReDim disp(1 To IIf(nk = 0, 1, nk), 0 To nC - 1)
    ReDim raw(1 To IIf(nk = 0, 1, nk), 1 To 2)
    ReDim sums(0 To nC - 1)
    j = 0
    For i = 1 To nk
        r = keep(i)
        rowTxt = ""
        c = 0
        For Each f In mFields
            If f.FType = "FK" Then
                disp(j + 1, c) = ToStr(fkMaps(f.Name)(ToStr(data(r, cols(c)))))
            Else
                disp(j + 1, c) = f.Display(data(r, cols(c)))
            End If
            If Len(q) > 0 Then rowTxt = rowTxt & "|" & LCase$(disp(j + 1, c))
            c = c + 1
        Next f
        If Len(q) = 0 Or InStr(1, rowTxt, q, vbTextCompare) > 0 Then
            j = j + 1
            c = 0
            For Each f In mFields
                If f.FType = "MNY" Or (f.FType = "NUM" And f.Name <> "CapaciteT") Then sums(c) = sums(c) + ToDbl(data(r, cols(c)))
                c = c + 1
            Next f
            If mSortCol >= 0 Then raw(j, 1) = data(r, cols(mSortCol)) Else raw(j, 1) = j
            If mSortCol >= 0 Then
                If mFields(mSortCol + 1).FType = "FK" Then raw(j, 1) = disp(j, mSortCol)
            End If
            raw(j, 2) = j
        End If
    Next i
    ' tri
    If j > 1 And mSortCol >= 0 Then
        ReDim idx(1 To j, 1 To 2)
        For i = 1 To j
            idx(i, 1) = raw(i, 1): idx(i, 2) = raw(i, 2)
        Next i
        SortArray2D idx, 1, mSortDesc
    End If
    ' rendu
    Dim shown As Long
    shown = j
    If shown > MAX_ROWS Then shown = MAX_ROWS
    mList.Clear
    If shown > 0 Then
        ReDim out(0 To shown - 1, 0 To nC - 1)
        ReDim mIds(0 To shown - 1)
        For i = 1 To shown
            If j > 1 And mSortCol >= 0 Then r = idx(i, 2) Else r = i
            For c = 0 To nC - 1
                out(i - 1, c) = disp(r, c)
            Next c
            mIds(i - 1) = ToLng(Replace(disp(r, 0), " ", ""))
        Next i
        mList.List = out
    Else
        ReDim mIds(0 To 0)
    End If
    ' pied
    mLblCount.Caption = j & " enregistrement(s)" & IIf(j > shown, " - " & shown & " affichés (affinez les filtres)", "")
    Dim tot As String
    c = 0
    For Each f In mFields
        If f.FType = "MNY" And sums(c) <> 0 Then tot = tot & f.Label & " : " & FmtMoney(sums(c)) & "    "
        If f.FType = "NUM" And sums(c) <> 0 And InStr(f.Name, "Poids") + InStr(f.Name, "Litres") + InStr(f.Name, "Heures") + InStr(f.Name, "Tonnage") > 0 Then _
            tot = tot & f.Label & " : " & FmtNum(sums(c), 2) & "    "
        c = c + 1
    Next f
    mLblTotals.Caption = tot
    Me.Controls("hdr_sub").Caption = Schema_TableLabel(mTable) & "  -  " & j & " enregistrement(s)"
    Status "Prêt"
    Exit Sub
EH:
    Log_Error "frmList.LoadData", Err.Number, Err.Description
End Sub

Private Function FKMap(ByVal f As cFieldDef) As Object
    Dim d As Object, data As Variant, r As Long, cId As Long, cV As Long
    Set d = NewDict()
    data = DB_Data(f.FKTable)
    If Not IsEmpty(data) Then
        cId = DB_ColIdx(f.FKTable, "ID"): cV = DB_ColIdx(f.FKTable, f.FKCol)
        For r = 1 To UBound(data, 1)
            d(ToStr(data(r, cId))) = ToStr(data(r, cV))
        Next r
    End If
    Set FKMap = d
End Function

Private Function GetText(ByVal key As String) As String
    On Error Resume Next
    GetText = ToStr(Me.Controls("txt_" & key).Text)
    If Err.Number <> 0 Then
        Err.Clear
        GetText = ToStr(Me.Controls("cbo_" & key).Text)
    End If
End Function

Public Function SelectedId() As Long
    If mList.ListIndex < 0 Then Exit Function
    SelectedId = mIds(mList.ListIndex)
End Function

' Donnees affichees (pour impression / export de la liste)
Public Function VisibleData() As Variant
    Dim out() As Variant, i As Long, c As Long
    If mList.ListCount = 0 Then VisibleData = Empty: Exit Function
    ReDim out(1 To mList.ListCount, 1 To mList.ColumnCount)
    For i = 0 To mList.ListCount - 1
        For c = 0 To mList.ColumnCount - 1
            out(i + 1, c + 1) = mList.List(i, c)
        Next c
    Next i
    VisibleData = out
End Function

Public Function VisibleFields() As Collection
    Set VisibleFields = mFields
End Function

Public Function FilterText() As String
    Dim s As String
    If Len(mDateCol) > 0 Then s = "Période du " & GetText("f_d1") & " au " & GetText("f_d2")
    If mHasStatus Then
        If GetText("f_status") <> "(Tous)" Then s = s & IIf(Len(s) > 0, " | ", "") & "Statut : " & GetText("f_status")
    End If
    If Len(GetText("f_search")) > 0 Then s = s & IIf(Len(s) > 0, " | ", "") & "Recherche : " & GetText("f_search")
    FilterText = s
End Function

Public Sub OnUIEvent(ByVal key As String, ByVal evt As String)
    Dim id As Long
    On Error GoTo EH
    Select Case True
        Case key = "close" And evt = "Click": Me.Hide
        Case key = "f_apply" And evt = "Click": LoadData
        Case key = "f_search" And evt = "Enter": LoadData
        Case (key = "f_d1" Or key = "f_d2") And evt = "Enter": LoadData
        Case key = "f_status" And evt = "Change": LoadData
        Case key = "f_actif" And evt = "Change": LoadData
        Case key = "f_clear" And evt = "Click"
            Me.Controls("txt_f_search").Text = ""
            If Len(mDateCol) > 0 Then
                Me.Controls("txt_f_d1").Text = ""
                Me.Controls("txt_f_d2").Text = ""
            End If
            If mHasStatus Then Me.Controls("cbo_f_status").Value = "(Tous)"
            LoadData
        Case Left$(key, 4) = "col_" And evt = "Click"
            Dim c As Long
            c = CLng(Mid$(key, 5))
            If c = mSortCol Then mSortDesc = Not mSortDesc Else mSortCol = c: mSortDesc = False
            LoadData
        Case key = "grid" And evt = "DblClick"
            If Len(mDefaultDbl) > 0 Then RunAction mDefaultDbl
        Case Left$(key, 4) = "act_" And evt = "Click"
            RunAction Mid$(key, 5)
    End Select
    Exit Sub
EH:
    Log_Error "frmList", Err.Number, Err.Description
End Sub

Private Sub RunAction(ByVal action As String)
    Dim keepIdx As Long
    On Error GoTo EH
    keepIdx = mList.ListIndex
    If Act_Run(mMod, action, SelectedId(), Me) Then
        LoadData
        If keepIdx >= 0 And keepIdx < mList.ListCount Then mList.ListIndex = keepIdx
    End If
    Exit Sub
EH:
    Log_Error "Action " & Act_Caption(action), Err.Number, Err.Description
    LoadData
End Sub

Public Property Get ModuleKey() As String
    ModuleKey = mMod
End Property

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
