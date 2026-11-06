Attribute VB_Name = "mBiz_Tires"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Tires
' Pneumatiques (actifs physiques identifies par n° de serie) et stock de
' consommables (prix moyen pondere).
'
' Cycle de vie d'un pneu :
'   Entree (Stock) -> Montage (Monte) -> Demontage (Stock ou Reparation)
'   -> Transfert (Monte sur un autre camion) -> Reparation -> Retour reparation
'   -> Reforme (fin de vie). Chaque mouvement est historise dans T_PNEU_MVT.
' Charge pneumatique = achats (Entree) + reparations de la periode.
'==============================================================================
Option Explicit

Private mHoursCache As Object

Public Sub Tire_BeforeSave(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    rec("NumSerie") = UCase$(Trim$(ToStr(rec("NumSerie"))))
    If ToDbl(rec("PrixAchat")) <= 0 Then Fail "Prix d'achat obligatoire."
    If isNew Then
        rec("Statut") = PN_STOCK
        rec("CamionID") = Empty: rec("Position") = "": rec("DateMontage") = Empty
        If IsBlank(rec("HeuresCumulees")) Then rec("HeuresCumulees") = 0
    End If
End Sub

Public Sub Tire_LogEntry(ByVal tireId As Long)
    Dim t As Object
    Set t = DB_Get("T_PNEUS", tireId)
    DB_Insert "T_PNEU_MVT", NewRecord("DateMvt", t("DateEntree"), "PneuID", tireId, "TypeMvt", "Entrée", _
                                       "Cout", t("PrixAchat"), "Motif", "Entrée en stock (achat)")
End Sub

Private Sub AddMove(ByVal tireId As Long, ByVal d As Date, ByVal typ As String, ByVal cam As Variant, ByVal pos As String, _
                    ByVal camDest As Variant, ByVal posDest As String, ByVal cost As Double, ByVal reason As String, Optional ByVal counter As Variant)
    Dim rec As Object
    Set rec = NewRecord("DateMvt", d, "PneuID", tireId, "TypeMvt", typ, "Position", pos, "PositionDest", posDest, _
                        "Cout", cost, "Motif", reason)
    If ToLng(cam) > 0 Then rec("CamionID") = ToLng(cam)
    If ToLng(camDest) > 0 Then rec("CamionDestID") = ToLng(camDest)
    If Not IsMissing(counter) Then If Not IsBlank(counter) Then rec("CompteurH") = counter
    DB_Insert "T_PNEU_MVT", rec
End Sub

' Positions standard (essieux) et occupation
Public Function Tire_Positions(ByVal cam As Long) As Variant
    Dim n As Long
    n = ToLng(DB_Value("T_CAMIONS", cam, "NbPositionsPneus"))
    If n <= 6 Then
        Tire_Positions = Array("AVG", "AVD", "AR1-GE", "AR1-GI", "AR1-DI", "AR1-DE")
    Else
        Tire_Positions = Array("AVG", "AVD", "AR1-GE", "AR1-GI", "AR1-DI", "AR1-DE", "AR2-GE", "AR2-GI", "AR2-DI", "AR2-DE", "SEC-1", "SEC-2")
    End If
End Function

Public Function Tire_AtPosition(ByVal cam As Long, ByVal pos As String) As Long
    Dim r As Long, rows As Collection
    Set rows = DB_Rows("T_PNEUS", "CamionID", "=", cam, "Position", "=", pos, "Statut", "=", PN_MONTE)
    If rows.Count > 0 Then Tire_AtPosition = ToLng(DB_Data("T_PNEUS")(rows(1), DB_ColIdx("T_PNEUS", "ID")))
End Function

' Heures de service d'un camion entre deux dates (releves de location, heures brutes - immobilisations)
Public Function Tire_TruckHours(ByVal cam As Long, ByVal d1 As Date, ByVal d2 As Date) As Double
    Dim k As String
    If cam = 0 Or d1 = 0 Then Exit Function
    k = cam & "|" & CLng(d1) & "|" & CLng(d2)
    If Not mHoursCache Is Nothing Then
        If mHoursCache.Exists(k) Then Tire_TruckHours = mHoursCache(k): Exit Function
    End If
    Tire_TruckHours = DB_Sum("T_LOCATION", "HeuresBrutes", "CamionID", "=", cam, "DateReleve", ">=", d1, "DateReleve", "<=", d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE) _
                    - DB_Sum("T_LOCATION", "HeuresImmobAtiko", "CamionID", "=", cam, "DateReleve", ">=", d1, "DateReleve", "<=", d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE) _
                    - DB_Sum("T_LOCATION", "HeuresImmobExploitant", "CamionID", "=", cam, "DateReleve", ">=", d1, "DateReleve", "<=", d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)
    If Not mHoursCache Is Nothing Then mHoursCache(k) = Tire_TruckHours
End Function

' Heures cumulees "en direct" = heures historisees + heures depuis le dernier montage
Public Function Tire_LiveHours(ByVal t As Object) As Double
    Tire_LiveHours = ToDbl(t("HeuresCumulees"))
    If ToStr(t("Statut")) = PN_MONTE Then
        Tire_LiveHours = Tire_LiveHours + Tire_TruckHours(ToLng(t("CamionID")), ToDate(t("DateMontage")) + 1, App_Today())
    End If
End Function

Private Function TireOrFail(ByVal tireId As Long) As Object
    Set TireOrFail = DB_Get("T_PNEUS", tireId)
    If TireOrFail Is Nothing Then Fail "Pneu introuvable."
End Function

Public Sub Tire_Mount(ByVal tireId As Long, ByVal cam As Long, ByVal pos As String, ByVal d As Date, Optional ByVal counter As Variant)
    Dim t As Object, other As Long
    Sec_Require "PNEUS", "M"
    Set t = TireOrFail(tireId)
    If t("Statut") <> PN_STOCK Then Fail "Seul un pneu en stock peut être monté (statut actuel : " & t("Statut") & ")."
    If cam = 0 Or Len(pos) = 0 Then Fail "Camion et position obligatoires."
    If ToStr(DB_Value("T_CAMIONS", cam, "Actif")) = "Non" Then Fail "Camion désactivé."
    If d < ToDate(t("DateEntree")) Then Fail "Date de montage antérieure à l'entrée en stock."
    other = Tire_AtPosition(cam, pos)
    If other > 0 Then Fail "La position " & pos & " du camion " & CodeOf("T_CAMIONS", cam) & " est occupée par le pneu " & CodeOf("T_PNEUS", other) & _
                          ". Démontez-le ou transférez-le d'abord."
    DB_BeginTx "T_PNEUS|T_PNEU_MVT|T_AUDIT|T_SEQ"
    DB_Update "T_PNEUS", tireId, NewRecord("Statut", PN_MONTE, "CamionID", cam, "Position", pos, "DateMontage", d)
    AddMove tireId, d, "Montage", cam, pos, Empty, "", 0, "Montage", counter
    Log_Audit "PNEU-MONTAGE", "T_PNEUS", tireId, CodeOf("T_PNEUS", tireId) & " sur " & CodeOf("T_CAMIONS", cam) & " / " & pos
    DB_Commit
End Sub

' destination : "Stock" ou "Réparation"
Public Sub Tire_Unmount(ByVal tireId As Long, ByVal d As Date, ByVal destination As String, ByVal reason As String, _
                        Optional ByVal depth As Variant, Optional ByVal repairCost As Double = 0)
    Dim t As Object, hrs As Double, upd As Object
    Sec_Require "PNEUS", "M"
    Set t = TireOrFail(tireId)
    If t("Statut") <> PN_MONTE Then Fail "Ce pneu n'est pas monté."
    If d < ToDate(t("DateMontage")) Then Fail "Date de démontage antérieure au montage."
    If Len(Trim$(reason)) = 0 Then Fail "Motif de démontage obligatoire."
    hrs = Tire_TruckHours(ToLng(t("CamionID")), ToDate(t("DateMontage")) + 1, d)
    DB_BeginTx "T_PNEUS|T_PNEU_MVT|T_AUDIT|T_SEQ"
    AddMove tireId, d, "Démontage", t("CamionID"), ToStr(t("Position")), Empty, "", 0, reason
    Set upd = NewRecord("CamionID", Empty, "Position", "", "HeuresCumulees", Round(ToDbl(t("HeuresCumulees")) + hrs, 0))
    If Not IsMissing(depth) Then If Not IsBlank(depth) Then upd("ProfondeurMM") = ToDbl(depth)
    If destination = PN_REPAR Then
        upd("Statut") = PN_REPAR
        AddMove tireId, d, "Réparation", Empty, "", Empty, "", repairCost, "Envoi en réparation : " & reason
    Else
        upd("Statut") = PN_STOCK
    End If
    DB_Update "T_PNEUS", tireId, upd
    Log_Audit "PNEU-DEMONTAGE", "T_PNEUS", tireId, CodeOf("T_PNEUS", tireId) & " (" & FmtNum(hrs, 0) & " h depuis montage) -> " & destination
    DB_Commit
End Sub

Public Sub Tire_Transfer(ByVal tireId As Long, ByVal camDest As Long, ByVal posDest As String, ByVal d As Date, ByVal reason As String)
    Dim t As Object, hrs As Double, other As Long
    Sec_Require "PNEUS", "M"
    Set t = TireOrFail(tireId)
    If t("Statut") <> PN_MONTE Then Fail "Seul un pneu monté peut être transféré."
    other = Tire_AtPosition(camDest, posDest)
    If other > 0 And other <> tireId Then Fail "Position de destination occupée par " & CodeOf("T_PNEUS", other) & "."
    If ToLng(t("CamionID")) = camDest And ToStr(t("Position")) = posDest Then Fail "Destination identique à la position actuelle."
    hrs = Tire_TruckHours(ToLng(t("CamionID")), ToDate(t("DateMontage")) + 1, d)
    DB_BeginTx "T_PNEUS|T_PNEU_MVT|T_AUDIT|T_SEQ"
    AddMove tireId, d, "Transfert", t("CamionID"), ToStr(t("Position")), camDest, posDest, 0, reason
    DB_Update "T_PNEUS", tireId, NewRecord("CamionID", camDest, "Position", posDest, "DateMontage", d, _
                                           "HeuresCumulees", Round(ToDbl(t("HeuresCumulees")) + hrs, 0))
    Log_Audit "PNEU-TRANSFERT", "T_PNEUS", tireId, CodeOf("T_PNEUS", tireId) & " -> " & CodeOf("T_CAMIONS", camDest) & " / " & posDest
    DB_Commit
End Sub

Public Sub Tire_Repair(ByVal tireId As Long, ByVal d As Date, ByVal cost As Double, ByVal reason As String)
    Dim t As Object
    Sec_Require "PNEUS", "M"
    Set t = TireOrFail(tireId)
    If t("Statut") = PN_MONTE Then Fail "Démontez le pneu avant de l'envoyer en réparation (option « vers réparation » du démontage)."
    If t("Statut") = PN_REFORME Then Fail "Pneu réformé."
    If t("Statut") = PN_REPAR Then Fail "Pneu déjà en réparation."
    DB_BeginTx "T_PNEUS|T_PNEU_MVT|T_AUDIT|T_SEQ"
    AddMove tireId, d, "Réparation", Empty, "", Empty, "", cost, reason
    DB_SetValue "T_PNEUS", tireId, "Statut", PN_REPAR
    Log_Audit "PNEU-REPARATION", "T_PNEUS", tireId, reason & " - " & FmtMoney(cost)
    DB_Commit
End Sub

Public Sub Tire_ReturnFromRepair(ByVal tireId As Long, ByVal d As Date, ByVal extraCost As Double, ByVal depth As Variant)
    Dim t As Object
    Sec_Require "PNEUS", "M"
    Set t = TireOrFail(tireId)
    If t("Statut") <> PN_REPAR Then Fail "Ce pneu n'est pas en réparation."
    DB_BeginTx "T_PNEUS|T_PNEU_MVT|T_AUDIT|T_SEQ"
    AddMove tireId, d, "Retour réparation", Empty, "", Empty, "", extraCost, "Retour de réparation"
    DB_SetValue "T_PNEUS", tireId, "Statut", PN_STOCK
    If Not IsBlank(depth) Then DB_SetValue "T_PNEUS", tireId, "ProfondeurMM", ToDbl(depth)
    Log_Audit "PNEU-RETOUR", "T_PNEUS", tireId, "Retour en stock"
    DB_Commit
End Sub

Public Sub Tire_Scrap(ByVal tireId As Long, ByVal d As Date, ByVal reason As String)
    Dim t As Object, hrs As Double
    Sec_Require "PNEUS", "V"
    Set t = TireOrFail(tireId)
    If t("Statut") = PN_REFORME Then Fail "Pneu déjà réformé."
    If Len(Trim$(reason)) = 0 Then Fail "Motif de réforme obligatoire."
    DB_BeginTx "T_PNEUS|T_PNEU_MVT|T_AUDIT|T_SEQ"
    If t("Statut") = PN_MONTE Then
        hrs = Tire_TruckHours(ToLng(t("CamionID")), ToDate(t("DateMontage")) + 1, d)
        AddMove tireId, d, "Démontage", t("CamionID"), ToStr(t("Position")), Empty, "", 0, "Démontage pour réforme"
        DB_Update "T_PNEUS", tireId, NewRecord("HeuresCumulees", Round(ToDbl(t("HeuresCumulees")) + hrs, 0))
    End If
    AddMove tireId, d, "Réforme", Empty, "", Empty, "", 0, reason
    DB_Update "T_PNEUS", tireId, NewRecord("Statut", PN_REFORME, "CamionID", Empty, "Position", "", "Observations", reason)
    Log_Audit "PNEU-REFORME", "T_PNEUS", tireId, reason
    DB_Commit
End Sub

' Pneus a remplacer : profondeur < seuil ou heures > 90 % de la duree de vie cible
Public Function Tire_ToReplace() As Collection
    Dim rows As Collection, r As Variant, t As Object, res As New Collection, minD As Double, life As Double
    minD = Param_Num("PNEU_PROFONDEUR_MIN", 6)
    Set mHoursCache = NewDict()
    Set rows = DB_Rows("T_PNEUS", "Statut", "=", PN_MONTE)
    For Each r In rows
        Set t = DB_RowDict("T_PNEUS", r)
        life = ToDbl(t("DureeVieCibleH"))
        If (Not IsBlank(t("ProfondeurMM")) And ToDbl(t("ProfondeurMM")) < minD) Or (life > 0 And Tire_LiveHours(t) > 0.9 * life) Then res.Add t
    Next r
    Set mHoursCache = Nothing
    Set Tire_ToReplace = res
End Function

Public Function Tire_Cost(ByVal d1 As Date, ByVal d2 As Date, Optional ByVal truckId As Long = 0) As Double
    Tire_Cost = DB_Sum("T_PNEU_MVT", "Cout", "TypeMvt", "IN", "Entrée|Réparation|Retour réparation", "DateMvt", ">=", d1, "DateMvt", "<=", d2)
    If truckId > 0 Then Tire_Cost = DB_Sum("T_PNEU_MVT", "Cout", "TypeMvt", "IN", "Réparation|Retour réparation", "DateMvt", ">=", d1, "DateMvt", "<=", d2, "CamionID", "=", truckId)
End Function

'------------------------------------------------------------------ Stock consommables
Public Sub Stock_BeforeSaveArticle(ByVal rec As Object, ByVal isNew As Boolean)
    rec("Code") = UCase$(Trim$(ToStr(rec("Code"))))
    If isNew Then rec("Stock") = 0: rec("PrixMoyen") = 0
End Sub

Public Sub Stock_BeforeSaveMove(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    Dim stock As Double, art As Long, q As Double
    art = ToLng(rec("ArticleID"))
    q = ToDbl(rec("Quantite"))
    If q <= 0 Then Fail "Quantité strictement positive obligatoire."
    If ToStr(rec("TypeMvt")) = "Entrée" And ToDbl(rec("PU")) <= 0 Then Fail "Prix unitaire obligatoire pour une entrée."
    If ToStr(rec("TypeMvt")) = "Sortie" Then
        stock = ToDbl(DB_Value("T_ARTICLES", art, "Stock"))
        If Not old Is Nothing Then
            If ToStr(old("TypeMvt")) = "Sortie" Then stock = stock + ToDbl(old("Quantite")) Else stock = stock - ToDbl(old("Quantite"))
        End If
        If q > stock + 0.0001 Then Fail "Stock insuffisant : " & FmtNum(stock, 2) & " " & DB_Value("T_ARTICLES", art, "Unite") & " disponible(s)."
        If IsBlank(rec("PU")) Or ToDbl(rec("PU")) = 0 Then rec("PU") = ToDbl(DB_Value("T_ARTICLES", art, "PrixMoyen"))
    End If
    rec("Montant") = Round(q * ToDbl(rec("PU")), 0)
End Sub

' Recalcule stock et prix moyen pondere d'un article a partir de ses mouvements
Public Sub Stock_Recompute(ByVal art As Long)
    Dim rows As Collection, r As Variant, data As Variant, arr() As Variant, n As Long, i As Long
    Dim stock As Double, pmp As Double, q As Double, pu As Double, typ As String
    If art = 0 Then Exit Sub
    Set rows = DB_Rows("T_STOCK_MVT", "ArticleID", "=", art)
    data = DB_Data("T_STOCK_MVT")
    If rows.Count > 0 Then
        ReDim arr(1 To rows.Count, 1 To 4)
        For Each r In rows
            n = n + 1
            arr(n, 1) = ToDate(data(r, DB_ColIdx("T_STOCK_MVT", "DateMvt"))) + ToLng(data(r, DB_ColIdx("T_STOCK_MVT", "ID"))) / 1000000#
            arr(n, 2) = ToStr(data(r, DB_ColIdx("T_STOCK_MVT", "TypeMvt")))
            arr(n, 3) = ToDbl(data(r, DB_ColIdx("T_STOCK_MVT", "Quantite")))
            arr(n, 4) = ToDbl(data(r, DB_ColIdx("T_STOCK_MVT", "PU")))
        Next r
        SortArray2D arr, 1
        For i = 1 To n
            typ = arr(i, 2): q = arr(i, 3): pu = arr(i, 4)
            Select Case typ
                Case "Entrée"
                    If stock + q > 0 Then pmp = (stock * pmp + q * pu) / (stock + q)
                    stock = stock + q
                Case "Sortie"
                    stock = stock - q
                Case "Ajustement"
                    stock = q
            End Select
        Next i
    End If
    DB_Update "T_ARTICLES", art, NewRecord("Stock", Round(stock, 3), "PrixMoyen", Round(pmp, 0)), "ID", False
End Sub

Public Function Stock_ConsumptionCost(ByVal d1 As Date, ByVal d2 As Date) As Double
    Stock_ConsumptionCost = DB_Sum("T_STOCK_MVT", "Montant", "TypeMvt", "=", "Sortie", "DateMvt", ">=", d1, "DateMvt", "<=", d2)
End Function

Public Function Stock_BelowMin() As Collection
    Dim data As Variant, r As Long, res As New Collection
    data = DB_Data("T_ARTICLES")
    For r = 1 To ArrayRows(data)
        If ToStr(data(r, DB_ColIdx("T_ARTICLES", "Actif"))) <> "Non" Then
            If ToDbl(data(r, DB_ColIdx("T_ARTICLES", "Stock"))) < ToDbl(data(r, DB_ColIdx("T_ARTICLES", "StockMin"))) Then res.Add DB_RowDict("T_ARTICLES", r)
        End If
    Next r
    Set Stock_BelowMin = res
End Function

'------------------------------------------------------------------ Fenetres d'action pneus
Public Sub Tire_ActionDialog(ByVal action As String, ByVal tireId As Long)
    Dim f As frmInput, t As Object, r As String, trucks As Variant, cam As Long
    Set t = TireOrFail(tireId)
    trucks = Schema_FKChoices(Schema_Field("T_ROTATIONS", "CamionID"))
    Select Case action
        Case "TIRE_MOUNT"
            Set f = Dlg_New("Montage du pneu " & t("NumSerie"), t("Marque") & " " & t("Dimension") & " - statut " & t("Statut"), CLR_PURPLE())
            f.AddField "D", "Date de montage", "DATE", App_Today(), , True
            f.AddField "CAM", "Camion", "LIST", "", trucks, True
            f.AddField "POS", "Position", "COMBO", "", Tire_Positions(1), True, "AVG/AVD = avant ; AR1/AR2 = essieux arrière ; GE/GI/DI/DE = gauche/droite ext./int."
            f.AddField "CPT", "Compteur moteur (h)", "NUM", ""
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Monter", CLR_PURPLE(), True
            r = f.ShowDialog
            If r = "OK" Then Tire_Mount tireId, IdOf("T_CAMIONS", f.FieldValue("CAM")), UCase$(f.FieldValue("POS")), f.FieldValue("D"), f.FieldValue("CPT")
        Case "TIRE_UNMOUNT"
            Set f = Dlg_New("Démontage du pneu " & t("NumSerie"), "Monté sur " & CodeOf("T_CAMIONS", t("CamionID")) & " / " & t("Position"), CLR_PURPLE())
            f.AddField "D", "Date", "DATE", App_Today(), , True
            f.AddField "DEST", "Destination", "LIST", PN_STOCK, Array(PN_STOCK, PN_REPAR), True
            f.AddField "MOT", "Motif", "TEXT", "", , True
            f.AddField "PROF", "Profondeur mesurée (mm)", "NUM", ToStr(t("ProfondeurMM"))
            f.AddField "COUT", "Coût réparation (GNF)", "MONEY", "0"
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Démonter", CLR_PURPLE(), True
            r = f.ShowDialog
            If r = "OK" Then Tire_Unmount tireId, f.FieldValue("D"), f.FieldValue("DEST"), f.FieldValue("MOT"), f.FieldValue("PROF"), ToDbl(f.FieldValue("COUT"))
        Case "TIRE_TRANSFER"
            Set f = Dlg_New("Transfert du pneu " & t("NumSerie"), "Actuellement sur " & CodeOf("T_CAMIONS", t("CamionID")) & " / " & t("Position"), CLR_PURPLE())
            f.AddField "D", "Date", "DATE", App_Today(), , True
            f.AddField "CAM", "Camion destination", "LIST", "", trucks, True
            f.AddField "POS", "Position destination", "COMBO", "", Tire_Positions(1), True
            f.AddField "MOT", "Motif", "TEXT", "", , True
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Transférer", CLR_PURPLE(), True
            r = f.ShowDialog
            If r = "OK" Then Tire_Transfer tireId, IdOf("T_CAMIONS", f.FieldValue("CAM")), UCase$(f.FieldValue("POS")), f.FieldValue("D"), f.FieldValue("MOT")
        Case "TIRE_REPAIR"
            Set f = Dlg_New("Réparation du pneu " & t("NumSerie"), "Statut actuel : " & t("Statut"), CLR_PURPLE())
            f.AddField "D", "Date", "DATE", App_Today(), , True
            f.AddField "COUT", "Coût (GNF)", "MONEY", "0", , True
            f.AddField "MOT", "Nature de la réparation", "TEXT", "", , True
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Envoyer en réparation", CLR_PURPLE(), True
            r = f.ShowDialog
            If r = "OK" Then Tire_Repair tireId, f.FieldValue("D"), ToDbl(f.FieldValue("COUT")), f.FieldValue("MOT")
        Case "TIRE_RETURN"
            Set f = Dlg_New("Retour de réparation " & t("NumSerie"), "", CLR_PURPLE())
            f.AddField "D", "Date", "DATE", App_Today(), , True
            f.AddField "COUT", "Coût complémentaire (GNF)", "MONEY", "0"
            f.AddField "PROF", "Profondeur (mm)", "NUM", ToStr(t("ProfondeurMM"))
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Remettre en stock", CLR_PURPLE(), True
            r = f.ShowDialog
            If r = "OK" Then Tire_ReturnFromRepair tireId, f.FieldValue("D"), ToDbl(f.FieldValue("COUT")), f.FieldValue("PROF")
        Case "TIRE_SCRAP"
            Set f = Dlg_New("Réforme du pneu " & t("NumSerie"), "Opération définitive", CLR_RED())
            f.AddMessage "La réforme sort définitivement le pneu du parc. Un pneu monté est démonté automatiquement.", "warning"
            f.AddField "D", "Date", "DATE", App_Today(), , True
            f.AddField "MOT", "Motif de réforme", "TEXT", "", , True
            f.AddAction "NO", "Annuler", CLR_GREY(), False, True
            f.AddAction "OK", "Réformer", CLR_RED(), True
            r = f.ShowDialog
            If r = "OK" Then Tire_Scrap tireId, f.FieldValue("D"), f.FieldValue("MOT")
    End Select
    If Not f Is Nothing Then f.Dispose
End Sub

Public Sub Stock_MoveDialog(ByVal typ As String, ByVal articleId As Long)
    Dim f As frmInput, a As Object, rec As Object, trucks As Variant
    Set a = DB_Get("T_ARTICLES", articleId)
    If a Is Nothing Then Fail "Sélectionnez un article."
    trucks = Schema_FKChoices(Schema_Field("T_ROTATIONS", "CamionID"))
    Set f = Dlg_New(IIf(typ = "Entrée", "Entrée en stock", "Sortie de stock") & " - " & a("Code"), a("Designation") & " - stock actuel : " & _
                    FmtNum(a("Stock"), 2) & " " & a("Unite"), CLR_PURPLE())
    f.AddField "D", "Date", "DATE", App_Today(), , True
    f.AddField "Q", "Quantité", "NUM", "", , True
    If typ = "Entrée" Then
        f.AddField "PU", "Prix unitaire (GNF)", "MONEY", ToStr(a("PrixMoyen")), , True
        f.AddField "FOU", "Fournisseur", "LIST", "", Schema_FKChoices(Schema_Field("T_STOCK_MVT", "FournisseurID"))
    Else
        f.AddField "CAM", "Camion concerné", "LIST", "", trucks
    End If
    f.AddField "REF", "Référence pièce (BL / bon de sortie)", "TEXT", ""
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "OK", "Enregistrer", CLR_PURPLE(), True
    If f.ShowDialog = "OK" Then
        Set rec = NewRecord("DateMvt", f.FieldValue("D"), "ArticleID", articleId, "TypeMvt", typ, "Quantite", f.FieldValue("Q"), _
                            "Reference", f.FieldValue("REF"))
        If typ = "Entrée" Then
            rec("PU") = f.FieldValue("PU")
            If Len(f.FieldValue("FOU")) > 0 Then rec("FournisseurID") = IdOf("T_FOURNISSEURS", f.FieldValue("FOU"))
        Else
            If Len(f.FieldValue("CAM")) > 0 Then rec("CamionID") = IdOf("T_CAMIONS", f.FieldValue("CAM"))
        End If
        f.Dispose
        Rec_Save "T_STOCK_MVT", rec
    Else
        f.Dispose
    End If
End Sub
