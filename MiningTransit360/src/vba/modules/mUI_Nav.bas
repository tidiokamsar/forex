Attribute VB_Name = "mUI_Nav"
'==============================================================================
' MINING TRANSIT 360 - mUI_Nav
' Navigation : ouverture des modules, sous-menus (hubs), session utilisateur,
' demarrage de l'application, mode plein ecran.
'==============================================================================
Option Explicit

Private mKiosk As Boolean

'------------------------------------------------------------------ Ouverture
Public Sub Nav_Home()
    Nav_Open "DASHBOARD"
End Sub

Public Sub Nav_Open(ByVal key As String)
    On Error GoTo EH
    If Not Sec_IsLogged() Then App_Login: Exit Sub
    Select Case key
        Case "DASHBOARD"
            DashSheet().Activate
            Dash_FitZoom
            Dash_Refresh
        Case "RAPPORTS"
            Sec_Require "RAPPORTS", "L"
            Dim fr As frmReports
            Set fr = New frmReports
            fr.Init
            fr.Show
            Unload fr
        Case "EXPLOITATION": Nav_Hub "HUB_EXPLOITATION"
        Case Else
            If Left$(key, 4) = "HUB_" Then
                Nav_Hub key
            Else
                Nav_OpenList key
            End If
    End Select
    Exit Sub
EH:
    Log_Error "Nav_Open(" & key & ")", Err.Number, Err.Description
End Sub

Public Sub Nav_OpenList(ByVal modKey As String)
    Dim f As frmList
    Sec_Require modKey, "L"
    Set f = New frmList
    f.Init modKey
    f.Show
    Unload f
    App_Persist
    If ActiveSheet.Name = DashSheet().Name Then Dash_Refresh
End Sub

' Enregistre le classeur si des donnees ont change (persistance sans action de l'utilisateur)
Public Sub App_Persist()
    On Error GoTo Quiet
    If Not ThisWorkbook.Saved And Not ThisWorkbook.ReadOnly Then
        Status "Enregistrement des données..."
        ThisWorkbook.Save
        Status "Données enregistrées"
    End If
    Exit Sub
Quiet:
    Log_File "Enregistrement automatique impossible : " & Err.Description
End Sub

' Sous-menus (tuiles)
Public Sub Nav_Hub(ByVal hub As String)
    Dim f As frmInput, r As String, title As String, sub_ As String, accent As Long, items As Variant, it As Variant, p() As String
    Select Case hub
        Case "HUB_EXPLOITATION"
            title = "Exploitation": sub_ = "Pilotage de la journée et organisation du transport": accent = CLR_GREEN()
            items = Array("DAY|Journée en cours|Synthèse par camion", "QUICK_ROT|Saisie rapide|Nouvelle rotation", "PLANNING|Programme journalier|Planification", _
                          "AFFECTATIONS|Affectations|Chauffeurs / camions", "OBJECTIFS|Objectifs|Tonnage mensuel", "CLOTURES|Clôtures|Journalières et mensuelles")
        Case "HUB_ROTATIONS"
            title = "Rotations et pesées": sub_ = "Saisie, contrôle et validation de la production": accent = CLR_GREEN()
            items = Array("ROTATIONS|Rotations|Saisie et validation", "PESEES|Pesées et tickets|Tickets de pont bascule", _
                          "QUICK_ROT|Saisie rapide|Enchaîner les rotations", "VALID_DAY|Valider une journée|Validation en masse")
        Case "HUB_PNEUS"
            title = "Pneumatiques et stock": sub_ = "Actifs pneus, mouvements et consommables": accent = CLR_PURPLE()
            items = Array("PNEUS|Pneumatiques|Fiches et mouvements", "PNEU_MVT|Historique pneus|Tous les mouvements", _
                          "ARTICLES|Stock et consommables|Articles, entrées, sorties", "STOCK_MVT|Mouvements de stock|Historique")
        Case "HUB_MAINTENANCE"
            title = "Incidents et maintenance": sub_ = "Immobilisations et responsabilités contractuelles": accent = CLR_RED()
            items = Array("INCIDENTS|Incidents|Déclaration et clôture", "MAINTENANCE|Maintenance|Ordres de travail")
        Case "HUB_DEPENSES"
            title = "Dépenses et trésorerie": sub_ = "Charges, caisse, avances et personnel": accent = RGB(15, 123, 108)
            items = Array("DEPENSES|Dépenses|Saisie et validation", "CAISSE|Caisse|Petite caisse", "AVANCES|Avances|Avances au personnel", _
                          "PERSONNEL|Personnel|Effectifs hors chauffeurs")
        Case "HUB_RECOUVREMENT"
            title = "Paiements et recouvrement": sub_ = "Encaissements, relances, litiges": accent = CLR_PURPLE()
            items = Array("PAIEMENTS|Paiements|Encaissements clients", "PAY_NEW|Nouvel encaissement|Saisie d'un règlement", _
                          "RECOUVREMENT|Relances|Suivi des relances", "RECLAMATIONS|Réclamations / litiges|Contestations", _
                          "AGING|Balance âgée|Créances par ancienneté", "CLIENT_SIT|Situation client|Relevé de compte")
        Case "HUB_PARAMETRES"
            title = "Paramètres": sub_ = "Référentiels et règles de gestion": accent = CLR_NAVY2()
            items = Array("PARAMETRES|Paramètres|Règles, tarifs, seuils", "CLIENTS|Clients|Tarifs et contrats", "FOURNISSEURS|Fournisseurs|Référentiel", _
                          "LIEUX|Lieux|Fosses et destinations", "DOCUMENTS|Gestion documentaire|Contrats, assurances, permis", "PWD|Mon mot de passe|Changer")
        Case "HUB_ADMIN"
            title = "Administration": sub_ = "Sécurité, traçabilité et données": accent = CLR_NAVY()
            items = Array("UTILISATEURS|Utilisateurs et droits|Comptes et profils", "AUDIT|Audit et historique|Journal des opérations", _
                          "SAUVEGARDES|Sauvegardes|Sauvegarder, restaurer, vérifier", "COHERENCE|Contrôle de cohérence|Intégrité des données", _
                          "CLOTURES|Clôtures|Verrouillage des périodes", "TESTS|Tests automatiques|Recette de l'application")
        Case Else
            Fail "Menu inconnu : " & hub
    End Select
    Set f = Dlg_New(title, sub_, accent, 560)
    For Each it In items
        p = Split(it, "|")
        f.AddTile p(0), p(1), p(2), accent
    Next it
    f.EndTiles
    f.AddAction "CLOSE", "Fermer", CLR_GREY(), False, True
    r = f.ShowDialog
    f.Dispose
    If Len(r) = 0 Or r = "CLOSE" Then Exit Sub
    Select Case r
        Case "QUICK_ROT": Ops_QuickRotation
        Case "VALID_DAY": Act_Run "ROTATIONS", "VALID_DAY", 0, Nothing
        Case "DAY": Nav_DaySummary
        Case "PAY_NEW": Bill_PaymentDialog 0
        Case "AGING": Rpt_RunByKey "FIN_AGING"
        Case "CLIENT_SIT": Act_Run "CLIENTS", "CLIENT_SIT", 0, Nothing
        Case "PWD": Admin_ChangeOwnPassword False
        Case "COHERENCE": Act_Run "CLOTURES", "COHERENCE", 0, Nothing
        Case "TESTS": Test_RunAllDialog
        Case Else: Nav_OpenList r
    End Select
    App_Persist
    If ActiveSheet.Name = DashSheet().Name Then Dash_Refresh
End Sub

' Synthese de la journee par camion (cockpit d'exploitation)
Public Sub Nav_DaySummary()
    Dim t As Date, data As Variant, r As Long, cam As Long, out() As Variant, n As Long, f As frmInput, k As Object
    t = App_Today()
    data = DB_Data("T_CAMIONS")
    ReDim out(1 To ArrayRows(data), 1 To 8)
    For r = 1 To ArrayRows(data)
        cam = ToLng(data(r, DB_ColIdx("T_CAMIONS", "ID")))
        n = n + 1
        out(n, 1) = data(r, DB_ColIdx("T_CAMIONS", "Code"))
        out(n, 2) = data(r, DB_ColIdx("T_CAMIONS", "Statut"))
        out(n, 3) = CodeOf("T_CHAUFFEURS", Fleet_DriverFor(cam, t))
        out(n, 4) = DB_CountIf("T_ROTATIONS", "CamionID", "=", cam, "DateRotation", "=", t, "Statut", "<>", ST_ANNULEE)
        out(n, 5) = FmtNum(DB_Sum("T_ROTATIONS", "PoidsNet", "CamionID", "=", cam, "DateRotation", "=", t, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE), 1)
        out(n, 6) = DB_CountIf("T_PLANNING", "CamionID", "=", cam, "DatePlan", "=", t)
        out(n, 7) = FmtNum(DB_Sum("T_CARBURANT", "Litres", "CamionID", "=", cam, "DateOp", "=", t, "TypeOp", "=", "Distribution"), 0)
        out(n, 8) = IIf(DB_CountIf("T_LOCATION", "CamionID", "=", cam, "DateReleve", "=", t) > 0, "Oui", "Non")
    Next r
    Set k = KPI_Period(t, t)
    Set f = Dlg_New("Journée du " & FmtDate(t), FmtNum(k("Tonnage"), 1) & " t validées | " & k("RotationsAttente") & " rotation(s) en attente de validation", CLR_GREEN(), 860)
    f.AddField "G", "", "GRID", "Camion|Statut|Chauffeur|Rotations|Tonnage (t)|Programmé|Gasoil (L)|Relevé loc.", out, False, "80;100;80;70;90;80;80;80", 260
    f.AddAction "CLOSE", "Fermer", CLR_GREY(), False, True
    f.AddAction "QUICK", "Saisie rapide de rotation", CLR_GREEN(), True
    If f.ShowDialog = "QUICK" Then
        f.Dispose
        Ops_QuickRotation
    Else
        f.Dispose
    End If
End Sub

' Ouvre la fiche d'un enregistrement (NEW / EDIT / VIEW). Retourne l'ID enregistre (0 si annule).
Public Function Rec_OpenForm(ByVal modKey As String, ByVal id As Long, ByVal mode As String) As Long
    Dim f As frmRecord
    Select Case mode
        Case "NEW": Sec_Require modKey, "C"
        Case "EDIT": Sec_Require modKey, "M"
        Case Else: Sec_Require modKey, "L"
    End Select
    Set f = New frmRecord
    f.Init modKey, id, mode
    f.Show
    Rec_OpenForm = f.SavedId
    Unload f
End Function

'------------------------------------------------------------------ Session
Public Sub App_Start()
    On Error GoTo EH
    Application.EnableEvents = True
    DB_Invalidate
    Schema_Reset
    DashSheet().Protect DrawingObjects:=True, Contents:=True, UserInterfaceOnly:=True
    App_Kiosk True
    DashSheet().Activate
    Dash_FitZoom
    App_Login
    Exit Sub
EH:
    Log_Error "App_Start", Err.Number, Err.Description
End Sub

Public Sub App_Login()
    Dim f As frmLogin
    Set f = New frmLogin
    f.Show
    If Not f.Success Then
        Unload f
        If Not Sec_IsLogged() Then
            ' pas de connexion : ecran de garde
            DashSheet().Activate
            UI_Toast "Non connecté - cliquez sur un élément du menu pour vous connecter."
        End If
        Exit Sub
    End If
    Unload f
    If Sec_MustChangePassword() Then
        If Not Admin_ChangeOwnPassword(True) Then Sec_Logout: Exit Sub
    End If
    ActiveWindow.DisplayWorkbookTabs = (Sec_CurrentProfile() = "Administrateur")
    Dash_Refresh
    Dash_ScheduleTick
End Sub

Public Sub App_Logout()
    If Not UI_Confirm("Se déconnecter de " & APP_NAME & " ?", "Déconnexion", "Se déconnecter") Then Exit Sub
    Sec_Logout
    UI_SetText DashSheet(), "usr_menu", "Utilisateur : -"
    App_Login
End Sub

Public Sub App_UserMenu()
    Dim r As String, f As frmInput
    Set f = Dlg_New(Sec_CurrentName(), "Profil : " & Sec_CurrentProfile(), CLR_NAVY(), 460)
    f.AddTile "PWD", "Changer mon mot de passe", "Sécurité du compte", CLR_NAVY2()
    f.AddTile "REFRESH", "Actualiser", "Recalculer le tableau de bord", CLR_TEAL()
    f.AddTile "ALERTS", "Toutes les alertes", "Notifications", CLR_ORANGE()
    f.AddTile "FULL", IIf(mKiosk, "Quitter le plein écran", "Mode plein écran"), "Affichage", CLR_BLUE()
    f.AddTile "LOGOUT", "Se déconnecter", "Fin de session", CLR_RED()
    f.EndTiles
    f.AddAction "CLOSE", "Fermer", CLR_GREY(), False, True
    r = f.ShowDialog
    f.Dispose
    Select Case r
        Case "PWD": Admin_ChangeOwnPassword False
        Case "REFRESH": Dash_Refresh
        Case "ALERTS": Alerts_ShowAll
        Case "FULL": App_Kiosk Not mKiosk: Dash_FitZoom
        Case "LOGOUT": App_Logout
    End Select
End Sub

' Mode "application" : masque ruban, barre de formule, onglets (restaure a la sortie)
Public Sub App_Kiosk(ByVal enable As Boolean)
    On Error Resume Next
    If enable And Not Param_Bool("UI_PLEIN_ECRAN", True) Then enable = False
    Application.ExecuteExcel4Macro "SHOW.TOOLBAR(""Ribbon""," & IIf(enable, "False", "True") & ")"
    Application.DisplayFormulaBar = Not enable
    Application.DisplayStatusBar = True
    If Not ActiveWindow Is Nothing Then
        ActiveWindow.DisplayHeadings = False
        If enable Then ActiveWindow.DisplayWorkbookTabs = (Sec_CurrentProfile() = "Administrateur")
    End If
    mKiosk = enable
End Sub

Public Sub App_Shutdown()
    On Error Resume Next
    Dash_StopTick
    App_Kiosk False
    If Sec_IsLogged() Then
        Sec_Logout
        App_Persist
        Backup_AutoIfDue
    End If
End Sub
