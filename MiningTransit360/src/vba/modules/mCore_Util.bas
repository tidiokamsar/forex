Attribute VB_Name = "mCore_Util"
'==============================================================================
' MINING TRANSIT 360 - mCore_Util
' Fonctions communes : dictionnaires, chaines, dates, montants, fichiers,
' erreurs metier, performance d'affichage.
'==============================================================================
Option Explicit

Private mScreenDepth As Long

'------------------------------------------------------------------ Objets
Public Function NewDict() As Object
    Set NewDict = CreateObject("Scripting.Dictionary")
    NewDict.CompareMode = vbTextCompare
End Function

Public Function FSO() As Object
    Set FSO = CreateObject("Scripting.FileSystemObject")
End Function

'------------------------------------------------------------------ Erreurs
' Leve une erreur de regle de gestion (message affiche tel quel a l'utilisateur)
Public Sub Fail(ByVal message As String, Optional ByVal code As Long = 0)
    If code = 0 Then code = ERR_BIZ
    Err.Raise code, APP_NAME, message
End Sub

Public Function IsBizError(ByVal n As Long) As Boolean
    IsBizError = (n = ERR_BIZ Or n = ERR_DATA Or n = ERR_RIGHTS Or n = ERR_CANCEL)
End Function

'------------------------------------------------------------------ Ecran
Public Sub ScreenOff()
    mScreenDepth = mScreenDepth + 1
    Application.ScreenUpdating = False
    Application.EnableEvents = False
    Application.Calculation = xlCalculationManual
End Sub

Public Sub ScreenOn(Optional ByVal force As Boolean = False)
    mScreenDepth = mScreenDepth - 1
    If mScreenDepth < 0 Or force Then mScreenDepth = 0
    If mScreenDepth = 0 Then
        Application.Calculation = xlCalculationAutomatic
        Application.EnableEvents = True
        Application.ScreenUpdating = True
        Application.StatusBar = False
    End If
End Sub

Public Sub Status(ByVal msg As String)
    Application.StatusBar = APP_NAME & " - " & msg
    DoEvents
End Sub

'------------------------------------------------------------------ Valeurs
Public Function Nz(ByVal v As Variant, Optional ByVal dflt As Variant = "") As Variant
    If IsError(v) Then
        Nz = dflt
    ElseIf IsEmpty(v) Or IsNull(v) Then
        Nz = dflt
    ElseIf VarType(v) = vbString Then
        If Len(v) = 0 Then Nz = dflt Else Nz = v
    Else
        Nz = v
    End If
End Function

Public Function IsBlank(ByVal v As Variant) As Boolean
    If IsError(v) Then IsBlank = True: Exit Function
    If IsEmpty(v) Or IsNull(v) Then IsBlank = True: Exit Function
    If VarType(v) = vbString Then IsBlank = (Len(Trim$(v)) = 0)
End Function

Public Function ToDbl(ByVal v As Variant, Optional ByVal dflt As Double = 0) As Double
    On Error GoTo Bad
    If IsBlank(v) Then ToDbl = dflt: Exit Function
    If VarType(v) = vbString Then
        Dim s As String
        s = Trim$(Replace(Replace(v, ChrW(160), ""), " ", ""))
        ' Accepte la virgule ou le point comme separateur decimal
        If Application.International(xlDecimalSeparator) = "," Then
            s = Replace(s, ".", ",")
        Else
            s = Replace(s, ",", ".")
        End If
        If Not IsNumeric(s) Then ToDbl = dflt: Exit Function
        ToDbl = CDbl(s)
    Else
        ToDbl = CDbl(v)
    End If
    Exit Function
Bad:
    ToDbl = dflt
End Function

Public Function ToLng(ByVal v As Variant, Optional ByVal dflt As Long = 0) As Long
    Dim d As Double
    d = ToDbl(v, dflt)
    If d > 2147483647# Or d < -2147483648# Then ToLng = dflt Else ToLng = CLng(d)
End Function

Public Function ToStr(ByVal v As Variant) As String
    If IsError(v) Or IsNull(v) Or IsEmpty(v) Then ToStr = "" Else ToStr = CStr(v)
End Function

' Convertit une saisie (texte jj/mm/aaaa, aaaa-mm-jj ou date Excel) en Date. 0 si invalide.
Public Function ToDate(ByVal v As Variant) As Date
    On Error GoTo Bad
    If IsBlank(v) Then Exit Function
    If VarType(v) = vbDate Then ToDate = DateValue(v): Exit Function
    If IsNumeric(v) And VarType(v) <> vbString Then ToDate = CDate(Int(CDbl(v))): Exit Function
    Dim s As String, p() As String
    s = Trim$(CStr(v))
    If s Like "####-##-##*" Then
        ToDate = DateSerial(CInt(Left$(s, 4)), CInt(Mid$(s, 6, 2)), CInt(Mid$(s, 9, 2)))
        Exit Function
    End If
    s = Replace(Replace(s, "-", "/"), ".", "/")
    p = Split(s, "/")
    If UBound(p) = 2 Then
        If Len(p(2)) = 2 Then p(2) = "20" & p(2)
        ToDate = DateSerial(CInt(p(2)), CInt(p(1)), CInt(p(0)))
        If Day(ToDate) <> CInt(p(0)) Then ToDate = 0
        Exit Function
    End If
Bad:
    ToDate = 0
End Function

' Heure (fraction de jour) depuis "hh:mm", "hhmm", "hh.mm" ou valeur Excel. -1 si invalide.
Public Function ToTime(ByVal v As Variant) As Double
    On Error GoTo Bad
    ToTime = -1
    If IsBlank(v) Then Exit Function
    If VarType(v) = vbDate Or (IsNumeric(v) And VarType(v) <> vbString) Then
        ToTime = CDbl(v) - Int(CDbl(v))
        Exit Function
    End If
    Dim s As String, h As Long, m As Long
    s = Trim$(Replace(Replace(CStr(v), "h", ":"), ".", ":"))
    If InStr(s, ":") > 0 Then
        h = CLng(Split(s, ":")(0)): m = CLng(Val(Split(s, ":")(1)))
    ElseIf Len(s) = 4 And IsNumeric(s) Then
        h = CLng(Left$(s, 2)): m = CLng(Right$(s, 2))
    ElseIf IsNumeric(s) Then
        h = CLng(s): m = 0
    Else
        Exit Function
    End If
    If h < 0 Or h > 23 Or m < 0 Or m > 59 Then Exit Function
    ToTime = TimeSerial(h, m, 0)
    Exit Function
Bad:
    ToTime = -1
End Function

' Heure valide ou 0
Public Function TimeOr0(ByVal v As Variant) As Double
    TimeOr0 = ToTime(v)
    If TimeOr0 < 0 Then TimeOr0 = 0
End Function

'------------------------------------------------------------------ Formats
Public Function FmtMoney(ByVal v As Variant, Optional ByVal withCurrency As Boolean = True) As String
    Dim s As String
    s = Format$(Round(ToDbl(v), 0), "#,##0")
    s = Replace(s, ",", " ")
    s = Replace(s, ChrW(160), " ")
    s = Replace(s, ChrW(8239), " ")
    If withCurrency Then s = s & " GNF"
    FmtMoney = s
End Function

' Montant abrege pour les cartes KPI : 2 368 955 100 -> "2,37 Md" ; 84 000 000 -> "84,0 M"
Public Function FmtShortMoney(ByVal v As Double) As String
    If Abs(v) >= 1000000000# Then
        FmtShortMoney = Format$(v / 1000000000#, "0.00") & " Md GNF"
    ElseIf Abs(v) >= 1000000# Then
        FmtShortMoney = Format$(v / 1000000#, "0.0") & " M GNF"
    Else
        FmtShortMoney = FmtMoney(v)
    End If
End Function

Public Function FmtNum(ByVal v As Variant, Optional ByVal decimals As Long = 0) As String
    Dim f As String
    f = "#,##0"
    If decimals > 0 Then f = f & "." & String$(decimals, "0")
    If Application.International(xlDecimalSeparator) = "," Then
        FmtNum = Format$(ToDbl(v), f)
    Else
        FmtNum = Replace(Format$(ToDbl(v), f), ",", " ")
    End If
    FmtNum = Replace(Replace(FmtNum, ChrW(160), " "), ChrW(8239), " ")
End Function

Public Function FmtPct(ByVal v As Double, Optional ByVal decimals As Long = 1) As String
    FmtPct = Format$(v * 100, "0." & String$(decimals, "0")) & " %"
End Function

Public Function FmtDate(ByVal v As Variant) As String
    If IsBlank(v) Then FmtDate = "": Exit Function
    If ToDate(v) = 0 Then FmtDate = ToStr(v) Else FmtDate = Format$(ToDate(v), "dd/mm/yyyy")
End Function

Public Function FmtTime(ByVal v As Variant) As String
    Dim t As Double
    t = ToTime(v)
    If t < 0 Then FmtTime = "" Else FmtTime = Format$(t, "hh:mm")
End Function

Public Function IsoDate(ByVal d As Date) As String
    IsoDate = Format$(d, "yyyy-mm-dd")
End Function

Public Function Stamp() As String
    Stamp = Format$(Now, "yyyy-mm-dd hh:nn:ss")
End Function

Public Function MonthKey(ByVal d As Date) As String
    MonthKey = Format$(d, "yyyy-mm")
End Function

Public Function MonthStart(ByVal d As Date) As Date
    MonthStart = DateSerial(Year(d), Month(d), 1)
End Function

Public Function MonthEnd(ByVal d As Date) As Date
    MonthEnd = DateSerial(Year(d), Month(d) + 1, 0)
End Function

Public Function MonthFromKey(ByVal key As String) As Date
    MonthFromKey = DateSerial(CInt(Left$(key, 4)), CInt(Mid$(key, 6, 2)), 1)
End Function

Public Function MonthNameFR(ByVal d As Date) As String
    MonthNameFR = Choose(Month(d), "janvier", "février", "mars", "avril", "mai", "juin", "juillet", _
                         "août", "septembre", "octobre", "novembre", "décembre") & " " & Year(d)
End Function

Public Function DayNameFR(ByVal d As Date, Optional ByVal shortName As Boolean = False) As String
    Dim n As String
    n = Choose(Weekday(d, vbMonday), "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
    If shortName Then n = Left$(n, 3)
    DayNameFR = n
End Function

Public Function LongDateFR(ByVal d As Date) As String
    LongDateFR = DayNameFR(d) & " " & Format$(d, "dd") & " " & _
                 StrConv(Left$(MonthNameFR(d), InStr(MonthNameFR(d), " ") - 1), vbProperCase) & " " & Year(d)
End Function

' Duree en heures entre deux (date, heure) ; gere le passage de minuit
Public Function HoursBetween(ByVal d1 As Date, ByVal t1 As Double, ByVal d2 As Date, ByVal t2 As Double) As Double
    HoursBetween = Round(((CDbl(d2) + t2) - (CDbl(d1) + t1)) * 24, 2)
End Function

Public Function ShiftHours(ByVal tStart As Double, ByVal tEnd As Double) As Double
    Dim h As Double
    h = (tEnd - tStart) * 24
    If h < 0 Then h = h + 24
    ShiftHours = Round(h, 2)
End Function

'------------------------------------------------------------------ Chaines
Public Function PadNum(ByVal n As Long, ByVal width As Long) As String
    PadNum = Right$(String$(width, "0") & CStr(n), width)
End Function

Public Function InList(ByVal v As String, ByVal listPipe As String) As Boolean
    InList = (InStr(1, "|" & listPipe & "|", "|" & v & "|", vbTextCompare) > 0)
End Function

Public Function SafeFileName(ByVal s As String) As String
    Dim c As Variant
    For Each c In Array("\", "/", ":", "*", "?", """", "<", ">", "|")
        s = Replace(s, c, "_")
    Next c
    SafeFileName = s
End Function

Public Function JoinCol(ByVal col As Collection, Optional ByVal sep As String = ", ") As String
    Dim v As Variant, s As String
    For Each v In col
        If Len(s) > 0 Then s = s & sep
        s = s & CStr(v)
    Next v
    JoinCol = s
End Function

'------------------------------------------------------------------ Fichiers
Public Function AppFolder() As String
    AppFolder = ThisWorkbook.Path
End Function

Public Function EnsureFolder(ByVal p As String) As String
    Dim f As Object
    Set f = FSO()
    If Len(p) = 0 Then EnsureFolder = "": Exit Function
    If Not f.FolderExists(p) Then
        If Len(f.GetParentFolderName(p)) > 0 Then EnsureFolder f.GetParentFolderName(p)
        f.CreateFolder p
    End If
    EnsureFolder = p
End Function

Public Function SubFolder(ByVal paramKey As String, ByVal defaultName As String) As String
    Dim p As String
    p = ToStr(Param_Get(paramKey, ""))
    If Len(p) = 0 Then p = AppFolder() & Application.PathSeparator & defaultName
    SubFolder = EnsureFolder(p)
End Function

Public Function FileExists(ByVal p As String) As Boolean
    If Len(p) = 0 Then Exit Function
    FileExists = FSO().FileExists(p)
End Function

Public Sub OpenFile(ByVal p As String)
    If Not FileExists(p) Then Fail "Fichier introuvable : " & p
    ThisWorkbook.FollowHyperlink p
End Sub

'------------------------------------------------------------------ Tableaux
Public Function ArrayRows(ByVal a As Variant) As Long
    On Error GoTo Bad
    If IsEmpty(a) Then Exit Function
    ArrayRows = UBound(a, 1) - LBound(a, 1) + 1
    Exit Function
Bad:
    ArrayRows = 0
End Function

' Tri d'un tableau 2D (1-based) sur une colonne (quicksort sur index, O(n log n))
Public Sub SortArray2D(ByRef a As Variant, ByVal col As Long, Optional ByVal descending As Boolean = False)
    Dim n As Long, m As Long, i As Long, k As Long
    Dim idx() As Long, out() As Variant
    n = ArrayRows(a)
    If n < 2 Then Exit Sub
    m = UBound(a, 2)
    ReDim idx(1 To n)
    For i = 1 To n: idx(i) = i: Next i
    QuickSortIdx a, idx, col, 1, n, descending
    ReDim out(1 To n, 1 To m)
    For i = 1 To n
        For k = 1 To m
            out(i, k) = a(idx(i), k)
        Next k
    Next i
    a = out
End Sub

Private Sub QuickSortIdx(ByRef a As Variant, ByRef idx() As Long, ByVal col As Long, _
                         ByVal lo As Long, ByVal hi As Long, ByVal desc As Boolean)
    Dim i As Long, j As Long, t As Long
    Dim pivot As Variant
    i = lo: j = hi
    pivot = a(idx((lo + hi) \ 2), col)
    Do While i <= j
        Do While CompareValues(a(idx(i), col), pivot, desc) < 0
            i = i + 1
        Loop
        Do While CompareValues(a(idx(j), col), pivot, desc) > 0
            j = j - 1
        Loop
        If i <= j Then
            t = idx(i): idx(i) = idx(j): idx(j) = t
            i = i + 1: j = j - 1
        End If
    Loop
    If lo < j Then QuickSortIdx a, idx, col, lo, j, desc
    If i < hi Then QuickSortIdx a, idx, col, i, hi, desc
End Sub

' Vrai pour un nombre, une date ou un texte numerique non vide
Public Function IsNumOrDate(ByVal v As Variant) As Boolean
    If IsError(v) Or IsEmpty(v) Or IsNull(v) Then Exit Function
    If VarType(v) = vbDate Then IsNumOrDate = True: Exit Function
    If VarType(v) = vbString Then
        If Len(Trim$(v)) = 0 Then Exit Function
    End If
    IsNumOrDate = IsNumeric(v)
End Function

' Comparaison generique (numerique si possible, sinon texte). Retourne -1 / 0 / 1.
Public Function CompareValues(ByVal x As Variant, ByVal y As Variant, Optional ByVal desc As Boolean = False) As Long
    Dim r As Long
    If IsError(x) Then x = ""
    If IsError(y) Then y = ""
    If IsNumOrDate(x) And IsNumOrDate(y) Then
        If CDbl(x) > CDbl(y) Then
            r = 1
        ElseIf CDbl(x) < CDbl(y) Then
            r = -1
        End If
    Else
        r = StrComp(ToStr(x), ToStr(y), vbTextCompare)
    End If
    If desc Then r = -r
    CompareValues = r
End Function
