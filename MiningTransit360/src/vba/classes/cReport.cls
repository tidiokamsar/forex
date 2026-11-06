VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cReport"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - cReport
' Description d'un etat imprimable : titre, periode, filtres, blocs (infos,
' tableaux avec totaux, textes), signatures. Rendu par mRpt_Engine.
' Types de colonnes : T texte, D date, H heure, N0 / N1 / N2 nombre, M montant, P pourcentage
'==============================================================================
Option Explicit

Public Title As String
Public Subtitle As String
Public Category As String
Public Filters As String
Public Orientation As String          ' auto | portrait | landscape
Public Signatures As Boolean
Public FileStem As String
Private mBlocks As Collection

Private Sub Class_Initialize()
    Set mBlocks = New Collection
    Orientation = "auto"
    Signatures = True
End Sub

Public Function Blocks() As Collection
    Set Blocks = mBlocks
End Function

Public Sub SetPeriod(ByVal d1 As Date, ByVal d2 As Date)
    If d1 = d2 Then
        Subtitle = "Journée du " & FmtDate(d1)
    ElseIf d1 = MonthStart(d1) And d2 = MonthEnd(d1) Then
        Subtitle = "Mois de " & MonthNameFR(d1) & "  (du " & FmtDate(d1) & " au " & FmtDate(d2) & ")"
    Else
        Subtitle = "Période du " & FmtDate(d1) & " au " & FmtDate(d2)
    End If
End Sub

' Bloc de couples libelle / valeur (pairs = Array(l1, v1, l2, v2, ...))
Public Sub AddInfo(ByVal title As String, ByVal pairs As Variant)
    Dim d As Object
    Set d = NewDict()
    d("Kind") = "INFO"
    d("Title") = title
    d("Pairs") = pairs
    mBlocks.Add d
End Sub

' headers / types / totals : chaines separees par "|" ; data : tableau 2D 1-based
Public Sub AddTable(ByVal title As String, ByVal headers As String, ByVal types As String, ByVal data As Variant, _
                    Optional ByVal totals As String = "")
    Dim d As Object
    Set d = NewDict()
    d("Kind") = "TABLE"
    d("Title") = title
    d("Headers") = Split(headers, "|")
    d("Types") = Split(types, "|")
    d("Data") = data
    If Len(totals) > 0 Then d("Totals") = Split(totals, "|") Else d("Totals") = Empty
    mBlocks.Add d
End Sub

Public Sub AddText(ByVal text As String, Optional ByVal style As String = "normal")
    Dim d As Object
    Set d = NewDict()
    d("Kind") = "TEXT"
    d("Text") = text
    d("Style") = style
    mBlocks.Add d
End Sub

Public Function MaxCols() As Long
    Dim b As Object, n As Long
    MaxCols = 4
    For Each b In mBlocks
        If b("Kind") = "TABLE" Then
            n = UBound(b("Headers")) + 1
            If n > MaxCols Then MaxCols = n
        End If
    Next b
End Function

Public Function IsLandscape() As Boolean
    If Orientation = "landscape" Then IsLandscape = True: Exit Function
    If Orientation = "portrait" Then Exit Function
    IsLandscape = (MaxCols() > 7)
End Function
