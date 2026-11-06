Attribute VB_Name = "mBiz_Admin"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Admin
' Administration : utilisateurs, mots de passe, droits, parametres.
'==============================================================================
Option Explicit

Public Sub Admin_BeforeSaveUser(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    rec("Login") = LCase$(Trim$(ToStr(rec("Login"))))
    If Len(rec("Login")) < 3 Then Fail "Identifiant trop court (3 caractères minimum)."
    If InStr(rec("Login"), " ") > 0 Then Fail "L'identifiant ne doit pas contenir d'espace."
    If Not old Is Nothing Then
        If ToStr(old("Profil")) = "Administrateur" And (ToStr(rec("Profil")) <> "Administrateur" Or ToStr(rec("Actif")) = "Non") Then
            If DB_CountIf("T_USERS", "Profil", "=", "Administrateur", "Actif", "=", "Oui") <= 1 Then Fail "Il doit rester au moins un administrateur actif."
        End If
    End If
    If isNew Then rec("Echecs") = 0
End Sub

Public Sub Admin_AfterNewUser(ByVal id As Long)
    Dim f As frmInput, p1 As String, p2 As String
    Set f = Dlg_New("Mot de passe initial", "Utilisateur " & DB_Value("T_USERS", id, "Login") & " - il devra le changer à la première connexion", CLR_NAVY())
    f.AddField "P1", "Mot de passe", "PASSWORD", "", , True
    f.AddField "P2", "Confirmation", "PASSWORD", "", , True
    f.AddAction "OK", "Définir", CLR_NAVY(), True
    Do
        f.ShowDialog
        p1 = f.FieldValue("P1"): p2 = f.FieldValue("P2")
        If p1 = p2 And Len(p1) >= 6 Then Exit Do
        UI_Alert "Les mots de passe doivent être identiques et comporter au moins 6 caractères.", "warning"
    Loop
    f.Dispose
    Sec_SetPassword id, p1, True
End Sub

Public Sub Admin_ResetPassword(ByVal userId As Long)
    Dim f As frmInput, p1 As String
    Sec_Require "UTILISATEURS", "M"
    Set f = Dlg_New("Réinitialiser le mot de passe", "Utilisateur " & DB_Value("T_USERS", userId, "Login"), CLR_NAVY())
    f.AddField "P1", "Nouveau mot de passe provisoire", "PASSWORD", "", , True, "6 caractères minimum ; changement exigé à la prochaine connexion"
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "OK", "Réinitialiser", CLR_NAVY(), True
    If f.ShowDialog = "OK" Then
        p1 = f.FieldValue("P1")
        f.Dispose
        Sec_SetPassword userId, p1, True
        UI_Alert "Mot de passe réinitialisé. Le compte est déverrouillé.", "success"
    Else
        f.Dispose
    End If
End Sub

' Changement de son propre mot de passe (premiere connexion ou volontaire)
Public Function Admin_ChangeOwnPassword(Optional ByVal forced As Boolean = False) As Boolean
    Dim f As frmInput, uid As Long, r As String
    uid = ToLng(DB_Lookup("T_USERS", "Login", Sec_CurrentLogin(), "ID", 0))
    If uid = 0 Then Exit Function
    Do
        Set f = Dlg_New("Changer mon mot de passe", IIf(forced, "Changement obligatoire avant de continuer", Sec_CurrentName()), CLR_NAVY())
        f.AddField "OLD", "Mot de passe actuel", "PASSWORD", "", , True
        f.AddField "P1", "Nouveau mot de passe", "PASSWORD", "", , True, "6 caractères minimum"
        f.AddField "P2", "Confirmation", "PASSWORD", "", , True
        If Not forced Then f.AddAction "NO", "Annuler", CLR_GREY(), False, True
        f.AddAction "OK", "Enregistrer", CLR_NAVY(), True
        r = f.ShowDialog
        If r <> "OK" Then f.Dispose: Exit Function
        If Not Sec_CheckPassword(uid, f.FieldValue("OLD")) Then
            UI_Alert "Mot de passe actuel incorrect.", "warning"
        ElseIf f.FieldValue("P1") <> f.FieldValue("P2") Then
            UI_Alert "La confirmation ne correspond pas.", "warning"
        ElseIf Len(f.FieldValue("P1")) < 6 Then
            UI_Alert "6 caractères minimum.", "warning"
        ElseIf f.FieldValue("P1") = f.FieldValue("OLD") Then
            UI_Alert "Le nouveau mot de passe doit être différent de l'ancien.", "warning"
        Else
            Sec_SetPassword uid, f.FieldValue("P1"), False
            f.Dispose
            Admin_ChangeOwnPassword = True
            UI_Alert "Mot de passe modifié.", "success"
            Exit Function
        End If
        f.Dispose
    Loop
End Function

Public Sub Admin_BeforeSaveParam(ByVal rec As Object, ByVal old As Object)
    Select Case ToStr(rec("TypeValeur"))
        Case "Nombre"
            If Len(ToStr(rec("Valeur"))) > 0 And ToDbl(rec("Valeur"), -987654321#) = -987654321# Then Fail "Valeur numérique attendue pour " & rec("Cle") & "."
        Case "Date"
            If Len(ToStr(rec("Valeur"))) > 0 Then
                If ToDate(rec("Valeur")) = 0 Then Fail "Date attendue (jj/mm/aaaa) pour " & rec("Cle") & "."
                rec("Valeur") = IsoDate(ToDate(rec("Valeur")))
            End If
        Case "Oui/Non"
            If Not InList(ToStr(rec("Valeur")), "Oui|Non") Then Fail "Valeur Oui ou Non attendue pour " & rec("Cle") & "."
    End Select
    If InList(ToStr(rec("Cle")), "CLOTURE_DERNIER_JOUR|CLOTURE_DERNIER_MOIS|SAUV_DERNIERE|CARB_STOCK_CUVE|APP_VERSION") Then
        If Sec_CurrentProfile() <> "Administrateur" Then Fail "Paramètre système : modification réservée à l'administrateur.", ERR_RIGHTS
    End If
    If Not old Is Nothing Then
        If ToStr(old("Valeur")) <> ToStr(rec("Valeur")) Then Log_Audit "PARAMETRE", "T_PARAM", rec("Cle"), ToStr(old("Valeur")) & " -> " & ToStr(rec("Valeur"))
    End If
End Sub

' Matrice des droits : edition d'un profil
Public Sub Admin_EditRights()
    Dim prof As String, data() As Variant, keys As Collection, k As Variant, i As Long, f As frmInput, r As String, sel As Long, cur As String, nv As String
    Sec_Require "UTILISATEURS", "M"
    prof = UI_Pick("Droits par profil", "Profil", Array("Directeur", "Responsable exploitation", "Comptable", "Agent de saisie"))
    If Len(prof) = 0 Then Exit Sub
    Set keys = Mod_Keys()
    Do
        ReDim data(1 To keys.Count, 1 To 3)
        i = 0
        For Each k In keys
            i = i + 1
            data(i, 1) = k
            data(i, 2) = Mod_Get(k)("Titre")
            data(i, 3) = ToStr(RightsOf(prof, CStr(k)))
        Next k
        Set f = Dlg_New("Droits du profil « " & prof & " »", "L lire, C créer, M modifier, V valider, S supprimer, I imprimer/exporter", CLR_NAVY(), 640)
        f.AddField "G", "", "GRID", "Code|Module|Droits", data, False, "120;300;120", 320
        f.AddField "NEW", "Nouveaux droits du module sélectionné", "TEXT", ""
        f.AddAction "CLOSE", "Fermer", CLR_GREY(), False, True
        f.AddAction "APPLY", "Appliquer", CLR_NAVY(), False, False, False
        r = f.ShowDialog
        sel = f.GridRow("G")
        nv = UCase$(Replace(f.FieldValue("NEW"), " ", ""))
        f.Dispose
        If r <> "APPLY" Then Exit Do
        If sel < 0 Then
            UI_Alert "Sélectionnez un module dans la liste.", "warning"
        Else
            For i = 1 To Len(nv)
                If InStr("LCMVSI", Mid$(nv, i, 1)) = 0 Then Fail "Droit inconnu : " & Mid$(nv, i, 1)
            Next i
            cur = data(sel + 1, 1)
            SetRights prof, cur, nv
        End If
    Loop
End Sub

Private Function RightsOf(ByVal prof As String, ByVal modKey As String) As String
    Dim rows As Collection
    Set rows = DB_Rows("T_DROITS", "Profil", "=", prof, "Module", "=", modKey)
    If rows.Count > 0 Then RightsOf = ToStr(DB_Data("T_DROITS")(rows(1), DB_ColIdx("T_DROITS", "Droits")))
End Function

Private Sub SetRights(ByVal prof As String, ByVal modKey As String, ByVal rights As String)
    Dim rows As Collection, id As Long, old As String
    Set rows = DB_Rows("T_DROITS", "Profil", "=", prof, "Module", "=", modKey)
    If rows.Count = 0 Then
        DB_Insert "T_DROITS", NewRecord("Profil", prof, "Module", modKey, "Droits", rights), False
    Else
        id = ToLng(DB_Data("T_DROITS")(rows(1), DB_ColIdx("T_DROITS", "ID")))
        old = RightsOf(prof, modKey)
        DB_SetValue "T_DROITS", id, "Droits", rights
    End If
    Log_Audit "DROITS", "T_DROITS", prof & "/" & modKey, old & " -> " & rights
    Sec_ReloadRights
End Sub
