VERSION 5.00
Begin {C62A69F0-16DC-11CE-9E98-00AA00574A4F} frmReports
   Caption         =   "MINING TRANSIT 360 - Centre de rapports"
   ClientHeight    =   11400
   ClientWidth     =   18600
   StartUpPosition =   1  'CenterOwner
End
Attribute VB_Name = "frmReports"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
'==============================================================================
' MINING TRANSIT 360 - frmReports
' Centre de rapports : categories, liste des etats, parametres (periode,
' filtre), sorties (consulter, apercu, imprimer, PDF, Excel).
'==============================================================================
Option Explicit

Private Const FW As Single = 930
Private Const FH As Single = 570

Private mHandlers As Collection
Private mCat As String
Private mKeys As Collection
Private mList As Object
Private mDesc As Object
Private mFilter As Object
Private mFilterLbl As Object
Private mStatus As Object

Private Sub UserForm_Initialize()
    Set mHandlers = New Collection
    Me.BackColor = CLR_BG()
End Sub

Public Sub Init()
    Dim cats As Variant, i As Long, colors As Variant, y As Single
    Kit_SizeForm Me, FW, FH
    Me.Caption = APP_NAME & " - Rapports et états"
    Kit_Header Me, "Centre de rapports", "Production, location, carburant, pneumatiques, finances, direction", CLR_TEAL(), FW
    cats = Array("Production", "Location", "Carburant", "Pneumatiques", "Finances", "Direction", "Fiches")
    colors = Array(CLR_GREEN(), CLR_ORANGE(), CLR_BLUE(), CLR_PURPLE(), RGB(15, 123, 108), CLR_NAVY2(), CLR_TEAL())
    y = 66
    Kit_Label Me, "l_cat", "CATÉGORIES", PT_PAD, y, 180, 14, 8, True, CLR_MUTED()
    For i = 0 To UBound(cats)
        Kit_Button Me, mHandlers, "cat_" & cats(i), CStr(cats(i)), PT_PAD, y + 18 + i * 40, 180, 34, colors(i), CLR_WHITE(), 10
    Next i
    ' liste des etats
    Kit_Label Me, "l_list", "ÉTATS DISPONIBLES", 210, y, 380, 14, 8, True, CLR_MUTED()
    Set mList = Kit_ListBox(Me, mHandlers, "list", 210, y + 18, 380, FH - y - 70, 1, "370 pt")
    mList.Font.size = 10
    ' parametres / sorties
    Kit_Panel Me, "pnl", 606, y + 18, FW - 606 - PT_PAD, FH - y - 70, CLR_WHITE(), CLR_LINE()
    Set mDesc = Kit_Label(Me, "desc", "Sélectionnez une catégorie puis un état.", 618, y + 28, FW - 640, 60, 9.5, False, CLR_TEXT())
    Kit_Label Me, "l_d1", "Du", 618, y + 100, 30, 14, 9, True, CLR_TEXT()
    Kit_TextBox Me, mHandlers, "d1", 650, y + 97, 90, 20, Format$(MonthStart(App_Today()), "dd/mm/yyyy")
    Kit_Label Me, "l_d2", "au", 748, y + 100, 20, 14, 9, True, CLR_TEXT()
    Kit_TextBox Me, mHandlers, "d2", 770, y + 97, 90, 20, Format$(App_Today(), "dd/mm/yyyy")
    Kit_Button Me, mHandlers, "q_day", "Jour", 618, y + 124, 52, 20, CLR_GREY(), CLR_WHITE(), 8
    Kit_Button Me, mHandlers, "q_week", "7 jours", 674, y + 124, 56, 20, CLR_GREY(), CLR_WHITE(), 8
    Kit_Button Me, mHandlers, "q_month", "Mois", 734, y + 124, 52, 20, CLR_GREY(), CLR_WHITE(), 8
    Kit_Button Me, mHandlers, "q_prev", "Mois préc.", 790, y + 124, 66, 20, CLR_GREY(), CLR_WHITE(), 8
    Kit_Button Me, mHandlers, "q_year", "Année", 618, y + 148, 52, 20, CLR_GREY(), CLR_WHITE(), 8
    Set mFilterLbl = Kit_Label(Me, "l_f", "Filtre", 618, y + 180, 240, 14, 9, True, CLR_TEXT())
    Set mFilter = Kit_Combo(Me, mHandlers, "filter", 618, y + 196, 240, 20, Array(), "", True)
    Kit_Button Me, mHandlers, "o_SCREEN", "Consulter à l'écran", 618, y + 236, 240, 30, CLR_NAVY2()
    Kit_Button Me, mHandlers, "o_PREVIEW", "Aperçu (PDF)", 618, y + 272, 118, 30, CLR_TEAL()
    Kit_Button Me, mHandlers, "o_PRINT", "Imprimer", 740, y + 272, 118, 30, CLR_BLUE()
    Kit_Button Me, mHandlers, "o_PDF", "Exporter PDF", 618, y + 308, 118, 30, CLR_RED()
    Kit_Button Me, mHandlers, "o_XLSX", "Exporter Excel", 740, y + 308, 118, 30, CLR_GREEN()
    Set mStatus = Kit_Label(Me, "status", "", 618, y + 348, 240, 40, 8.5, False, CLR_MUTED())
    Kit_Button Me, mHandlers, "close", "Fermer", FW - PT_PAD - 110, FH - 40, 110, 28, CLR_NAVY()
    ShowCategory "Production"
End Sub

Private Sub ShowCategory(ByVal cat As String)
    Dim it As Variant
    mCat = cat
    Set mKeys = New Collection
    mList.Clear
    For Each it In Rpt_Catalog()
        If it(1) = cat Then
            mList.AddItem it(2)
            mKeys.Add it(0)
        End If
    Next it
    If mList.ListCount > 0 Then mList.ListIndex = 0
    ShowInfo
End Sub

Private Function CurrentKey() As String
    If mList.ListIndex >= 0 Then CurrentKey = mKeys(mList.ListIndex + 1)
End Function

Private Sub ShowInfo()
    Dim info As Variant, k As String, items As Variant, i As Long
    k = CurrentKey()
    If Len(k) = 0 Then Exit Sub
    info = Rpt_Info(k)
    mDesc.Caption = info(2) & vbLf & info(5) & IIf(info(3), "", vbLf & "(état à date : la période n'est pas utilisée)")
    mFilter.Clear
    If Len(info(4)) > 0 Then
        mFilterLbl.Caption = Choose(InStr("TRUCKCLIENTDRIVER", info(4)) \ 5 + 1, "Camion", "Client", "Chauffeur")
        items = Rpt_FilterChoices(info(4), k <> "CARD_TRUCK" And k <> "CARD_DRIVER" And k <> "CLIENT_SIT")
        For i = LBound(items) To UBound(items)
            mFilter.AddItem items(i)
        Next i
        If mFilter.ListCount > 0 Then mFilter.ListIndex = 0
        mFilter.Enabled = True
    Else
        mFilterLbl.Caption = "Filtre (non applicable)"
        mFilter.Enabled = False
    End If
End Sub

Private Sub SetPeriod(ByVal d1 As Date, ByVal d2 As Date)
    Me.Controls("txt_d1").Text = Format$(d1, "dd/mm/yyyy")
    Me.Controls("txt_d2").Text = Format$(d2, "dd/mm/yyyy")
End Sub

Public Sub OnUIEvent(ByVal key As String, ByVal evt As String)
    Dim t As Date
    On Error GoTo EH
    t = App_Today()
    If evt <> "Click" And evt <> "DblClick" Then Exit Sub
    Select Case True
        Case key = "close": Me.Hide
        Case Left$(key, 4) = "cat_": ShowCategory Mid$(key, 5)
        Case key = "list" And evt = "Click": ShowInfo
        Case key = "list" And evt = "DblClick": RunOutput "PREVIEW"
        Case key = "q_day": SetPeriod t, t
        Case key = "q_week": SetPeriod t - 6, t
        Case key = "q_month": SetPeriod MonthStart(t), MonthEnd(t)
        Case key = "q_prev": SetPeriod MonthStart(MonthStart(t) - 1), MonthStart(t) - 1
        Case key = "q_year": SetPeriod DateSerial(Year(t), 1, 1), DateSerial(Year(t), 12, 31)
        Case Left$(key, 2) = "o_": RunOutput Mid$(key, 3)
    End Select
    Exit Sub
EH:
    mStatus.Caption = Err.Description
    Log_Error "Centre de rapports", Err.Number, Err.Description
End Sub

Private Sub RunOutput(ByVal mode As String)
    Dim k As String, info As Variant, d1 As Date, d2 As Date, fid As Long, rep As cReport
    k = CurrentKey()
    If Len(k) = 0 Then Fail "Sélectionnez un état."
    Sec_Require "RAPPORTS", "I"
    info = Rpt_Info(k)
    d1 = ToDate(Me.Controls("txt_d1").Text): d2 = ToDate(Me.Controls("txt_d2").Text)
    If info(3) Then
        If d1 = 0 Or d2 = 0 Then Fail "Période invalide (jj/mm/aaaa)."
        If d2 < d1 Then Fail "La date de fin précède la date de début."
    End If
    If Len(info(4)) > 0 Then
        fid = Rpt_FilterId(info(4), ToStr(mFilter.Text))
        If fid = 0 And (k = "CARD_TRUCK" Or k = "CARD_DRIVER" Or k = "CLIENT_SIT") Then Fail "Choisissez un élément dans le filtre."
    End If
    mStatus.Caption = "Préparation de « " & info(2) & " »..."
    DoEvents
    Set rep = Rpt_Build(k, d1, d2, fid)
    If mode = "SCREEN" Then
        Me.Hide
        Rpt_Output rep, "SCREEN"
        Exit Sub
    End If
    Rpt_Output rep, mode
    mStatus.Caption = "« " & info(2) & " » : sortie " & mode & " effectuée."
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
