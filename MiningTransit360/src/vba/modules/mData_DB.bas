Attribute VB_Name = "mData_DB"
'==============================================================================
' MINING TRANSIT 360 - mData_DB  (couche d'acces aux donnees)
'
' Chaque table metier est un ListObject Excel (T_XXX) sur une feuille masquee.
' Toute lecture / ecriture de donnees passe par ce module : c'est le seul
' point a remplacer pour migrer vers une base SQL (voir docs/01_ARCHITECTURE).
'
' Conventions :
'  - colonne "ID" (Long) = cle technique, attribuee par mData_Seq ;
'  - les lectures renvoient des tableaux 2D 1-based mis en cache ;
'  - toute ecriture invalide le cache de la table ;
'  - criteres : tableaux plats (Colonne, Operateur, Valeur, Colonne, ...)
'    operateurs : = <> > >= < <= IN (a|b|c)  NOTIN  LIKE (contient)
'  - transactions : DB_BeginTx "T_A|T_B" / DB_Commit / DB_Rollback
'==============================================================================
Option Explicit

Private mCache As Object      ' table -> tableau de donnees
Private mCols As Object       ' table -> dictionnaire colonne -> index
Private mTx As Object         ' table -> instantane (transaction)
Private mTxDepth As Long

'------------------------------------------------------------------ Acces table
Public Function DB_LO(ByVal tableName As String) As ListObject
    Dim ws As Worksheet, lo As ListObject
    For Each ws In ThisWorkbook.Worksheets
        For Each lo In ws.ListObjects
            If StrComp(lo.Name, tableName, vbTextCompare) = 0 Then
                Set DB_LO = lo
                Exit Function
            End If
        Next lo
    Next ws
    Fail "Table introuvable dans le classeur : " & tableName, ERR_DATA
End Function

Public Function DB_TableExists(ByVal tableName As String) As Boolean
    Dim ws As Worksheet, lo As ListObject
    For Each ws In ThisWorkbook.Worksheets
        For Each lo In ws.ListObjects
            If StrComp(lo.Name, tableName, vbTextCompare) = 0 Then DB_TableExists = True: Exit Function
        Next lo
    Next ws
End Function

Public Sub DB_Invalidate(Optional ByVal tableName As String = "")
    If mCache Is Nothing Then Exit Sub
    If Len(tableName) = 0 Then
        Set mCache = Nothing
        Set mCols = Nothing
    ElseIf mCache.Exists(tableName) Then
        mCache.Remove tableName
    End If
End Sub

Public Function DB_Cols(ByVal tableName As String) As Object
    If mCols Is Nothing Then Set mCols = NewDict()
    If Not mCols.Exists(tableName) Then
        Dim d As Object, lo As ListObject, i As Long
        Set d = NewDict()
        Set lo = DB_LO(tableName)
        For i = 1 To lo.ListColumns.Count
            d(lo.ListColumns(i).Name) = i
        Next i
        mCols.Add tableName, d
    End If
    Set DB_Cols = mCols(tableName)
End Function

Public Function DB_HasCol(ByVal tableName As String, ByVal colName As String) As Boolean
    DB_HasCol = DB_Cols(tableName).Exists(colName)
End Function

Public Function DB_ColIdx(ByVal tableName As String, ByVal colName As String) As Long
    Dim d As Object
    Set d = DB_Cols(tableName)
    If Not d.Exists(colName) Then Fail "Colonne '" & colName & "' absente de " & tableName, ERR_DATA
    DB_ColIdx = d(colName)
End Function

Public Function DB_ColCount(ByVal tableName As String) As Long
    DB_ColCount = DB_Cols(tableName).Count
End Function

' Vrai si le ListObject ne contient que la ligne vide technique
Private Function IsPlaceholder(ByVal lo As ListObject) As Boolean
    If lo.ListRows.Count = 0 Then IsPlaceholder = True: Exit Function
    If lo.ListRows.Count = 1 Then
        IsPlaceholder = (Application.WorksheetFunction.CountA(lo.DataBodyRange) = 0)
    End If
End Function

' Donnees de la table (tableau 2D 1-based) ou Empty si vide. Mis en cache.
Public Function DB_Data(ByVal tableName As String) As Variant
    If mCache Is Nothing Then Set mCache = NewDict()
    If Not mCache.Exists(tableName) Then
        Dim lo As ListObject, v As Variant
        Set lo = DB_LO(tableName)
        If IsPlaceholder(lo) Then
            v = Empty
        ElseIf lo.ListRows.Count = 1 Then
            Dim one() As Variant, j As Long
            ReDim one(1 To 1, 1 To lo.ListColumns.Count)
            For j = 1 To lo.ListColumns.Count
                one(1, j) = lo.DataBodyRange.Cells(1, j).Value
            Next j
            v = one
        Else
            v = lo.DataBodyRange.Value
        End If
        mCache.Add tableName, v
    End If
    DB_Data = mCache(tableName)
End Function

Public Function DB_Count(ByVal tableName As String) As Long
    DB_Count = ArrayRows(DB_Data(tableName))
End Function

'------------------------------------------------------------------ Criteres
Private Function MatchOne(ByVal v As Variant, ByVal op As String, ByVal target As Variant) As Boolean
    Dim c As Long
    Select Case UCase$(op)
        Case "IN"
            MatchOne = InList(ToStr(v), ToStr(target))
        Case "NOTIN"
            MatchOne = Not InList(ToStr(v), ToStr(target))
        Case "LIKE"
            MatchOne = (InStr(1, ToStr(v), ToStr(target), vbTextCompare) > 0)
        Case "ISBLANK"
            MatchOne = IsBlank(v)
        Case "NOTBLANK"
            MatchOne = Not IsBlank(v)
        Case Else
            If IsBlank(v) And IsBlank(target) Then
                c = 0
            ElseIf IsBlank(v) Or IsBlank(target) Then
                If op = "=" Then MatchOne = False: Exit Function
                If op = "<>" Then MatchOne = True: Exit Function
                MatchOne = False: Exit Function
            Else
                c = CompareValues(v, target)
            End If
            Select Case op
                Case "=": MatchOne = (c = 0)
                Case "<>": MatchOne = (c <> 0)
                Case ">": MatchOne = (c > 0)
                Case ">=": MatchOne = (c >= 0)
                Case "<": MatchOne = (c < 0)
                Case "<=": MatchOne = (c <= 0)
                Case Else: Fail "Operateur de critere inconnu : " & op, ERR_DATA
            End Select
    End Select
End Function

' crit : tableau plat (col, op, val, col, op, val ...) - colonnes deja resolues
Private Function RowMatches(ByRef data As Variant, ByVal r As Long, ByRef idx() As Long, ByRef crit As Variant) As Boolean
    Dim k As Long, n As Long
    If IsEmpty(crit) Then RowMatches = True: Exit Function
    n = (UBound(crit) - LBound(crit) + 1) \ 3
    For k = 0 To n - 1
        If Not MatchOne(data(r, idx(k)), crit(LBound(crit) + k * 3 + 1), crit(LBound(crit) + k * 3 + 2)) Then Exit Function
    Next k
    RowMatches = True
End Function

Private Function ResolveCrit(ByVal tableName As String, ByRef crit As Variant) As Long()
    Dim idx() As Long, n As Long, k As Long
    If IsEmpty(crit) Then ReDim idx(0 To 0): ResolveCrit = idx: Exit Function
    If (UBound(crit) - LBound(crit) + 1) Mod 3 <> 0 Then Fail "Criteres mal formes pour " & tableName, ERR_DATA
    n = (UBound(crit) - LBound(crit) + 1) \ 3
    ReDim idx(0 To n - 1)
    For k = 0 To n - 1
        idx(k) = DB_ColIdx(tableName, crit(LBound(crit) + k * 3))
    Next k
    ResolveCrit = idx
End Function

Private Function NormCrit(ByVal crit As Variant) As Variant
    ' ParamArray vide -> Empty
    If IsMissing(crit) Then NormCrit = Empty: Exit Function
    If Not IsArray(crit) Then NormCrit = Empty: Exit Function
    If UBound(crit) < LBound(crit) Then NormCrit = Empty: Exit Function
    ' Cas d'un tableau transmis comme unique argument d'un ParamArray
    If UBound(crit) = LBound(crit) And IsArray(crit(LBound(crit))) Then
        NormCrit = NormCrit(crit(LBound(crit)))
        Exit Function
    End If
    NormCrit = crit
End Function

' Numeros de lignes (1-based dans DB_Data) satisfaisant les criteres
Public Function DB_RowsA(ByVal tableName As String, ByVal crit As Variant) As Collection
    Dim data As Variant, r As Long, idx() As Long, res As New Collection
    crit = NormCrit(crit)
    data = DB_Data(tableName)
    If IsEmpty(data) Then Set DB_RowsA = res: Exit Function
    idx = ResolveCrit(tableName, crit)
    For r = 1 To UBound(data, 1)
        If RowMatches(data, r, idx, crit) Then res.Add r
    Next r
    Set DB_RowsA = res
End Function

Public Function DB_Rows(ByVal tableName As String, ParamArray crit() As Variant) As Collection
    Set DB_Rows = DB_RowsA(tableName, crit)
End Function

Public Function DB_SumA(ByVal tableName As String, ByVal sumCol As String, ByVal crit As Variant) As Double
    Dim data As Variant, r As Long, idx() As Long, c As Long, t As Double
    crit = NormCrit(crit)
    data = DB_Data(tableName)
    If IsEmpty(data) Then Exit Function
    c = DB_ColIdx(tableName, sumCol)
    idx = ResolveCrit(tableName, crit)
    For r = 1 To UBound(data, 1)
        If RowMatches(data, r, idx, crit) Then t = t + ToDbl(data(r, c))
    Next r
    DB_SumA = t
End Function

Public Function DB_Sum(ByVal tableName As String, ByVal sumCol As String, ParamArray crit() As Variant) As Double
    DB_Sum = DB_SumA(tableName, sumCol, crit)
End Function

Public Function DB_CountIfA(ByVal tableName As String, ByVal crit As Variant) As Long
    DB_CountIfA = DB_RowsA(tableName, crit).Count
End Function

Public Function DB_CountIf(ByVal tableName As String, ParamArray crit() As Variant) As Long
    DB_CountIf = DB_RowsA(tableName, crit).Count
End Function

' Valeurs distinctes d'une colonne (dictionnaire valeur -> nombre)
Public Function DB_Distinct(ByVal tableName As String, ByVal col As String, ParamArray crit() As Variant) As Object
    Dim rows As Collection, data As Variant, c As Long, r As Variant, d As Object
    Set d = NewDict()
    Set rows = DB_RowsA(tableName, crit)
    data = DB_Data(tableName)
    c = DB_ColIdx(tableName, col)
    For Each r In rows
        d(ToStr(data(r, c))) = d(ToStr(data(r, c))) + 1
    Next r
    Set DB_Distinct = d
End Function

'------------------------------------------------------------------ Lecture unitaire
Public Function DB_RowOf(ByVal tableName As String, ByVal keyCol As String, ByVal keyVal As Variant) As Long
    Dim data As Variant, r As Long, c As Long
    data = DB_Data(tableName)
    If IsEmpty(data) Then Exit Function
    c = DB_ColIdx(tableName, keyCol)
    For r = 1 To UBound(data, 1)
        If CompareValues(data(r, c), keyVal) = 0 And Not IsBlank(data(r, c)) Then DB_RowOf = r: Exit Function
    Next r
End Function

Public Function DB_RowDict(ByVal tableName As String, ByVal r As Long) As Object
    Dim data As Variant, cols As Object, k As Variant, d As Object
    data = DB_Data(tableName)
    Set cols = DB_Cols(tableName)
    Set d = NewDict()
    For Each k In cols.Keys
        d(k) = data(r, cols(k))
    Next k
    Set DB_RowDict = d
End Function

Public Function DB_GetBy(ByVal tableName As String, ByVal keyCol As String, ByVal keyVal As Variant) As Object
    Dim r As Long
    r = DB_RowOf(tableName, keyCol, keyVal)
    If r = 0 Then Set DB_GetBy = Nothing Else Set DB_GetBy = DB_RowDict(tableName, r)
End Function

Public Function DB_Get(ByVal tableName As String, ByVal id As Variant) As Object
    Set DB_Get = DB_GetBy(tableName, "ID", id)
End Function

Public Function DB_Lookup(ByVal tableName As String, ByVal keyCol As String, ByVal keyVal As Variant, _
                          ByVal retCol As String, Optional ByVal dflt As Variant = "") As Variant
    Dim r As Long
    If IsBlank(keyVal) Then DB_Lookup = dflt: Exit Function
    r = DB_RowOf(tableName, keyCol, keyVal)
    If r = 0 Then
        DB_Lookup = dflt
    Else
        DB_Lookup = DB_Data(tableName)(r, DB_ColIdx(tableName, retCol))
    End If
End Function

Public Function DB_Value(ByVal tableName As String, ByVal id As Variant, ByVal col As String) As Variant
    DB_Value = DB_Lookup(tableName, "ID", id, col, Empty)
End Function

Public Function DB_Exists(ByVal tableName As String, ByVal col As String, ByVal v As Variant, _
                          Optional ByVal excludeId As Variant = Empty) As Boolean
    Dim data As Variant, r As Long, c As Long, cid As Long
    data = DB_Data(tableName)
    If IsEmpty(data) Or IsBlank(v) Then Exit Function
    c = DB_ColIdx(tableName, col)
    If DB_HasCol(tableName, "ID") Then cid = DB_ColIdx(tableName, "ID")
    For r = 1 To UBound(data, 1)
        If CompareValues(data(r, c), v) = 0 Then
            If cid = 0 Or IsBlank(excludeId) Then DB_Exists = True: Exit Function
            If CompareValues(data(r, cid), excludeId) <> 0 Then DB_Exists = True: Exit Function
        End If
    Next r
End Function

Public Function DB_MaxValue(ByVal tableName As String, ByVal col As String) As Double
    Dim data As Variant, r As Long, c As Long, m As Double
    data = DB_Data(tableName)
    If IsEmpty(data) Then Exit Function
    c = DB_ColIdx(tableName, col)
    For r = 1 To UBound(data, 1)
        If ToDbl(data(r, c)) > m Then m = ToDbl(data(r, c))
    Next r
    DB_MaxValue = m
End Function

'------------------------------------------------------------------ Ecriture
Private Function SheetRowOf(ByVal lo As ListObject, ByVal tableName As String, ByVal keyCol As String, ByVal keyVal As Variant) As Long
    SheetRowOf = DB_RowOf(tableName, keyCol, keyVal)
End Function

Private Sub ApplyAudit(ByVal tableName As String, ByVal rec As Object, ByVal isNew As Boolean)
    If isNew Then
        If DB_HasCol(tableName, "CreePar") Then rec("CreePar") = Sec_CurrentLogin()
        If DB_HasCol(tableName, "CreeLe") Then rec("CreeLe") = App_Today()
    Else
        If DB_HasCol(tableName, "ModifPar") Then rec("ModifPar") = Sec_CurrentLogin()
        If DB_HasCol(tableName, "ModifLe") Then rec("ModifLe") = App_Today()
    End If
End Sub

' Insere un enregistrement (dictionnaire champ -> valeur). Retourne l'ID attribue.
Public Function DB_Insert(ByVal tableName As String, ByVal rec As Object, Optional ByVal audit As Boolean = True) As Long
    Dim lo As ListObject, cols As Object, k As Variant, arr() As Variant, lr As ListRow
    Set lo = DB_LO(tableName)
    Set cols = DB_Cols(tableName)
    If cols.Exists("ID") Then
        If IsBlank(Nz(rec("ID"), "")) Or ToLng(rec("ID")) = 0 Then rec("ID") = Seq_NextID(tableName)
        DB_Insert = ToLng(rec("ID"))
    End If
    If audit Then ApplyAudit tableName, rec, True
    ReDim arr(1 To 1, 1 To cols.Count)
    For Each k In rec.Keys
        If cols.Exists(k) Then arr(1, cols(k)) = rec(k)
    Next k
    If IsPlaceholder(lo) And lo.ListRows.Count = 1 Then
        lo.DataBodyRange.Value = arr
    Else
        Set lr = lo.ListRows.Add(AlwaysInsert:=True)
        lr.Range.Value = arr
    End If
    DB_Invalidate tableName
End Function

' Met a jour les champs fournis (mise a jour partielle) de l'enregistrement id
Public Sub DB_Update(ByVal tableName As String, ByVal id As Variant, ByVal rec As Object, _
                     Optional ByVal keyCol As String = "ID", Optional ByVal audit As Boolean = True)
    Dim lo As ListObject, cols As Object, r As Long, k As Variant, rowRange As Range, arr As Variant
    Set lo = DB_LO(tableName)
    Set cols = DB_Cols(tableName)
    r = DB_RowOf(tableName, keyCol, id)
    If r = 0 Then Fail "Enregistrement " & id & " introuvable dans " & tableName, ERR_DATA
    If audit Then ApplyAudit tableName, rec, False
    Set rowRange = lo.ListRows(r).Range
    arr = rowRange.Value
    For Each k In rec.Keys
        If cols.Exists(k) And StrComp(k, keyCol, vbTextCompare) <> 0 Then arr(1, cols(k)) = rec(k)
    Next k
    rowRange.Value = arr
    DB_Invalidate tableName
End Sub

Public Sub DB_SetValue(ByVal tableName As String, ByVal id As Variant, ByVal col As String, ByVal v As Variant, _
                       Optional ByVal keyCol As String = "ID")
    Dim lo As ListObject, r As Long
    Set lo = DB_LO(tableName)
    r = DB_RowOf(tableName, keyCol, id)
    If r = 0 Then Fail "Enregistrement " & id & " introuvable dans " & tableName, ERR_DATA
    lo.ListRows(r).Range.Cells(1, DB_ColIdx(tableName, col)).Value = v
    DB_Invalidate tableName
End Sub

Public Sub DB_Delete(ByVal tableName As String, ByVal id As Variant, Optional ByVal keyCol As String = "ID")
    Dim lo As ListObject, r As Long
    Set lo = DB_LO(tableName)
    r = DB_RowOf(tableName, keyCol, id)
    If r = 0 Then Fail "Enregistrement " & id & " introuvable dans " & tableName, ERR_DATA
    If lo.ListRows.Count = 1 Then
        lo.DataBodyRange.ClearContents
    Else
        lo.ListRows(r).Delete
    End If
    DB_Invalidate tableName
End Sub

' Remplace tout le contenu d'une table (restauration, reinitialisation demo)
Public Sub DB_ReplaceAll(ByVal tableName As String, ByVal data As Variant)
    Dim lo As ListObject, n As Long, m As Long, oldRows As Long, newRows As Long
    Set lo = DB_LO(tableName)
    m = lo.ListColumns.Count
    n = ArrayRows(data)
    oldRows = lo.ListRows.Count
    If Not lo.DataBodyRange Is Nothing Then lo.DataBodyRange.ClearContents
    newRows = n
    If newRows = 0 Then newRows = 1
    lo.Resize lo.HeaderRowRange.Resize(newRows + 1, m)
    If oldRows > newRows Then lo.HeaderRowRange.Offset(newRows + 1, 0).Resize(oldRows - newRows, m).Clear
    If n > 0 Then
        If UBound(data, 2) <> m Then Fail "Structure incompatible pour " & tableName & " (" & UBound(data, 2) & " colonnes au lieu de " & m & ")", ERR_DATA
        lo.DataBodyRange.Value = data
    End If
    DB_Invalidate tableName
End Sub

'------------------------------------------------------------------ Transactions
' Instantane des tables listees ("T_A|T_B") ; DB_Rollback restaure l'etat initial.
Public Sub DB_BeginTx(ByVal tables As String)
    Dim t As Variant
    If mTx Is Nothing Then Set mTx = NewDict()
    mTxDepth = mTxDepth + 1
    For Each t In Split(tables, "|")
        If Len(t) > 0 And Not mTx.Exists(t) Then
            DB_Invalidate CStr(t)
            mTx.Add CStr(t), DB_Data(CStr(t))
        End If
    Next t
End Sub

Public Sub DB_Commit()
    mTxDepth = mTxDepth - 1
    If mTxDepth <= 0 Then
        mTxDepth = 0
        Set mTx = Nothing
    End If
End Sub

Public Sub DB_Rollback()
    Dim t As Variant
    If mTx Is Nothing Then Exit Sub
    For Each t In mTx.Keys
        DB_ReplaceAll CStr(t), mTx(t)
    Next t
    Set mTx = Nothing
    mTxDepth = 0
End Sub

Public Function DB_InTx() As Boolean
    DB_InTx = Not (mTx Is Nothing)
End Function

'------------------------------------------------------------------ Divers
' Vrai si une valeur est referencee par une autre table (controle avant suppression)
Public Function DB_IsReferenced(ByVal tableName As String, ByVal id As Variant, ByRef usedBy As String) As Boolean
    Dim refs As Collection, item As Variant, p() As String
    Set refs = Schema_ReferencesTo(tableName)
    For Each item In refs
        p = Split(item, ".")
        If DB_CountIf(p(0), p(1), "=", id) > 0 Then
            usedBy = Schema_TableLabel(p(0))
            DB_IsReferenced = True
            Exit Function
        End If
    Next item
End Function

Public Function NewRecord(ParamArray kv() As Variant) As Object
    Dim d As Object, i As Long
    Set d = NewDict()
    For i = LBound(kv) To UBound(kv) - 1 Step 2
        d(kv(i)) = kv(i + 1)
    Next i
    Set NewRecord = d
End Function
