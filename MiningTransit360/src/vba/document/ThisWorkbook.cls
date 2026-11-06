'==============================================================================
' MINING TRANSIT 360 - ThisWorkbook (code du document)
' Ce code est injecte dans le module ThisWorkbook par build/Build-MT360.ps1.
'==============================================================================
Option Explicit

Private Sub Workbook_Open()
    ' Demarrage differe d'une seconde : laisse Excel terminer l'ouverture du classeur
    Application.OnTime Now + TimeSerial(0, 0, 1), "App_Start"
End Sub

Private Sub Workbook_BeforeClose(Cancel As Boolean)
    App_Shutdown
End Sub

Private Sub Workbook_Activate()
    On Error Resume Next
    If Sec_IsLogged() Then App_Kiosk True
End Sub

Private Sub Workbook_Deactivate()
    On Error Resume Next
    App_Kiosk False
End Sub

Private Sub Workbook_WindowResize(ByVal Wn As Window)
    On Error Resume Next
    Dash_FitZoom
End Sub

Private Sub Workbook_SheetActivate(ByVal Sh As Object)
    On Error Resume Next
    If Sh.Name = "ACCUEIL" Then Dash_FitZoom
End Sub
