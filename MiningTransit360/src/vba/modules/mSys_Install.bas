Attribute VB_Name = "mSys_Install"
'==============================================================================
' MINING TRANSIT 360 - mSys_Install
' Installation / reparation de l'interface (appelee par le script de
' construction et disponible pour l'administrateur) :
'  - incorporation des icones dans la feuille ASSETS (application autonome) ;
'  - masquage des feuilles techniques ;
'  - mots de passe de demonstration (calcules par le VBA) ;
'  - construction du tableau de bord.
'==============================================================================
Option Explicit

' Point d'entree du script de construction (session systeme, aucune fenetre bloquante)
Public Sub Install_Run(Optional ByVal assetsDir As String = "")
    Sec_SystemSession
    DB_Invalidate
    Schema_Reset
    Install_EmbedAssets assetsDir
    Install_DemoPasswords
    Install_Sheets
    Dash_Build
    Fuel_RefreshTank
    Log_Audit "INSTALLATION", "", "", APP_NAME & " v" & APP_VERSION
    Sec_Logout
End Sub

' Copie les icones PNG dans la feuille ASSETS (utilisees si le dossier assets est absent)
Public Sub Install_EmbedAssets(Optional ByVal assetsDir As String = "")
    Dim ws As Worksheet, file As Object, s As Shape, x As Double, nm As String, img As Variant
    If Len(assetsDir) = 0 Then assetsDir = Assets_Path()
    Set ws = ThisWorkbook.Worksheets("ASSETS")
    UI_DeleteShapes ws
    If Not FSO().FolderExists(assetsDir & "\icons") Then Exit Sub
    For Each file In FSO().GetFolder(assetsDir & "\icons").Files
        If LCase$(Right$(file.Name, 4)) = ".png" Then
            nm = "ico_" & Left$(file.Name, Len(file.Name) - 4)
            Set s = ws.Shapes.AddPicture(file.Path, msoFalse, msoTrue, x, 0, 24, 24)
            s.Name = nm
            x = x + 26
        End If
    Next file
    For Each img In Array("logo", "banner")
        If FSO().FileExists(assetsDir & "\" & img & ".png") Then
            Set s = ws.Shapes.AddPicture(assetsDir & "\" & img & ".png", msoFalse, msoTrue, 0, 40, 120, 60)
            s.Name = "img_" & img
        End If
    Next img
End Sub

' Mots de passe des comptes de demonstration recalcules par le VBA (garantit la coherence
' de l'empreinte meme si le jeu de donnees a ete produit par un autre outil)
Public Sub Install_DemoPasswords()
    Dim pw As Variant, i As Long, id As Long
    If Not Param_Bool("DEMO_MODE", False) Then Exit Sub
    pw = Array("admin", "Admin@2026", "directeur", "Direction@2026", "exploitation", "Exploit@2026", "comptable", "Compta@2026", "saisie", "Saisie@2026")
    For i = 0 To UBound(pw) Step 2
        id = ToLng(DB_Lookup("T_USERS", "Login", pw(i), "ID", 0))
        If id > 0 Then
            DB_Update "T_USERS", id, NewRecord("HashMDP", Sec_PasswordHash(CStr(pw(i + 1)), ToStr(DB_Value("T_USERS", id, "Sel"))), "Echecs", 0), "ID", False
        End If
    Next i
End Sub

Public Sub Install_Sheets()
    Dim ws As Worksheet
    For Each ws In ThisWorkbook.Worksheets
        Select Case True
            Case ws.Name = "ACCUEIL"
                ws.Visible = xlSheetVisible
                ws.Tab.Color = CLR_ORANGE()
            Case ws.Name = "RAPPORT" Or ws.Name = "FACTURE" Or ws.Name = "TESTS" Or ws.Name = "FICHE"
                ws.Visible = xlSheetVisible
                ws.Tab.Color = CLR_TEAL()
            Case Left$(ws.Name, 3) = "DB_" Or Left$(ws.Name, 4) = "SYS_" Or ws.Name = "CALC" Or ws.Name = "ASSETS"
                ws.Visible = xlSheetVeryHidden
        End Select
    Next ws
    ThisWorkbook.Worksheets("ACCUEIL").Move Before:=ThisWorkbook.Worksheets(1)
End Sub

' Rend visibles les feuilles de donnees (administration / maintenance)
Public Sub Admin_ShowDataSheets()
    Dim ws As Worksheet
    If Sec_CurrentProfile() <> "Administrateur" Then Fail "Réservé à l'administrateur.", ERR_RIGHTS
    For Each ws In ThisWorkbook.Worksheets
        If Left$(ws.Name, 3) = "DB_" Or Left$(ws.Name, 4) = "SYS_" Then ws.Visible = xlSheetVisible
    Next ws
    Log_Audit "AFFICHAGE-DONNEES", "", "", "Feuilles de données rendues visibles"
End Sub

Public Sub Admin_HideDataSheets()
    Install_Sheets
End Sub

' Secours : reinitialise le mot de passe administrateur (a executer depuis l'editeur VBA, Alt+F11)
Public Sub Install_EmergencyAdminReset()
    Dim id As Long
    Sec_SystemSession
    id = ToLng(DB_Lookup("T_USERS", "Login", "admin", "ID", 0))
    If id = 0 Then
        id = DB_Insert("T_USERS", NewRecord("Login", "admin", "NomComplet", "Administrateur Système", "Profil", "Administrateur", "Actif", "Oui", "Echecs", 0))
    End If
    DB_Update "T_USERS", id, NewRecord("Actif", "Oui", "Profil", "Administrateur"), "ID", False
    Sec_SetPassword id, "Admin@2026", True
    Log_Audit "SECOURS-ADMIN", "T_USERS", id, "Mot de passe administrateur réinitialisé (Admin@2026, changement exigé)"
    Sec_Logout
    MsgBox "Compte admin réinitialisé : admin / Admin@2026 (changement exigé à la connexion).", vbInformation, APP_NAME
End Sub
