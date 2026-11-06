Attribute VB_Name = "mBiz_Operations"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Operations
' Rotations, tickets de pesee, objectifs, valorisation de la production.
'
' Regles cles :
'  - poids net = brut - tare (calcule, jamais saisi) ;
'  - un ticket = une rotation (unicite du n° de ticket sur les deux tables) ;
'  - seule une rotation Validee alimente la facturation ;
'  - une rotation facturee est verrouillee ;
'  - le chauffeur doit etre affecte au camion a la date de la rotation.
'==============================================================================
Option Explicit

Public Sub Ops_BeforeSaveRotation(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    Dim brut As Double, tare As Double, net As Double, cap As Double, tol As Double
    Dim cam As Long, ch As Long, d As Date, dep As Double, arr As Double, dur As Double, destClient As Long, tk As String
    cam = ToLng(rec("CamionID")): ch = ToLng(rec("ChauffeurID")): d = ToDate(rec("DateRotation"))
    If d = 0 Then Fail "Date de rotation obligatoire."
    If d > App_Today() Then Fail "Une rotation ne peut pas être datée dans le futur (" & FmtDate(d) & ")."
    ' --- pesee ---
    brut = ToDbl(rec("PoidsBrut")): tare = ToDbl(rec("Tare"))
    If brut <= 0 Or tare <= 0 Then Fail "Poids brut et tare obligatoires (en tonnes)."
    If brut <= tare Then Fail "Le poids brut (" & FmtNum(brut, 2) & " t) doit être supérieur à la tare (" & FmtNum(tare, 2) & " t)."
    net = Round(brut - tare, 2)
    rec("PoidsNet") = net
    cap = ToDbl(DB_Value("T_CAMIONS", cam, "CapaciteT"))
    tol = Param_Num("ROT_TOLERANCE_SURCHARGE_PCT", 10) / 100
    If cap > 0 And net > cap * (1 + tol) Then Fail "Surcharge : " & FmtNum(net, 2) & " t pour une capacité de " & FmtNum(cap, 1) & _
        " t (tolérance " & FmtPct(tol, 0) & "). Vérifiez le ticket de pesée."
    If net < Param_Num("ROT_NET_MIN", 10) Then Fail "Poids net anormalement faible (" & FmtNum(net, 2) & " t < " & _
        Param_Num("ROT_NET_MIN", 10) & " t). Vérifiez le ticket de pesée."
    ' --- horaires ---
    dep = ToTime(rec("HDepart")): arr = ToTime(rec("HArrivee"))
    If dep < 0 Then Fail "Heure de départ invalide."
    If arr >= 0 Then
        dur = ShiftHours(dep, arr)
        If dur = 0 Then Fail "L'heure d'arrivée doit être différente de l'heure de départ."
        If dur > Param_Num("ROT_DUREE_MAX_H", 6) Then Fail "Durée de rotation incohérente (" & FmtNum(dur, 1) & " h > " & _
            Param_Num("ROT_DUREE_MAX_H", 6) & " h)."
    End If
    ' --- camion / chauffeur ---
    If ToStr(DB_Value("T_CAMIONS", cam, "Statut")) = CAM_HS Then Fail "Camion hors service."
    If Fleet_DriverFor(cam, d) <> ch Then
        If Fleet_DriverFor(cam, d) = 0 Then
            Fail "Aucune affectation du camion " & CodeOf("T_CAMIONS", cam) & " le " & FmtDate(d) & ". Créez d'abord l'affectation (module Affectations)."
        Else
            Fail "Le chauffeur " & CodeOf("T_CHAUFFEURS", ch) & " n'est pas affecté au camion " & CodeOf("T_CAMIONS", cam) & " le " & FmtDate(d) & _
                 " (chauffeur affecté : " & CodeOf("T_CHAUFFEURS", Fleet_DriverFor(cam, d)) & ")."
        End If
    End If
    If Not Fleet_PermitValid(ch, d) Then Fail "Permis du chauffeur " & CodeOf("T_CHAUFFEURS", ch) & " expiré le " & FmtDate(DB_Value("T_CHAUFFEURS", ch, "ExpPermis")) & "."
    ' --- lieux / client ---
    If ToStr(DB_Value("T_LIEUX", rec("OrigineID"), "TypeLieu")) <> "Chargement" Then Fail "L'origine doit être un lieu de chargement."
    If ToStr(DB_Value("T_LIEUX", rec("DestinationID"), "TypeLieu")) <> "Déchargement" Then Fail "La destination doit être un lieu de déchargement."
    destClient = ToLng(DB_Value("T_LIEUX", rec("DestinationID"), "ClientID"))
    If IsBlank(rec("ClientID")) Then rec("ClientID") = destClient
    If destClient > 0 And ToLng(rec("ClientID")) <> destClient Then Fail "Incohérence : la destination " & CodeOf("T_LIEUX", rec("DestinationID")) & _
        " appartient au client " & CodeOf("T_CLIENTS", destClient) & "."
    ' --- ticket ---
    tk = UCase$(Trim$(ToStr(rec("NumTicket"))))
    rec("NumTicket") = tk
    Dim rTk As Long
    rTk = DB_RowOf("T_PESEES", "NumTicket", tk)
    If rTk > 0 Then
        Dim linked As Variant
        linked = DB_Data("T_PESEES")(rTk, DB_ColIdx("T_PESEES", "RotationID"))
        If Not IsBlank(linked) And ToLng(linked) <> ToLng(rec("ID")) Then Fail "Le ticket " & tk & " est déjà rattaché à la rotation " & CodeOf("T_ROTATIONS", linked) & " (doublon)."
    End If
    ' --- doublon probable : meme camion, meme jour, depart a moins de 20 min ---
    Dim rows As Collection, r As Variant, data As Variant
    Set rows = DB_Rows("T_ROTATIONS", "CamionID", "=", cam, "DateRotation", "=", d, "Statut", "<>", ST_ANNULEE)
    data = DB_Data("T_ROTATIONS")
    For Each r In rows
        If ToLng(data(r, DB_ColIdx("T_ROTATIONS", "ID"))) <> ToLng(rec("ID")) Then
            If Abs(ToTime(data(r, DB_ColIdx("T_ROTATIONS", "HDepart"))) - dep) * 1440 < 20 Then _
                Fail "Doublon probable : le camion " & CodeOf("T_CAMIONS", cam) & " a déjà une rotation le " & FmtDate(d) & " au départ de " & _
                     FmtTime(data(r, DB_ColIdx("T_ROTATIONS", "HDepart"))) & " (" & data(r, DB_ColIdx("T_ROTATIONS", "NumRotation")) & ")."
        End If
    Next r
    ' --- statut ---
    If isNew Then
        rec("Statut") = ST_SAISIE
        rec("FactureID") = Empty
    ElseIf Not old Is Nothing Then
        If old("Statut") = ST_VALIDEE Then
            ' Toute modification d'une rotation validee impose une nouvelle validation
            rec("Statut") = ST_SAISIE
            rec("ValidePar") = Empty
            rec("ValideLe") = Empty
        End If
        If old("Statut") = ST_REJETEE Then rec("Statut") = ST_SAISIE
    End If
End Sub

Public Sub Ops_CheckRotationValidable(ByVal rec As Object)
    If ToDbl(rec("PoidsNet")) <= 0 Then Fail "Poids net nul : validation impossible."
    If IsBlank(rec("NumTicket")) Then Fail "Ticket de pesée manquant : validation impossible."
    If IsBlank(rec("HArrivee")) Then Fail "Heure d'arrivée manquante : complétez la rotation " & rec("NumRotation") & " avant validation."
End Sub

' Le ticket de pesee est le reflet de la rotation (creation / mise a jour automatique)
Public Sub Ops_SyncTicketFromRotation(ByVal rotId As Long)
    Dim rot As Object, rTk As Long, tk As Object, pont As String
    Set rot = DB_Get("T_ROTATIONS", rotId)
    If rot Is Nothing Then Exit Sub
    Set tk = NewRecord("NumTicket", rot("NumTicket"), "RotationID", rotId, "DatePesee", rot("DateRotation"), _
                       "CamionID", rot("CamionID"), "PoidsBrut", rot("PoidsBrut"), "Tare", rot("Tare"), _
                       "PoidsNet", rot("PoidsNet"), "Statut", rot("Statut"))
    If Not IsBlank(rot("HArrivee")) Then tk("HeurePesee") = rot("HArrivee")
    rTk = DB_RowOf("T_PESEES", "RotationID", rotId)
    If rTk = 0 Then rTk = DB_RowOf("T_PESEES", "NumTicket", rot("NumTicket"))
    If rTk = 0 Then
        pont = "PB-02 Gare"
        tk("Pont") = pont
        tk("Operateur") = Sec_CurrentName()
        DB_Insert "T_PESEES", tk
    Else
        DB_Update "T_PESEES", DB_Data("T_PESEES")(rTk, DB_ColIdx("T_PESEES", "ID")), tk
    End If
End Sub

Public Sub Ops_BeforeSaveTicket(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    If ToDbl(rec("PoidsBrut")) <= ToDbl(rec("Tare")) Then Fail "Le poids brut doit être supérieur à la tare."
    rec("PoidsNet") = Round(ToDbl(rec("PoidsBrut")) - ToDbl(rec("Tare")), 2)
    rec("NumTicket") = UCase$(Trim$(ToStr(rec("NumTicket"))))
    If isNew Then rec("Statut") = ST_SAISIE
    If Not IsBlank(rec("RotationID")) Then
        If ToLng(DB_Value("T_ROTATIONS", rec("RotationID"), "CamionID")) <> ToLng(rec("CamionID")) Then _
            Fail "Le camion du ticket ne correspond pas à celui de la rotation."
        If Not IsBlank(DB_Value("T_ROTATIONS", rec("RotationID"), "FactureID")) Then Fail "Rotation facturée : ticket verrouillé."
    End If
End Sub

' Ticket modifie (rotation non validee) -> poids reportes sur la rotation
Public Sub Ops_SyncRotationFromTicket(ByVal tkId As Long)
    Dim tk As Object, rot As Object
    Set tk = DB_Get("T_PESEES", tkId)
    If IsBlank(tk("RotationID")) Then Exit Sub
    Set rot = DB_Get("T_ROTATIONS", tk("RotationID"))
    If rot Is Nothing Then Exit Sub
    If rot("Statut") = ST_VALIDEE Then Exit Sub
    DB_Update "T_ROTATIONS", rot("ID"), NewRecord("PoidsBrut", tk("PoidsBrut"), "Tare", tk("Tare"), "PoidsNet", tk("PoidsNet"), "NumTicket", tk("NumTicket"))
End Sub

' Validation en masse d'une journee : retourne "valides|erreurs" et la liste des erreurs
Public Function Ops_ValidateDay(ByVal d As Date, ByRef errors As Collection) As Long
    Dim rows As Collection, r As Variant, data As Variant, id As Long, n As Long
    Sec_Require "ROTATIONS", "V"
    Set errors = New Collection
    Set rows = DB_Rows("T_ROTATIONS", "DateRotation", "=", d, "Statut", "=", ST_SAISIE)
    data = DB_Data("T_ROTATIONS")
    Dim ids As New Collection
    For Each r In rows
        ids.Add ToLng(data(r, DB_ColIdx("T_ROTATIONS", "ID")))
    Next r
    Dim v As Variant
    For Each v In ids
        id = v
        On Error Resume Next
        Rec_SetStatus "T_ROTATIONS", id, ST_VALIDEE
        If Err.Number <> 0 Then
            errors.Add CodeOf("T_ROTATIONS", id) & " : " & Err.Description
            Err.Clear
        Else
            n = n + 1
        End If
        On Error GoTo 0
    Next v
    Ops_ValidateDay = n
End Function

Public Sub Ops_BeforeSaveObjective(ByVal rec As Object)
    Dim p As String
    p = Trim$(ToStr(rec("Periode")))
    If Not p Like "####-##" Then Fail "Période au format AAAA-MM (ex. 2026-11)."
    If CInt(Mid$(p, 6, 2)) < 1 Or CInt(Mid$(p, 6, 2)) > 12 Then Fail "Mois invalide dans la période."
    rec("Periode") = p
End Sub

' Lieu le plus frequent d'un camion sur les 14 jours precedant d
Public Function Ops_UsualPlace(ByVal truckId As Long, ByVal col As String, ByVal d As Date) As Long
    Dim cnt As Object, k As Variant, best As Long, bestN As Long
    Set cnt = DB_Distinct("T_ROTATIONS", col, "CamionID", "=", truckId, "DateRotation", ">=", d - 14, "DateRotation", "<", d)
    For Each k In cnt.Keys
        If cnt(k) > bestN Then bestN = cnt(k): best = ToLng(k)
    Next k
    Ops_UsualPlace = best
End Function

' Chiffre d'affaires reconnu : tonnage valide x tarif client, filtre sur un champ (CamionID, ChauffeurID, ClientID ou "")
Public Function Ops_RevenueFor(ByVal filterCol As String, ByVal filterVal As Variant, ByVal d1 As Date, ByVal d2 As Date) As Double
    Dim data As Variant, r As Long, cSt As Long, cD As Long, cNet As Long, cCli As Long, cF As Long, t As Double
    Dim tarifs As Object, cli As Variant
    data = DB_Data("T_ROTATIONS")
    If IsEmpty(data) Then Exit Function
    Set tarifs = NewDict()
    cSt = DB_ColIdx("T_ROTATIONS", "Statut"): cD = DB_ColIdx("T_ROTATIONS", "DateRotation")
    cNet = DB_ColIdx("T_ROTATIONS", "PoidsNet"): cCli = DB_ColIdx("T_ROTATIONS", "ClientID")
    If Len(filterCol) > 0 Then cF = DB_ColIdx("T_ROTATIONS", filterCol)
    For r = 1 To UBound(data, 1)
        If data(r, cSt) = ST_VALIDEE Then
            If ToDate(data(r, cD)) >= d1 And ToDate(data(r, cD)) <= d2 Then
                If cF = 0 Or CompareValues(data(r, cF), filterVal) = 0 Then
                    cli = ToLng(data(r, cCli))
                    If Not tarifs.Exists(cli) Then tarifs(cli) = ToDbl(DB_Value("T_CLIENTS", cli, "TarifTonne"))
                    t = t + ToDbl(data(r, cNet)) * tarifs(cli)
                End If
            End If
        End If
    Next r
    Ops_RevenueFor = Round(t, 0)
End Function

' Tonnage valide d'une periode (filtre optionnel)
Public Function Ops_Tonnage(ByVal d1 As Date, ByVal d2 As Date, Optional ByVal filterCol As String = "", Optional ByVal filterVal As Variant) As Double
    If Len(filterCol) = 0 Then
        Ops_Tonnage = DB_Sum("T_ROTATIONS", "PoidsNet", "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    Else
        Ops_Tonnage = DB_Sum("T_ROTATIONS", "PoidsNet", "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2, filterCol, "=", filterVal)
    End If
End Function

' Saisie rapide d'une rotation (fenetre compacte, valeurs pre-remplies)
Public Sub Ops_QuickRotation()
    Dim f As frmInput, trucks As Variant, r As String, cam As Long, ch As Long, rec As Object, id As Long, d As Date
    Dim origs As Variant, dests As Variant
    Sec_Require "ROTATIONS", "C"
    trucks = Schema_FKChoices(Schema_Field("T_ROTATIONS", "CamionID"))
    origs = Ops_PlacesOfType("Chargement")
    dests = Ops_PlacesOfType("Déchargement")
    Do
        Set f = Dlg_New("Saisie rapide de rotation", "Le chauffeur, le client et le poids net sont déterminés automatiquement", CLR_GREEN(), 560)
        f.AddField "D", "Date", "DATE", App_Today(), , True
        f.AddField "CAM", "Camion", "LIST", "", trucks, True
        f.AddField "ORIG", "Origine (chargement)", "LIST", "", origs, True
        f.AddField "DEST", "Destination", "LIST", "", dests, True
        f.AddField "HD", "Heure de départ", "TIME", Format$(Time, "hh:mm"), , True
        f.AddField "HA", "Heure d'arrivée", "TIME", "", , False
        f.AddField "TK", "N° ticket de pesée", "TEXT", "", , True
        f.AddField "PB", "Poids brut (t)", "NUM", "", , True
        f.AddField "TA", "Tare (t)", "NUM", "", , True
        f.AddAction "CLOSE", "Fermer", CLR_GREY(), False, True
        f.AddAction "SAVE", "Enregistrer", CLR_BLUE(), False
        f.AddAction "NEXT", "Enregistrer + suivante", CLR_GREEN(), True
        r = f.ShowDialog
        If r = "SAVE" Or r = "NEXT" Then
            d = f.FieldValue("D")
            cam = IdOf("T_CAMIONS", f.FieldValue("CAM"))
            ch = Fleet_DriverFor(cam, d)
            Set rec = NewRecord("DateRotation", d, "CamionID", cam, "ChauffeurID", ch, _
                                "OrigineID", IdOf("T_LIEUX", f.FieldValue("ORIG")), "DestinationID", IdOf("T_LIEUX", f.FieldValue("DEST")), _
                                "HDepart", f.FieldValue("HD"), "NumTicket", f.FieldValue("TK"), _
                                "PoidsBrut", f.FieldValue("PB"), "Tare", f.FieldValue("TA"))
            If Not IsEmpty(f.FieldValue("HA")) Then rec("HArrivee") = f.FieldValue("HA")
            f.Dispose
            On Error Resume Next
            id = Rec_Save("T_ROTATIONS", rec)
            If Err.Number <> 0 Then
                Dim msg As String
                msg = Err.Description
                Err.Clear
                On Error GoTo 0
                UI_Alert msg, "warning", "Rotation non enregistrée"
                r = "NEXT"
            Else
                On Error GoTo 0
                UI_Toast "Rotation " & CodeOf("T_ROTATIONS", id) & " enregistrée (" & FmtNum(DB_Value("T_ROTATIONS", id, "PoidsNet"), 2) & " t net)."
            End If
        Else
            f.Dispose
        End If
    Loop While r = "NEXT"
End Sub

Public Function Ops_PlacesOfType(ByVal typ As String) As Variant
    Dim rows As Collection, r As Variant, data As Variant, out() As String, i As Long
    Set rows = DB_Rows("T_LIEUX", "TypeLieu", "=", typ, "Actif", "<>", "Non")
    data = DB_Data("T_LIEUX")
    If rows.Count = 0 Then Ops_PlacesOfType = Array(): Exit Function
    ReDim out(0 To rows.Count - 1)
    For Each r In rows
        out(i) = ToStr(data(r, DB_ColIdx("T_LIEUX", "Code")))
        i = i + 1
    Next r
    Ops_PlacesOfType = out
End Function
