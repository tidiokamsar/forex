Attribute VB_Name = "mCore_Param"
'==============================================================================
' MINING TRANSIT 360 - mCore_Param
' Parametres applicatifs (table T_PARAM) + numerotation (T_SEQ) + date de travail.
'==============================================================================
Option Explicit

'------------------------------------------------------------------ Parametres
Public Function Param_Get(ByVal key As String, Optional ByVal dflt As Variant = "") As Variant
    Dim v As Variant
    v = DB_Lookup("T_PARAM", "Cle", key, "Valeur", Empty)
    If IsBlank(v) Then Param_Get = dflt Else Param_Get = v
End Function

Public Function Param_Num(ByVal key As String, Optional ByVal dflt As Double = 0) As Double
    Param_Num = ToDbl(Param_Get(key, dflt), dflt)
End Function

Public Function Param_Bool(ByVal key As String, Optional ByVal dflt As Boolean = False) As Boolean
    Dim s As String
    s = ToStr(Param_Get(key, IIf(dflt, "Oui", "Non")))
    Param_Bool = InList(s, "Oui|O|Vrai|1|Yes|True")
End Function

Public Sub Param_Set(ByVal key As String, ByVal v As Variant, Optional ByVal desc As String = "", _
                     Optional ByVal cat As String = "Systeme", Optional ByVal typ As String = "Texte")
    If DB_RowOf("T_PARAM", "Cle", key) = 0 Then
        DB_Insert "T_PARAM", NewRecord("Cle", key, "Valeur", CStr(v), "TypeValeur", typ, "Categorie", cat, "Description", desc), False
    Else
        DB_SetValue "T_PARAM", key, "Valeur", CStr(v), "Cle"
    End If
End Sub

' Date de travail : parametre DATE_TRAVAIL si renseigne (demonstration), sinon date systeme
Public Function App_Today() As Date
    Dim v As Variant
    On Error GoTo Fallback
    v = Param_Get("DATE_TRAVAIL", "")
    If Not IsBlank(v) Then
        If ToDate(v) > 0 Then App_Today = ToDate(v): Exit Function
    End If
Fallback:
    App_Today = Date
End Function

Public Function Company() As String
    Company = ToStr(Param_Get("SOCIETE_NOM", "MINING TRANSIT SARL"))
End Function

'------------------------------------------------------------------ Numerotation
' Compteur generique (table T_SEQ, cle -> dernier numero)
Public Function Seq_Next(ByVal key As String) As Long
    Dim cur As Long, r As Long
    r = DB_RowOf("T_SEQ", "Cle", key)
    If r = 0 Then
        DB_Insert "T_SEQ", NewRecord("Cle", key, "Dernier", 1), False
        Seq_Next = 1
    Else
        cur = ToLng(DB_Data("T_SEQ")(r, DB_ColIdx("T_SEQ", "Dernier")))
        DB_SetValue "T_SEQ", key, "Dernier", cur + 1, "Cle"
        Seq_Next = cur + 1
    End If
End Function

' Identifiant technique d'une table : max(compteur, max(ID)) + 1 (robuste aux imports)
Public Function Seq_NextID(ByVal tableName As String) As Long
    Dim n As Long, mx As Long
    n = Seq_Next("ID_" & tableName)
    mx = CLng(DB_MaxValue(tableName, "ID"))
    If n <= mx Then
        n = mx + 1
        DB_SetValue "T_SEQ", "ID_" & tableName, "Dernier", n, "Cle"
    End If
    Seq_NextID = n
End Function

' Numero metier : PREFIXE-AAAA-00001 (ou -0001 pour les factures)
Public Function Seq_Number(ByVal prefix As String, ByVal d As Date, Optional ByVal width As Long = 5) As String
    Seq_Number = prefix & "-" & Year(d) & "-" & PadNum(Seq_Next(prefix & "-" & Year(d)), width)
End Function
