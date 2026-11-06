Attribute VB_Name = "mUI_Dialogs"
'==============================================================================
' MINING TRANSIT 360 - mUI_Dialogs
' Fenetres standard basees sur frmInput : alerte, confirmation, motif de rejet,
' selection de periode. Remplacent les MsgBox pour toutes les actions importantes.
'==============================================================================
Option Explicit

Public Function Dlg_New(ByVal title As String, Optional ByVal subtitle As String = "", _
                        Optional ByVal accent As Long = -1, Optional ByVal width As Single = 520, _
                        Optional ByVal labelWidth As Single = 170) As frmInput
    Dim f As frmInput
    Set f = New frmInput
    f.Setup title, subtitle, accent, width, labelWidth
    Set Dlg_New = f
End Function

' kind : info | success | warning | error
Public Sub UI_Alert(ByVal message As String, Optional ByVal kind As String = "info", Optional ByVal title As String = "")
    Dim f As frmInput, accent As Long
    On Error GoTo Fallback
    Select Case kind
        Case "error": accent = CLR_RED(): If Len(title) = 0 Then title = "Erreur"
        Case "warning": accent = CLR_YELLOW(): If Len(title) = 0 Then title = "Attention"
        Case "success": accent = CLR_GREEN(): If Len(title) = 0 Then title = "Opération réussie"
        Case Else: accent = CLR_BLUE(): If Len(title) = 0 Then title = "Information"
    End Select
    Set f = Dlg_New(title, "", accent, 500)
    f.AddMessage message, kind
    f.AddAction "OK", "OK", accent, True, True
    f.ShowDialog
    f.Dispose
    Exit Sub
Fallback:
    MsgBox message, vbExclamation, APP_NAME
End Sub

Public Function UI_Confirm(ByVal message As String, Optional ByVal title As String = "Confirmation", _
                           Optional ByVal okCaption As String = "Confirmer", Optional ByVal danger As Boolean = False) As Boolean
    Dim f As frmInput, c As Long
    If danger Then c = CLR_RED() Else c = CLR_BLUE()
    Set f = Dlg_New(title, "", c, 500)
    f.AddMessage message, IIf(danger, "warning", "question")
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "YES", okCaption, c, True
    UI_Confirm = (f.ShowDialog = "YES")
    f.Dispose
End Function

' Demande un motif (rejet, annulation...). Retourne "" si l'utilisateur annule.
Public Function UI_AskReason(ByVal message As String, Optional ByVal title As String = "Motif", _
                             Optional ByVal okCaption As String = "Valider") As String
    Dim f As frmInput
    Set f = Dlg_New(title, "Le motif est conservé dans l'historique", CLR_RED(), 520)
    f.AddMessage message, "warning"
    f.AddField "MOTIF", "Motif", "MEMO", "", , True
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "YES", okCaption, CLR_RED(), True
    If f.ShowDialog = "YES" Then UI_AskReason = f.FieldValue("MOTIF")
    f.Dispose
End Function

' Selection de periode (raccourcis : jour, semaine, mois en cours, mois precedent, annee)
Public Function UI_AskPeriod(ByRef d1 As Date, ByRef d2 As Date, Optional ByVal title As String = "Période") As Boolean
    Dim f As frmInput, t As Date, r As String
    t = App_Today()
    If d1 = 0 Then d1 = MonthStart(t)
    If d2 = 0 Then d2 = t
    Set f = Dlg_New(title, "Choisissez la période à traiter", CLR_TEAL(), 480)
    f.AddField "D1", "Du", "DATE", d1, , True
    f.AddField "D2", "Au", "DATE", d2, , True
    f.AddField "Q", "Raccourci", "LIST", "(dates saisies)", Array("(dates saisies)", "Aujourd'hui", "7 derniers jours", _
               "Mois en cours", "Mois précédent", "Année en cours")
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "YES", "Valider", CLR_TEAL(), True
    r = f.ShowDialog
    If r = "YES" Then
        Select Case f.FieldValue("Q")
            Case "Aujourd'hui": d1 = t: d2 = t
            Case "7 derniers jours": d1 = t - 6: d2 = t
            Case "Mois en cours": d1 = MonthStart(t): d2 = MonthEnd(t)
            Case "Mois précédent": d1 = MonthStart(MonthStart(t) - 1): d2 = MonthStart(t) - 1
            Case "Année en cours": d1 = DateSerial(Year(t), 1, 1): d2 = DateSerial(Year(t), 12, 31)
            Case Else: d1 = f.FieldValue("D1"): d2 = f.FieldValue("D2")
        End Select
        If d2 < d1 Then
            f.Dispose
            Fail "La date de fin doit être postérieure à la date de début."
        End If
        UI_AskPeriod = True
    End If
    f.Dispose
End Function

' Selection d'un mois (AAAA-MM)
Public Function UI_AskMonth(ByRef monthKeyOut As String, Optional ByVal title As String = "Mois") As Boolean
    Dim f As frmInput, i As Long, lst() As String, t As Date
    t = App_Today()
    ReDim lst(0 To 11)
    For i = 0 To 11
        lst(i) = MonthKey(DateSerial(Year(t), Month(t) - i, 1))
    Next i
    If Len(monthKeyOut) = 0 Then monthKeyOut = MonthKey(DateSerial(Year(t), Month(t) - 1, 1))
    Set f = Dlg_New(title, "Format AAAA-MM", CLR_TEAL(), 420)
    f.AddField "M", "Mois", "LIST", monthKeyOut, lst, True
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "YES", "Valider", CLR_TEAL(), True
    If f.ShowDialog = "YES" Then
        monthKeyOut = f.FieldValue("M")
        UI_AskMonth = True
    End If
    f.Dispose
End Function

' Selection d'un element dans une liste
Public Function UI_Pick(ByVal title As String, ByVal label As String, ByVal items As Variant, _
                        Optional ByVal dflt As String = "") As String
    Dim f As frmInput
    Set f = Dlg_New(title, "", CLR_BLUE(), 460)
    f.AddField "V", label, "LIST", dflt, items, True
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "YES", "Valider", CLR_BLUE(), True
    If f.ShowDialog = "YES" Then UI_Pick = f.FieldValue("V")
    f.Dispose
End Function

' Liste de resultats / anomalies dans une grille (lecture seule)
Public Sub UI_ShowGrid(ByVal title As String, ByVal subtitle As String, ByVal headers As String, _
                       ByVal widths As String, ByVal data As Variant, Optional ByVal intro As String = "", _
                       Optional ByVal kind As String = "info")
    Dim f As frmInput, w As Single, p As Variant
    For Each p In Split(widths, ";")
        w = w + Val(p)
    Next p
    w = w + 2 * PT_PAD + 20
    If w < 520 Then w = 520
    Set f = Dlg_New(title, subtitle, CLR_TEAL(), w)
    If Len(intro) > 0 Then f.AddMessage intro, kind
    f.AddField "G", "", "GRID", headers, data, False, widths, 300
    f.AddAction "OK", "Fermer", CLR_NAVY(), True, True
    f.ShowDialog
    f.Dispose
End Sub

Public Sub UI_Toast(ByVal msg As String)
    Application.StatusBar = APP_NAME & " - " & msg
End Sub
