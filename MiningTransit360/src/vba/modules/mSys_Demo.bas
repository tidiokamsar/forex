Attribute VB_Name = "mSys_Demo"
'==============================================================================
' MINING TRANSIT 360 - mSys_Demo
' Reinitialisation de l'environnement de demonstration a partir du fichier
' data\demo_dataset.xlsx (une feuille par table, genere par tools/build_workbook.py).
' Disponible uniquement si le parametre DEMO_MODE = Oui.
'==============================================================================
Option Explicit

Public Function Demo_DatasetPath() As String
    Dim p As Variant
    For Each p In Array(AppFolder() & "\data\demo_dataset.xlsx", AppFolder() & "\..\data\demo_dataset.xlsx", AppFolder() & "\demo_dataset.xlsx")
        If FileExists(CStr(p)) Then Demo_DatasetPath = CStr(p): Exit Function
    Next p
End Function

Public Sub Demo_ResetDialog()
    Dim p As String
    If Sec_CurrentProfile() <> "Administrateur" Then Fail "Réinitialisation réservée à l'administrateur.", ERR_RIGHTS
    If Not Param_Bool("DEMO_MODE", False) Then Fail "Le mode démonstration est désactivé (paramètre DEMO_MODE) : réinitialisation interdite sur des données réelles."
    p = Demo_DatasetPath()
    If Len(p) = 0 Then
        Dim pick As Variant
        pick = Application.GetOpenFilename("Jeu de démonstration (demo_dataset.xlsx),*.xlsx", , "Localiser demo_dataset.xlsx")
        If VarType(pick) = vbBoolean Then Exit Sub
        p = CStr(pick)
    End If
    If Not UI_Confirm("Réinitialiser l'environnement de démonstration ?" & vbLf & vbLf & _
                      "Toutes les données actuelles seront remplacées par le jeu de démonstration fictif." & vbLf & _
                      "Une sauvegarde de l'état actuel est réalisée avant l'opération.", "Réinitialisation démo", "Réinitialiser", True) Then Exit Sub
    Demo_Reset p, False
    UI_Alert "Environnement de démonstration réinitialisé." & vbLf & "Connectez-vous avec admin / Admin@2026.", "success", "Démo"
End Sub

Public Sub Demo_Reset(ByVal path As String, Optional ByVal skipBackup As Boolean = False)
    Dim wb As Workbook, t As Variant, lo As ListObject, prevSec As Long, keepUsers As Variant
    If Not skipBackup Then Backup_Run "Pré-restauration", True
    prevSec = Application.AutomationSecurity
    Application.AutomationSecurity = 3
    ScreenOff
    On Error GoTo Fail_
    Set wb = Workbooks.Open(Filename:=path, ReadOnly:=True, UpdateLinks:=0, AddToMru:=False)
    For Each t In Schema_AllTables()
        If t <> "T_SAUVEGARDES" Then
            Set lo = FindLO(wb, "D" & CStr(t))
            If Not lo Is Nothing Then DB_ReplaceAll CStr(t), MapColumns(lo, CStr(t))
        End If
    Next t
    wb.Close SaveChanges:=False
    Application.AutomationSecurity = prevSec
    DB_Invalidate
    Schema_Reset
    Sec_ReloadRights
    Install_DemoPasswords
    Fuel_RefreshTank
    ScreenOn
    Log_Audit "REINIT-DEMO", "", "", path
    App_Persist
    Exit Sub
Fail_:
    Dim msg As String
    msg = Err.Description
    On Error Resume Next
    If Not wb Is Nothing Then wb.Close SaveChanges:=False
    Application.AutomationSecurity = prevSec
    ScreenOn True
    Fail "Réinitialisation interrompue : " & msg, ERR_DATA
End Sub
