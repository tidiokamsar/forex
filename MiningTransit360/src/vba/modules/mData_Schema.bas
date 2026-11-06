Attribute VB_Name = "mData_Schema"
'==============================================================================
' MINING TRANSIT 360 - mData_Schema
' Metadonnees : definitions des champs (SYS_SCHEMA), tables (SYS_TABLES) et
' registre des modules / menus (SYS_MODULES). Mis en cache en memoire.
'==============================================================================
Option Explicit

Private mFields As Object     ' table -> Collection de cFieldDef (ordre)
Private mFieldIx As Object    ' "table|champ" -> cFieldDef
Private mModules As Object    ' cle module -> dictionnaire
Private mModOrder As Collection

Public Sub Schema_Reset()
    Set mFields = Nothing
    Set mFieldIx = Nothing
    Set mModules = Nothing
    Set mModOrder = Nothing
End Sub

Private Sub LoadFields()
    Dim data As Variant, r As Long, f As cFieldDef, t As String
    Set mFields = NewDict()
    Set mFieldIx = NewDict()
    data = DB_Data("T_SCHEMA")
    If IsEmpty(data) Then Fail "Schema SYS_SCHEMA vide : classeur corrompu.", ERR_DATA
    For r = 1 To UBound(data, 1)
        Set f = New cFieldDef
        f.Init ToStr(data(r, 1)), ToStr(data(r, 2)), ToStr(data(r, 3)), ToStr(data(r, 4)), ToStr(data(r, 5)), _
               ToLng(data(r, 6)), (ToStr(data(r, 7)) = "Oui"), ToLng(data(r, 8))
        t = f.TableName
        If Not mFields.Exists(t) Then mFields.Add t, New Collection
        mFields(t).Add f
        mFieldIx(t & "|" & f.Name) = f
    Next r
End Sub

Public Function Schema_Fields(ByVal tableName As String) As Collection
    If mFields Is Nothing Then LoadFields
    If Not mFields.Exists(tableName) Then Fail "Table non decrite dans le schema : " & tableName, ERR_DATA
    Set Schema_Fields = mFields(tableName)
End Function

Public Function Schema_Field(ByVal tableName As String, ByVal fieldName As String) As cFieldDef
    If mFieldIx Is Nothing Then LoadFields
    If mFieldIx.Exists(tableName & "|" & fieldName) Then Set Schema_Field = mFieldIx(tableName & "|" & fieldName)
End Function

Public Function Schema_TableLabel(ByVal tableName As String) As String
    Schema_TableLabel = ToStr(DB_Lookup("T_TABLES", "TableName", tableName, "Label", tableName))
End Function

Public Function Schema_HasAudit(ByVal tableName As String) As Boolean
    Schema_HasAudit = (ToStr(DB_Lookup("T_TABLES", "TableName", tableName, "Audit", "Non")) = "Oui")
End Function

Public Function Schema_AllTables() As Collection
    Dim data As Variant, r As Long, c As New Collection
    data = DB_Data("T_TABLES")
    For r = 1 To ArrayRows(data)
        c.Add ToStr(data(r, 1))
    Next r
    Set Schema_AllTables = c
End Function

' Liste "TABLE.Champ" des champs FK pointant vers tableName
Public Function Schema_ReferencesTo(ByVal tableName As String) As Collection
    Dim data As Variant, r As Long, c As New Collection, f As cFieldDef
    If mFields Is Nothing Then LoadFields
    data = DB_Data("T_SCHEMA")
    For r = 1 To ArrayRows(data)
        Set f = mFieldIx(ToStr(data(r, 1)) & "|" & ToStr(data(r, 2)))
        If f.FType = "FK" And StrComp(f.FKTable, tableName, vbTextCompare) = 0 Then c.Add f.TableName & "." & f.Name
    Next r
    Set Schema_ReferencesTo = c
End Function

' Choix proposes pour une cle etrangere (enregistrements actifs uniquement)
Public Function Schema_FKChoices(ByVal f As cFieldDef) As Variant
    Dim data As Variant, r As Long, c As Long, cAct As Long, cSt As Long, out() As String, n As Long
    data = DB_Data(f.FKTable)
    If IsEmpty(data) Then Schema_FKChoices = Array(): Exit Function
    c = DB_ColIdx(f.FKTable, f.FKCol)
    If DB_HasCol(f.FKTable, "Actif") Then cAct = DB_ColIdx(f.FKTable, "Actif")
    ReDim out(0 To UBound(data, 1) - 1)
    For r = 1 To UBound(data, 1)
        If cAct = 0 Or ToStr(data(r, cAct)) <> "Non" Then
            If Not IsBlank(data(r, c)) Then
                out(n) = ToStr(data(r, c))
                n = n + 1
            End If
        End If
    Next r
    If n = 0 Then Schema_FKChoices = Array(): Exit Function
    ReDim Preserve out(0 To n - 1)
    Schema_FKChoices = out
End Function

' Texte affiche (code) -> ID
Public Function Schema_FKResolve(ByVal f As cFieldDef, ByVal txt As String) As Variant
    Dim r As Long
    r = DB_RowOf(f.FKTable, f.FKCol, txt)
    If r = 0 Then
        If IsNumeric(txt) Then r = DB_RowOf(f.FKTable, "ID", CLng(txt))
    End If
    If r = 0 Then Schema_FKResolve = Empty Else Schema_FKResolve = DB_Data(f.FKTable)(r, DB_ColIdx(f.FKTable, "ID"))
End Function

' Raccourcis utilises par le code metier
Public Function CodeOf(ByVal tableName As String, ByVal id As Variant) As String
    Dim col As String
    Select Case tableName
        Case "T_CHAUFFEURS": col = "Matricule"
        Case "T_ROTATIONS": col = "NumRotation"
        Case "T_FACTURES": col = "NumFacture"
        Case "T_PNEUS": col = "NumSerie"
        Case "T_INCIDENTS": col = "NumIncident"
        Case Else: col = "Code"
    End Select
    CodeOf = ToStr(DB_Lookup(tableName, "ID", id, col, ""))
End Function

Public Function IdOf(ByVal tableName As String, ByVal code As String) As Long
    Dim col As String
    Select Case tableName
        Case "T_CHAUFFEURS": col = "Matricule"
        Case "T_ROTATIONS": col = "NumRotation"
        Case "T_FACTURES": col = "NumFacture"
        Case "T_PNEUS": col = "NumSerie"
        Case Else: col = "Code"
    End Select
    IdOf = ToLng(DB_Lookup(tableName, col, code, "ID", 0))
End Function

Public Function DriverName(ByVal id As Variant) As String
    Dim d As Object
    Set d = DB_Get("T_CHAUFFEURS", id)
    If d Is Nothing Then Exit Function
    DriverName = d("Prenom") & " " & d("Nom")
End Function

'------------------------------------------------------------------ Modules
Private Sub LoadModules()
    Dim data As Variant, r As Long, d As Object, cols As Object, k As Variant
    Set mModules = NewDict()
    Set mModOrder = New Collection
    data = DB_Data("T_MODULES")
    Set cols = DB_Cols("T_MODULES")
    For r = 1 To ArrayRows(data)
        Set d = NewDict()
        For Each k In cols.Keys
            d(k) = ToStr(data(r, cols(k)))
        Next k
        mModules.Add d("ModKey"), d
        mModOrder.Add d("ModKey")
    Next r
End Sub

Public Function Mod_Get(ByVal key As String) As Object
    If mModules Is Nothing Then LoadModules
    If Not mModules.Exists(key) Then Fail "Module inconnu : " & key, ERR_DATA
    Set Mod_Get = mModules(key)
End Function

Public Function Mod_Keys() As Collection
    If mModules Is Nothing Then LoadModules
    Set Mod_Keys = mModOrder
End Function

Public Function Mod_HasAction(ByVal key As String, ByVal action As String) As Boolean
    Mod_HasAction = InList(action, Mod_Get(key)("Actions"))
End Function

Public Function Mod_ForTable(ByVal tableName As String) As String
    Dim k As Variant
    If mModules Is Nothing Then LoadModules
    For Each k In mModOrder
        If StrComp(mModules(k)("TableName"), tableName, vbTextCompare) = 0 Then Mod_ForTable = k: Exit Function
    Next k
End Function
