Attribute VB_Name = "mSys_Backup"
'==============================================================================
' MINING TRANSIT 360 - mSys_Backup
' Sauvegarde manuelle / automatique / avant restauration / a la cloture,
' verification d'integrite de la copie, historique, rotation des copies,
' restauration des DONNEES depuis une sauvegarde (le code reste celui en cours).
'
' Regles :
'  - une sauvegarde est une copie complete du classeur (SaveCopyAs) horodatee ;
'  - chaque copie est rouverte (macros desactivees) et verifiee : tables
'    presentes et nombre de lignes identique ;
'  - la rotation ne supprime JAMAIS la derniere sauvegarde verifiee ;
'  - toute restauration est precedee d'une sauvegarde "Pre-restauration".
'==============================================================================
Option Explicit

Public Function Backup_Folder() As String
    Backup_Folder = SubFolder("SAUV_DOSSIER", "Sauvegardes")
End Function

Public Function Backup_Run(Optional ByVal kind As String = "Manuelle", Optional ByVal silent As Boolean = False) As String
    Dim path As String, ok As Boolean, sizeKb As Long, ext As String
    If kind = "Manuelle" Then Sec_Require "SAUVEGARDES", "L"
    ext = Mid$(ThisWorkbook.Name, InStrRev(ThisWorkbook.Name, "."))
    path = Backup_Folder() & Application.PathSeparator & "MT360_" & Format$(Now, "yyyymmdd_hhnnss") & "_" & SafeFileName(kind) & ext
    Status "Sauvegarde en cours..."
    ThisWorkbook.SaveCopyAs path
    ok = Backup_Verify(path)
    sizeKb = FileLen(path) \ 1024
    DB_Insert "T_SAUVEGARDES", NewRecord("Horodatage", Stamp(), "TypeSauv", kind, "Fichier", path, "TailleKo", sizeKb, _
                                         "Verifiee", IIf(ok, "Oui", "Non"), "Utilisateur", Sec_CurrentLogin()), False
    If ok Then Param_Set "SAUV_DERNIERE", Stamp(), "Horodatage de la dernière sauvegarde (système)", "Sauvegarde", "Texte"
    Log_Audit "SAUVEGARDE", "T_SAUVEGARDES", "", kind & " -> " & path & IIf(ok, " (vérifiée)", " (ECHEC VERIFICATION)")
    Backup_Prune
    Status IIf(ok, "Sauvegarde vérifiée", "Sauvegarde NON vérifiée")
    If Not silent Then
        If ok Then
            UI_Alert "Sauvegarde effectuée et vérifiée :" & vbLf & path & vbLf & "(" & sizeKb & " Ko)", "success", "Sauvegarde"
        Else
            UI_Alert "La copie a été créée mais sa vérification a échoué :" & vbLf & path & vbLf & "Recommencez la sauvegarde.", "error", "Sauvegarde"
        End If
    End If
    Backup_Run = path
End Function

' Ouvre la copie sans executer ses macros et compare la structure / volumetrie
Public Function Backup_Verify(ByVal path As String) As Boolean
    Dim wb As Workbook, t As Variant, lo As ListObject, ok As Boolean, prevSec As Long, prevEv As Boolean, cnt As Long
    On Error GoTo Fail_
    prevSec = Application.AutomationSecurity
    prevEv = Application.EnableEvents
    Application.AutomationSecurity = 3          ' msoAutomationSecurityForceDisable
    Application.EnableEvents = False
    Set wb = Workbooks.Open(Filename:=path, ReadOnly:=True, UpdateLinks:=0, AddToMru:=False)
    ok = True
    For Each t In Schema_AllTables()
        Set lo = FindLO(wb, CStr(t))
        If lo Is Nothing Then
            ok = False
        Else
            cnt = 0
            If Not lo.DataBodyRange Is Nothing Then
                If Application.WorksheetFunction.CountA(lo.DataBodyRange) > 0 Then cnt = lo.ListRows.Count
            End If
            If t <> "T_SAUVEGARDES" And t <> "T_AUDIT" And t <> "T_PARAM" And t <> "T_SEQ" Then
                If cnt <> DB_Count(CStr(t)) Then ok = False
            End If
        End If
    Next t
    wb.Close SaveChanges:=False
    Application.AutomationSecurity = prevSec
    Application.EnableEvents = prevEv
    Backup_Verify = ok
    Exit Function
Fail_:
    On Error Resume Next
    If Not wb Is Nothing Then wb.Close SaveChanges:=False
    Application.AutomationSecurity = prevSec
    Application.EnableEvents = prevEv
    Backup_Verify = False
End Function

Public Function FindLO(ByVal wb As Workbook, ByVal tableName As String) As ListObject
    Dim ws As Worksheet, lo As ListObject
    For Each ws In wb.Worksheets
        For Each lo In ws.ListObjects
            If StrComp(lo.Name, tableName, vbTextCompare) = 0 Then Set FindLO = lo: Exit Function
        Next lo
    Next ws
End Function

' Conserve les N dernieres sauvegardes ; ne supprime jamais la derniere verifiee
Public Sub Backup_Prune()
    Dim keep As Long, data As Variant, r As Long, n As Long, lastOk As Long, cF As Long, cV As Long, f As String
    keep = Param_Num("SAUV_CONSERVER", 30)
    data = DB_Data("T_SAUVEGARDES")
    n = ArrayRows(data)
    If n <= keep Then Exit Sub
    cF = DB_ColIdx("T_SAUVEGARDES", "Fichier"): cV = DB_ColIdx("T_SAUVEGARDES", "Verifiee")
    For r = n To 1 Step -1
        If data(r, cV) = "Oui" And FileExists(ToStr(data(r, cF))) Then lastOk = r: Exit For
    Next r
    For r = 1 To n - keep
        f = ToStr(data(r, cF))
        If r <> lastOk And FileExists(f) Then
            On Error Resume Next
            Kill f
            On Error GoTo 0
        End If
    Next r
End Sub

' Sauvegarde automatique a la fermeture si la derniere est trop ancienne
Public Sub Backup_AutoIfDue()
    Dim lastS As String, last As Date
    On Error GoTo Quiet
    lastS = ToStr(Param_Get("SAUV_DERNIERE", ""))
    If Len(lastS) > 0 Then last = ToDate(Left$(lastS, 10)) + TimeOr0(Mid$(lastS, 12, 5))
    If Len(lastS) = 0 Or (Now - last) * 24 >= Param_Num("SAUV_AUTO_HEURES", 24) Then Backup_Run "Automatique", True
Quiet:
End Sub

'------------------------------------------------------------------ Restauration
Public Sub Backup_RestoreDialog()
    Dim src As Variant
    Sec_Require "SAUVEGARDES", "V"
    If Sec_CurrentProfile() <> "Administrateur" Then Fail "Restauration réservée à l'administrateur.", ERR_RIGHTS
    ChDir Backup_Folder()
    src = Application.GetOpenFilename("Sauvegardes MT360 (*.xlsm;*.xlsx),*.xlsm;*.xlsx", , "Choisir la sauvegarde à restaurer")
    If VarType(src) = vbBoolean Then Exit Sub
    If Not UI_Confirm("Restaurer les DONNÉES depuis :" & vbLf & src & vbLf & vbLf & _
                      "Toutes les données actuelles seront remplacées. Une sauvegarde de sécurité de l'état actuel est faite avant.", _
                      "Restauration", "Restaurer", True) Then Exit Sub
    Backup_Restore CStr(src)
    UI_Alert "Restauration terminée. Contrôlez les données (Administration > Contrôle de cohérence).", "success", "Restauration"
End Sub

Public Sub Backup_Restore(ByVal path As String)
    Dim wb As Workbook, t As Variant, lo As ListObject, data As Variant, prevSec As Long, missing As String, mapped As Variant
    If Not FileExists(path) Then Fail "Fichier introuvable : " & path
    Backup_Run "Pré-restauration", True
    prevSec = Application.AutomationSecurity
    Application.AutomationSecurity = 3
    Application.EnableEvents = False
    ScreenOff
    On Error GoTo Fail_
    Set wb = Workbooks.Open(Filename:=path, ReadOnly:=True, UpdateLinks:=0, AddToMru:=False)
    ' controle prealable : toutes les tables metier presentes
    For Each t In Schema_AllTables()
        If t <> "T_SAUVEGARDES" Then
            If FindLO(wb, CStr(t)) Is Nothing Then missing = missing & t & " "
        End If
    Next t
    If Len(missing) > 0 Then Err.Raise ERR_DATA, , "Sauvegarde incomplète ou incompatible (tables absentes : " & missing & ")."
    For Each t In Schema_AllTables()
        If t <> "T_SAUVEGARDES" Then
            Set lo = FindLO(wb, CStr(t))
            mapped = MapColumns(lo, CStr(t))
            DB_ReplaceAll CStr(t), mapped
        End If
    Next t
    wb.Close SaveChanges:=False
    Application.AutomationSecurity = prevSec
    DB_Invalidate
    Schema_Reset
    Sec_ReloadRights
    ScreenOn
    Log_Audit "RESTAURATION", "", "", path
    Exit Sub
Fail_:
    Dim msg As String, num As Long
    msg = Err.Description: num = Err.Number
    On Error Resume Next
    If Not wb Is Nothing Then wb.Close SaveChanges:=False
    Application.AutomationSecurity = prevSec
    ScreenOn True
    Err.Raise IIf(IsBizError(num), num, ERR_DATA), , "Restauration interrompue : " & msg & vbLf & "Une sauvegarde pré-restauration a été faite : restaurez-la si nécessaire."
End Sub

' Aligne les colonnes d'une table source (sauvegarde / jeu demo) sur la structure courante (par nom)
Public Function MapColumns(ByVal srcLo As ListObject, ByVal tableName As String) As Variant
    Dim cols As Object, n As Long, m As Long, out() As Variant, src As Variant, i As Long, j As Long, k As Variant, srcIdx As Long
    Set cols = DB_Cols(tableName)
    m = cols.Count
    If srcLo.DataBodyRange Is Nothing Then MapColumns = Empty: Exit Function
    If Application.WorksheetFunction.CountA(srcLo.DataBodyRange) = 0 Then MapColumns = Empty: Exit Function
    n = srcLo.ListRows.Count
    src = srcLo.DataBodyRange.Value
    If n = 1 Then
        Dim one() As Variant
        ReDim one(1 To 1, 1 To srcLo.ListColumns.Count)
        For j = 1 To srcLo.ListColumns.Count
            one(1, j) = srcLo.DataBodyRange.Cells(1, j).Value
        Next j
        src = one
    End If
    ReDim out(1 To n, 1 To m)
    For Each k In cols.Keys
        srcIdx = 0
        For j = 1 To srcLo.ListColumns.Count
            If StrComp(srcLo.ListColumns(j).Name, k, vbTextCompare) = 0 Then srcIdx = j: Exit For
        Next j
        If srcIdx > 0 Then
            For i = 1 To n
                out(i, cols(k)) = src(i, srcIdx)
            Next i
        End If
    Next k
    MapColumns = out
End Function
