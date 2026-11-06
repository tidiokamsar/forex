VERSION 5.00
Begin {C62A69F0-16DC-11CE-9E98-00AA00574A4F} frmLogin
   Caption         =   "MINING TRANSIT 360 - Connexion"
   ClientHeight    =   6600
   ClientWidth     =   8400
   StartUpPosition =   1  'CenterOwner
End
Attribute VB_Name = "frmLogin"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - frmLogin
' Ecran de connexion (charte de la maquette : panneau marine + accent orange).
'==============================================================================
Option Explicit

Private Const FW As Single = 430
Private Const FH As Single = 330

Private mHandlers As Collection
Private mOk As Boolean
Private mMsg As Object

Private Sub UserForm_Initialize()
    Dim demo As String
    Set mHandlers = New Collection
    Me.BackColor = CLR_WHITE()
    Me.Caption = APP_NAME & " - Connexion"
    Kit_SizeForm Me, FW, FH
    Kit_Panel Me, "bg_top", 0, 0, FW, 118, CLR_NAVY()
    Kit_Panel Me, "bg_line", 0, 118, FW, 4, CLR_ORANGE()
    Kit_Label Me, "t1", "MINING", 0, 18, FW, 30, 22, True, CLR_WHITE(), -1, 2
    Kit_Label Me, "t2", "TRANSIT 360", 0, 46, FW, 30, 22, True, CLR_ORANGE(), -1, 2
    Kit_Label Me, "t3", APP_TAGLINE(), 0, 80, FW, 16, 9, False, RGB(190, 205, 230), -1, 2
    Kit_Label Me, "t4", Company() & " - " & Param_Get("SOCIETE_SITE", ""), 0, 96, FW, 16, 8, False, RGB(150, 170, 200), -1, 2
    Kit_Label Me, "l_user", "Identifiant", 60, 140, 310, 14, 9, True, CLR_TEXT()
    Kit_TextBox Me, mHandlers, "user", 60, 156, 310, 24
    Kit_Label Me, "l_pwd", "Mot de passe", 60, 190, 310, 14, 9, True, CLR_TEXT()
    Kit_TextBox Me, mHandlers, "pwd", 60, 206, 310, 24, "", False, False, ChrW(9679)
    Set mMsg = Kit_Label(Me, "msg", "", 60, 236, 310, 28, 8.5, True, CLR_RED(), -1, 2)
    Kit_Button Me, mHandlers, "login", "Se connecter", 60, 266, 310, 32, CLR_ORANGE(), CLR_WHITE(), 11
    If Param_Bool("DEMO_MODE", False) Then demo = "Démonstration : admin / Admin@2026  -  " Else demo = ""
    Kit_Label Me, "foot", demo & "v" & APP_VERSION, 0, FH - 22, FW, 14, 7.5, False, CLR_MUTED(), -1, 2
    On Error Resume Next
    Me.Controls("txt_user").SetFocus
End Sub

Public Property Get Success() As Boolean
    Success = mOk
End Property

Public Sub OnUIEvent(ByVal key As String, ByVal evt As String)
    If (key = "login" And evt = "Click") Or (evt = "Enter" And (key = "pwd" Or key = "user")) Then
        If key = "user" And Len(Me.Controls("txt_pwd").Text) = 0 Then
            Me.Controls("txt_pwd").SetFocus
            Exit Sub
        End If
        TryLogin
    End If
End Sub

Private Sub TryLogin()
    Dim res As String
    On Error GoTo EH
    mMsg.Caption = ""
    If Len(Trim$(Me.Controls("txt_user").Text)) = 0 Then mMsg.Caption = "Saisissez votre identifiant.": Exit Sub
    res = Sec_Login(Me.Controls("txt_user").Text, Me.Controls("txt_pwd").Text)
    If Len(res) > 0 Then
        mMsg.Caption = res
        Me.Controls("txt_pwd").Text = ""
        Me.Controls("txt_pwd").SetFocus
    Else
        mOk = True
        Me.Hide
    End If
    Exit Sub
EH:
    mMsg.Caption = "Erreur : " & Err.Description
End Sub

Private Sub UserForm_QueryClose(Cancel As Integer, CloseMode As Integer)
    If CloseMode = 0 Then
        Cancel = True
        Me.Hide
    End If
End Sub

Private Sub UserForm_Terminate()
    Kit_ReleaseAll mHandlers
    Set mHandlers = Nothing
End Sub
