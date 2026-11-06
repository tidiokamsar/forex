VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cCtlEvents"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - cCtlEvents
' Relais d'evenements pour les controles crees dynamiquement dans les
' UserForms. Chaque evenement est renvoye a la fenetre hote via
' Host.OnUIEvent(Key, NomEvenement).
'==============================================================================
Option Explicit

Public WithEvents Btn As MSForms.CommandButton
Attribute Btn.VB_VarHelpID = -1
Public WithEvents Lbl As MSForms.Label
Attribute Lbl.VB_VarHelpID = -1
Public WithEvents Lst As MSForms.ListBox
Attribute Lst.VB_VarHelpID = -1
Public WithEvents Txt As MSForms.TextBox
Attribute Txt.VB_VarHelpID = -1
Public WithEvents Cbo As MSForms.ComboBox
Attribute Cbo.VB_VarHelpID = -1
Public WithEvents Chk As MSForms.CheckBox
Attribute Chk.VB_VarHelpID = -1

Public Key As String
Private mHost As Object
Private mBaseColor As Long

Public Sub Bind(ByVal host As Object, ByVal ctlKey As String, ByVal ctl As Object)
    Set mHost = host
    Key = ctlKey
    Select Case TypeName(ctl)
        Case "CommandButton": Set Btn = ctl: mBaseColor = ctl.BackColor
        Case "Label": Set Lbl = ctl
        Case "ListBox": Set Lst = ctl
        Case "TextBox": Set Txt = ctl
        Case "ComboBox": Set Cbo = ctl
        Case "CheckBox": Set Chk = ctl
    End Select
End Sub

Public Sub Unbind()
    Set mHost = Nothing
    Set Btn = Nothing: Set Lbl = Nothing: Set Lst = Nothing
    Set Txt = Nothing: Set Cbo = Nothing: Set Chk = Nothing
End Sub

Private Sub Fire(ByVal evt As String)
    If mHost Is Nothing Then Exit Sub
    mHost.OnUIEvent Key, evt
End Sub

Private Sub Btn_Click()
    Fire "Click"
End Sub

Private Sub Lbl_Click()
    Fire "Click"
End Sub

Private Sub Lst_Click()
    Fire "Click"
End Sub

Private Sub Lst_DblClick(ByVal Cancel As MSForms.ReturnBoolean)
    Fire "DblClick"
End Sub

Private Sub Txt_Change()
    Fire "Change"
End Sub

Private Sub Txt_KeyDown(ByVal KeyCode As MSForms.ReturnInteger, ByVal Shift As Integer)
    If KeyCode = 13 Then Fire "Enter"
End Sub

Private Sub Cbo_Change()
    Fire "Change"
End Sub

Private Sub Chk_Click()
    Fire "Change"
End Sub
