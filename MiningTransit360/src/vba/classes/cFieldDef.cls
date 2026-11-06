VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cFieldDef"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - cFieldDef
' Definition d'un champ (lue dans SYS_SCHEMA). Pilote formulaires, listes,
' validations generiques et exports.
'==============================================================================
Option Explicit

Public TableName As String
Public Name As String
Public Label As String
Public FType As String          ' ID TXT MEMO INT NUM MNY PCT DATE TIME BOOL LIST FK FILE
Public Options As String
Public Order As Long
Public InList As Boolean
Public ListWidth As Long
Public Required As Boolean
Public Unique As Boolean
Public ReadOnly As Boolean
Public Hidden As Boolean
Public FKTable As String
Public FKCol As String
Public Vals As String           ' valeurs autorisees "a|b|c"
Public DefaultValue As Variant
Public HasMin As Boolean
Public MinVal As Double
Public SeqPrefix As String

Public Sub Init(ByVal tbl As String, ByVal fieldName As String, ByVal lbl As String, ByVal typ As String, _
                ByVal opts As String, ByVal ord As Long, ByVal showInList As Boolean, ByVal width As Long)
    Dim part As Variant, k As String, v As String, p As Long
    TableName = tbl
    Name = fieldName
    Label = lbl
    FType = UCase$(typ)
    Options = opts
    Order = ord
    InList = showInList
    ListWidth = width
    DefaultValue = Empty
    For Each part In Split(opts, ";")
        part = Trim$(part)
        If Len(part) > 0 Then
            p = InStr(part, "=")
            If p > 0 Then
                k = LCase$(Left$(part, p - 1))
                v = Mid$(part, p + 1)
            Else
                k = LCase$(part)
                v = ""
            End If
            Select Case k
                Case "req": Required = True
                Case "uniq": Unique = True
                Case "ro": ReadOnly = True
                Case "hid": Hidden = True
                Case "vals": Vals = v
                Case "def": DefaultValue = v
                Case "min": HasMin = True: MinVal = Val(v)
                Case "seq": SeqPrefix = v
                Case "fk"
                    FKTable = Left$(v, InStr(v, ".") - 1)
                    FKCol = Mid$(v, InStr(v, ".") + 1)
            End Select
        End If
    Next part
    If FType = "ID" Then ReadOnly = True
End Sub

Public Property Get IsNumericType() As Boolean
    IsNumericType = InList2(FType, "ID|INT|NUM|MNY|PCT")
End Property

Public Property Get IsChoice() As Boolean
    IsChoice = InList2(FType, "LIST|FK|BOOL")
End Property

Private Function InList2(ByVal v As String, ByVal lst As String) As Boolean
    InList2 = (InStr(1, "|" & lst & "|", "|" & v & "|", vbTextCompare) > 0)
End Function

' Liste des choix proposes a l'utilisateur
Public Function Choices() As Variant
    Select Case FType
        Case "BOOL": Choices = Array("Oui", "Non")
        Case "LIST": Choices = Split(Vals, "|")
        Case "FK": Choices = Schema_FKChoices(Me)
        Case Else: Choices = Array()
    End Select
End Function

' Valeur stockee -> texte affiche
Public Function Display(ByVal v As Variant) As String
    If IsError(v) Then Display = "#ERR": Exit Function
    If IsBlank(v) Then Display = "": Exit Function
    Select Case FType
        Case "DATE": Display = FmtDate(v)
        Case "TIME": Display = FmtTime(v)
        Case "MNY": Display = FmtMoney(v, False)
        Case "NUM": Display = FmtNum(v, 2)
        Case "INT", "ID": Display = CStr(ToLng(v))
        Case "PCT": Display = FmtPct(ToDbl(v))
        Case "FK": Display = ToStr(DB_Lookup(FKTable, "ID", v, FKCol, v))
        Case Else: Display = ToStr(v)
    End Select
End Function

' Texte saisi -> valeur stockee. Leve une erreur metier si conversion impossible.
Public Function Parse(ByVal txt As Variant) As Variant
    Dim s As String
    s = Trim$(ToStr(txt))
    If Len(s) = 0 Then Parse = Empty: Exit Function
    Select Case FType
        Case "DATE"
            If ToDate(s) = 0 Then Fail "« " & Label & " » : date invalide (" & s & "). Format attendu jj/mm/aaaa."
            Parse = ToDate(s)
        Case "TIME"
            If ToTime(s) < 0 Then Fail "« " & Label & " » : heure invalide (" & s & "). Format attendu hh:mm."
            Parse = ToTime(s)
        Case "INT", "ID"
            If Not IsNumeric(Replace(Replace(s, " ", ""), ChrW(160), "")) Then Fail "« " & Label & " » : nombre entier attendu."
            Parse = ToLng(s)
        Case "NUM", "MNY"
            If ToDbl(s, -987654321#) = -987654321# Then Fail "« " & Label & " » : nombre attendu (" & s & ")."
            If FType = "MNY" Then Parse = Round(ToDbl(s), 0) Else Parse = Round(ToDbl(s), 3)
        Case "PCT"
            s = Replace(s, "%", "")
            If ToDbl(s, -987654321#) = -987654321# Then Fail "« " & Label & " » : pourcentage attendu."
            If ToDbl(s) > 1 Then Parse = ToDbl(s) / 100 Else Parse = ToDbl(s)
        Case "FK"
            Parse = Schema_FKResolve(Me, s)
            If IsEmpty(Parse) Then Fail "« " & Label & " » : valeur inconnue (" & s & ")."
        Case "BOOL"
            If InList2(s, "Oui|O|Vrai|1|Yes") Then Parse = "Oui" Else Parse = "Non"
        Case "LIST"
            If Not InList2(s, Vals) Then Fail "« " & Label & " » : valeur non autorisée (" & s & ")."
            Parse = s
        Case Else
            Parse = s
    End Select
End Function
