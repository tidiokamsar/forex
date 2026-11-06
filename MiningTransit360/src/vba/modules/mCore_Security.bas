Attribute VB_Name = "mCore_Security"
'==============================================================================
' MINING TRANSIT 360 - mCore_Security
' Utilisateurs, authentification, droits par profil et par module.
'
' Droits : L lire, C creer, M modifier, V valider, S supprimer, I imprimer/exporter.
' LIMITES (documentees) : un classeur Excel n'est pas un coffre-fort. Toute
' personne disposant du fichier et de competences VBA peut contourner ces
' controles. Ils protegent contre les erreurs et les acces non autorises
' "ordinaires", pas contre une attaque deliberee. Voir docs/04_REGLES_DE_GESTION.
'==============================================================================
Option Explicit

Private mLogin As String
Private mProfile As String
Private mFullName As String
Private mRights As Object

Public Const MAX_FAILED As Long = 5

Public Function Sec_CurrentLogin() As String
    If Len(mLogin) = 0 Then Sec_CurrentLogin = "systeme" Else Sec_CurrentLogin = mLogin
End Function

Public Function Sec_CurrentName() As String
    If Len(mFullName) = 0 Then Sec_CurrentName = "Non connecté" Else Sec_CurrentName = mFullName
End Function

Public Function Sec_CurrentProfile() As String
    Sec_CurrentProfile = mProfile
End Function

Public Function Sec_IsLogged() As Boolean
    Sec_IsLogged = (Len(mLogin) > 0)
End Function

'------------------------------------------------------------------ Empreinte
' Miroir exact de tools/mt_hash.py (4 voies FNV-1a 32 bits, arithmetique en Double)
Private Function FnvStep(ByVal h As Double, ByVal b As Long) As Double
    Dim lo As Double
    lo = h - Int(h / 256#) * 256#
    h = h - lo + (CLng(lo) Xor (b And 255))
    lo = h - Int(h / 256#) * 256#
    h = lo * 16777216# + h * 403#
    FnvStep = h - Int(h / 4294967296#) * 4294967296#
End Function

Private Function Hex8(ByVal h As Double) As String
    Dim hi As Double, lo As Double
    hi = Int(h / 65536#)
    lo = h - hi * 65536#
    Hex8 = Right$("000" & Hex$(CLng(hi)), 4) & Right$("000" & Hex$(CLng(lo)), 4)
End Function

Public Function MT_Hash(ByVal text As String, Optional ByVal rounds As Long = 64) As String
    Dim h(0 To 3) As Double, x As Double, nxt As Double, v As Double
    Dim r As Long, i As Long, k As Long, j As Long, c As Long
    h(0) = 2166136261#: h(1) = 3735928559#: h(2) = 305419896#: h(3) = 2882400001#
    For r = 1 To rounds
        For i = 0 To 3
            x = h(i)
            For j = 1 To Len(text)
                c = AscW(Mid$(text, j, 1))
                If c < 0 Then c = c + 65536
                x = FnvStep(x, c And 255)
                x = FnvStep(x, (c \ 256) And 255)
            Next j
            x = FnvStep(x, (r + i) And 255)
            nxt = h((i + 1) Mod 4)
            For k = 0 To 3
                v = Int(nxt / (256# ^ k))
                v = v - Int(v / 256#) * 256#
                x = FnvStep(x, CLng(v))
            Next k
            h(i) = x
        Next i
    Next r
    MT_Hash = Hex8(h(0)) & Hex8(h(1)) & Hex8(h(2)) & Hex8(h(3))
End Function

Public Function Sec_PasswordHash(ByVal pwd As String, ByVal salt As String) As String
    Sec_PasswordHash = MT_Hash(salt & ":" & pwd, 64)
End Function

Private Function NewSalt() As String
    Randomize
    NewSalt = "S" & Format$(Int(Rnd() * 100000), "00000") & Hex$(Int(Rnd() * 65535))
End Function

'------------------------------------------------------------------ Connexion
' Retourne "" si succes, sinon le message d'erreur
Public Function Sec_Login(ByVal login As String, ByVal pwd As String) As String
    Dim u As Object
    Set u = DB_GetBy("T_USERS", "Login", Trim$(login))
    If u Is Nothing Then
        Log_Audit "CONNEXION-ECHEC", "T_USERS", "", "Identifiant inconnu : " & login
        Sec_Login = "Identifiant ou mot de passe incorrect."
        Exit Function
    End If
    If ToStr(u("Actif")) <> "Oui" Then Sec_Login = "Ce compte est désactivé. Contactez l'administrateur.": Exit Function
    If ToLng(u("Echecs")) >= MAX_FAILED Then
        Sec_Login = "Compte verrouillé après " & MAX_FAILED & " échecs. Un administrateur doit réinitialiser le mot de passe."
        Exit Function
    End If
    If Sec_PasswordHash(pwd, ToStr(u("Sel"))) <> ToStr(u("HashMDP")) Then
        DB_SetValue "T_USERS", u("ID"), "Echecs", ToLng(u("Echecs")) + 1
        Log_Audit "CONNEXION-ECHEC", "T_USERS", u("ID"), "Mot de passe incorrect pour " & login
        Sec_Login = "Identifiant ou mot de passe incorrect."
        Exit Function
    End If
    mLogin = ToStr(u("Login"))
    mProfile = ToStr(u("Profil"))
    mFullName = ToStr(u("NomComplet"))
    Set mRights = Nothing
    DB_Update "T_USERS", u("ID"), NewRecord("Echecs", 0, "DerniereConnexion", Stamp()), "ID", False
    Log_Audit "CONNEXION", "T_USERS", u("ID"), mFullName & " (" & mProfile & ")"
End Function

Public Function Sec_MustChangePassword() As Boolean
    Sec_MustChangePassword = (ToStr(DB_Lookup("T_USERS", "Login", mLogin, "DoitChanger", "Non")) = "Oui")
End Function

Public Sub Sec_Logout()
    If Len(mLogin) > 0 Then Log_Audit "DECONNEXION", "T_USERS", "", mLogin
    mLogin = "": mProfile = "": mFullName = ""
    Set mRights = Nothing
End Sub

Public Sub Sec_SetPassword(ByVal userId As Long, ByVal newPwd As String, Optional ByVal mustChange As Boolean = False)
    Dim salt As String
    If Len(newPwd) < 6 Then Fail "Le mot de passe doit contenir au moins 6 caractères."
    salt = NewSalt()
    DB_Update "T_USERS", userId, NewRecord("Sel", salt, "HashMDP", Sec_PasswordHash(newPwd, salt), _
                                          "DoitChanger", IIf(mustChange, "Oui", "Non"), "Echecs", 0)
    Log_Audit "MOT-DE-PASSE", "T_USERS", userId, "Mot de passe défini" & IIf(mustChange, " (changement exigé)", "")
End Sub

Public Function Sec_CheckPassword(ByVal userId As Long, ByVal pwd As String) As Boolean
    Dim u As Object
    Set u = DB_Get("T_USERS", userId)
    If u Is Nothing Then Exit Function
    Sec_CheckPassword = (Sec_PasswordHash(pwd, ToStr(u("Sel"))) = ToStr(u("HashMDP")))
End Function

'------------------------------------------------------------------ Droits
Private Sub LoadRights()
    Dim rows As Collection, r As Variant, data As Variant
    Set mRights = NewDict()
    data = DB_Data("T_DROITS")
    Set rows = DB_Rows("T_DROITS", "Profil", "=", mProfile)
    For Each r In rows
        mRights(ToStr(data(r, DB_ColIdx("T_DROITS", "Module")))) = ToStr(data(r, DB_ColIdx("T_DROITS", "Droits")))
    Next r
End Sub

' right : L C M V S I
Public Function Sec_Can(ByVal moduleKey As String, ByVal right As String) As Boolean
    If mProfile = "Administrateur" Then Sec_Can = True: Exit Function
    If Len(mProfile) = 0 Then Exit Function
    If mRights Is Nothing Then LoadRights
    If Not mRights.Exists(moduleKey) Then Exit Function
    Sec_Can = (InStr(1, mRights(moduleKey), right, vbBinaryCompare) > 0)
End Function

Public Sub Sec_Require(ByVal moduleKey As String, ByVal right As String)
    If Not Sec_Can(moduleKey, right) Then
        Fail "Droits insuffisants : votre profil (" & mProfile & ") ne permet pas cette opération (" & _
             Sec_RightName(right) & ") dans le module « " & Mod_Get(moduleKey)("Titre") & " ».", ERR_RIGHTS
    End If
End Sub

Public Function Sec_RightName(ByVal right As String) As String
    Select Case right
        Case "L": Sec_RightName = "consultation"
        Case "C": Sec_RightName = "création"
        Case "M": Sec_RightName = "modification"
        Case "V": Sec_RightName = "validation"
        Case "S": Sec_RightName = "suppression"
        Case "I": Sec_RightName = "impression / export"
        Case Else: Sec_RightName = right
    End Select
End Function

Public Sub Sec_ReloadRights()
    Set mRights = Nothing
End Sub

' Reprend la session d'un utilisateur existant sans mot de passe (usage interne : fin des tests)
Public Sub Sec_Impersonate(ByVal login As String)
    Dim u As Object
    Set u = DB_GetBy("T_USERS", "Login", login)
    If u Is Nothing Then Exit Sub
    mLogin = ToStr(u("Login")): mProfile = ToStr(u("Profil")): mFullName = ToStr(u("NomComplet"))
    Set mRights = Nothing
End Sub

' Session technique utilisee par les scripts de construction et de test
Public Sub Sec_SystemSession()
    mLogin = "systeme"
    mProfile = "Administrateur"
    mFullName = "Session système"
    Set mRights = Nothing
End Sub
