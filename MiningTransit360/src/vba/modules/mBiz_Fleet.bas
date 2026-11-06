Attribute VB_Name = "mBiz_Fleet"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Fleet
' Camions, chauffeurs, affectations, programme journalier.
'==============================================================================
Option Explicit

'------------------------------------------------------------------ Camions
Public Sub Fleet_BeforeSaveTruck(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    If ToDbl(rec("TarifHoraire")) <= 0 Then Fail "Le tarif horaire de location doit être renseigné (> 0)."
    If ToDbl(rec("CapaciteT")) <= 0 Or ToDbl(rec("CapaciteT")) > 120 Then Fail "Capacité utile invalide (1 à 120 t)."
    rec("Code") = UCase$(Trim$(ToStr(rec("Code"))))
    rec("Immatriculation") = UCase$(Trim$(ToStr(rec("Immatriculation"))))
    If Not old Is Nothing Then
        If ToStr(rec("Actif")) = "Non" And ToStr(old("Actif")) = "Oui" Then
            If Fleet_ActiveAssignment(ToLng(old("ID")), App_Today()) > 0 Then _
                Fail "Ce camion a une affectation en cours : terminez-la avant de le désactiver."
        End If
        If ToDbl(rec("TarifHoraire")) <> ToDbl(old("TarifHoraire")) Then
            Log_Audit "TARIF-CAMION", "T_CAMIONS", old("ID"), "Tarif horaire " & FmtMoney(old("TarifHoraire")) & " -> " & _
                      FmtMoney(rec("TarifHoraire")) & " (les relevés existants conservent leur tarif historique)"
        End If
    End If
End Sub

Public Sub Fleet_Deactivate(ByVal truckId As Long)
    Dim rec As Object
    Sec_Require "CAMIONS", "M"
    If Not UI_Confirm("Désactiver le camion " & CodeOf("T_CAMIONS", truckId) & " ?" & vbLf & _
                      "Il ne sera plus proposé dans les saisies mais son historique est conservé.", "Désactivation", "Désactiver", True) Then Exit Sub
    Set rec = NewRecord("Actif", "Non", "Statut", CAM_HS)
    Rec_Save "T_CAMIONS", rec, truckId
End Sub

Public Function Fleet_IsTruckOperational(ByVal truckId As Long) As Boolean
    Dim st As String
    st = ToStr(DB_Value("T_CAMIONS", truckId, "Statut"))
    Fleet_IsTruckOperational = (st = CAM_DISPO Or st = CAM_SERVICE) And ToStr(DB_Value("T_CAMIONS", truckId, "Actif")) <> "Non"
End Function

'------------------------------------------------------------------ Chauffeurs
Public Sub Fleet_BeforeSaveDriver(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    rec("Nom") = UCase$(Trim$(ToStr(rec("Nom"))))
    rec("Matricule") = UCase$(Trim$(ToStr(rec("Matricule"))))
    If ToDate(rec("ExpPermis")) = 0 Then Fail "Date d'expiration du permis obligatoire."
    If isNew And ToDate(rec("ExpPermis")) < App_Today() Then Fail "Le permis est expiré : impossible d'enregistrer un chauffeur sans permis valide."
    If Not old Is Nothing Then
        If ToStr(rec("Statut")) = "Sorti" And Fleet_DriverActiveAssignment(ToLng(old("ID")), App_Today()) > 0 Then _
            Fail "Ce chauffeur a une affectation en cours : terminez-la avant de le sortir des effectifs."
    End If
End Sub

Public Function Fleet_PermitValid(ByVal driverId As Long, ByVal d As Date) As Boolean
    Fleet_PermitValid = (ToDate(DB_Value("T_CHAUFFEURS", driverId, "ExpPermis")) >= d)
End Function

'------------------------------------------------------------------ Affectations
Private Function Overlaps(ByVal a1 As Date, ByVal b1 As Date, ByVal a2 As Date, ByVal b2 As Date) As Boolean
    If b1 = 0 Then b1 = DateSerial(2999, 12, 31)
    If b2 = 0 Then b2 = DateSerial(2999, 12, 31)
    Overlaps = (a1 <= b2 And a2 <= b1)
End Function

Public Sub Fleet_BeforeSaveAssignment(ByVal rec As Object, ByVal isNew As Boolean)
    Dim a As Date, b As Date, data As Variant, r As Long, cId As Long, cCam As Long, cCh As Long, cA As Long, cB As Long, cSt As Long, cPo As Long
    Dim cam As Long, ch As Long, myId As Long
    a = ToDate(rec("DateDebut")): b = ToDate(rec("DateFin"))
    cam = ToLng(rec("CamionID")): ch = ToLng(rec("ChauffeurID")): myId = ToLng(rec("ID"))
    If a = 0 Then Fail "Date de début obligatoire."
    If b > 0 And b < a Then Fail "La date de fin doit être postérieure ou égale à la date de début."
    If ToStr(DB_Value("T_CAMIONS", cam, "Actif")) = "Non" Or ToStr(DB_Value("T_CAMIONS", cam, "Statut")) = CAM_HS Then _
        Fail "Le camion " & CodeOf("T_CAMIONS", cam) & " est hors service ou désactivé."
    If isNew And (ToStr(DB_Value("T_CAMIONS", cam, "Statut")) = CAM_PANNE Or ToStr(DB_Value("T_CAMIONS", cam, "Statut")) = CAM_IMMO) And a <= App_Today() Then _
        Fail "Le camion " & CodeOf("T_CAMIONS", cam) & " est actuellement " & LCase$(DB_Value("T_CAMIONS", cam, "Statut")) & " : affectation impossible à cette date."
    If ToStr(DB_Value("T_CHAUFFEURS", ch, "Statut")) <> "Actif" Then Fail "Le chauffeur n'est pas au statut Actif (" & DB_Value("T_CHAUFFEURS", ch, "Statut") & ")."
    If Not Fleet_PermitValid(ch, a) Then Fail "Le permis du chauffeur " & CodeOf("T_CHAUFFEURS", ch) & " est expiré à la date de début (" & _
        FmtDate(DB_Value("T_CHAUFFEURS", ch, "ExpPermis")) & ")."
    data = DB_Data("T_AFFECTATIONS")
    If Not IsEmpty(data) Then
        cId = DB_ColIdx("T_AFFECTATIONS", "ID"): cCam = DB_ColIdx("T_AFFECTATIONS", "CamionID")
        cCh = DB_ColIdx("T_AFFECTATIONS", "ChauffeurID"): cA = DB_ColIdx("T_AFFECTATIONS", "DateDebut")
        cB = DB_ColIdx("T_AFFECTATIONS", "DateFin"): cSt = DB_ColIdx("T_AFFECTATIONS", "Statut"): cPo = DB_ColIdx("T_AFFECTATIONS", "Poste")
        For r = 1 To UBound(data, 1)
            If ToLng(data(r, cId)) <> myId And ToStr(data(r, cSt)) <> "Suspendue" Then
                If Overlaps(a, b, ToDate(data(r, cA)), ToDate(data(r, cB))) Then
                    If ToLng(data(r, cCam)) = cam And ToStr(data(r, cPo)) = ToStr(rec("Poste")) Then _
                        Fail "Chevauchement : le camion " & CodeOf("T_CAMIONS", cam) & " est déjà affecté au chauffeur " & _
                             CodeOf("T_CHAUFFEURS", data(r, cCh)) & " (poste " & data(r, cPo) & ") du " & FmtDate(data(r, cA)) & _
                             IIf(IsBlank(data(r, cB)), " (en cours)", " au " & FmtDate(data(r, cB))) & "."
                    If ToLng(data(r, cCh)) = ch Then _
                        Fail "Chevauchement : le chauffeur " & CodeOf("T_CHAUFFEURS", ch) & " est déjà affecté au camion " & _
                             CodeOf("T_CAMIONS", data(r, cCam)) & " sur cette période (depuis le " & FmtDate(data(r, cA)) & ")."
                End If
            End If
        Next r
    End If
    If ToStr(rec("Statut")) <> "Suspendue" Then
        If b > 0 And b < App_Today() Then rec("Statut") = "Terminée" Else rec("Statut") = "Active"
    End If
End Sub

Public Sub Fleet_AfterAssignment(ByVal id As Long)
    Dim cam As Long
    cam = ToLng(DB_Value("T_AFFECTATIONS", id, "CamionID"))
    If ToStr(DB_Value("T_CAMIONS", cam, "Statut")) = CAM_DISPO And Fleet_ActiveAssignment(cam, App_Today()) > 0 Then
        DB_SetValue "T_CAMIONS", cam, "Statut", CAM_SERVICE
    ElseIf ToStr(DB_Value("T_CAMIONS", cam, "Statut")) = CAM_SERVICE And Fleet_ActiveAssignment(cam, App_Today()) = 0 Then
        DB_SetValue "T_CAMIONS", cam, "Statut", CAM_DISPO
    End If
End Sub

' Termine une affectation a une date donnee
Public Sub Fleet_EndAssignment(ByVal id As Long, ByVal endDate As Date)
    Dim a As Object
    Set a = DB_Get("T_AFFECTATIONS", id)
    If a Is Nothing Then Fail "Affectation introuvable."
    If Not IsBlank(a("DateFin")) And ToStr(a("Statut")) = "Terminée" Then Fail "Affectation déjà terminée."
    If endDate < ToDate(a("DateDebut")) Then Fail "La date de fin ne peut pas précéder la date de début."
    Rec_Save "T_AFFECTATIONS", NewRecord("DateFin", endDate, "Statut", IIf(endDate < App_Today(), "Terminée", "Active")), id
End Sub

' Affectation couvrant (camion, date) -> ID affectation (0 si aucune)
Public Function Fleet_ActiveAssignment(ByVal truckId As Long, ByVal d As Date) As Long
    Dim data As Variant, r As Long, cCam As Long, cA As Long, cB As Long, cSt As Long
    data = DB_Data("T_AFFECTATIONS")
    If IsEmpty(data) Then Exit Function
    cCam = DB_ColIdx("T_AFFECTATIONS", "CamionID"): cA = DB_ColIdx("T_AFFECTATIONS", "DateDebut")
    cB = DB_ColIdx("T_AFFECTATIONS", "DateFin"): cSt = DB_ColIdx("T_AFFECTATIONS", "Statut")
    For r = 1 To UBound(data, 1)
        If ToLng(data(r, cCam)) = truckId And ToStr(data(r, cSt)) <> "Suspendue" Then
            If ToDate(data(r, cA)) <= d And (IsBlank(data(r, cB)) Or ToDate(data(r, cB)) >= d) Then
                Fleet_ActiveAssignment = ToLng(data(r, DB_ColIdx("T_AFFECTATIONS", "ID")))
                Exit Function
            End If
        End If
    Next r
End Function

Public Function Fleet_DriverActiveAssignment(ByVal driverId As Long, ByVal d As Date) As Long
    Dim rows As Collection, r As Variant, data As Variant
    Set rows = DB_Rows("T_AFFECTATIONS", "ChauffeurID", "=", driverId, "DateDebut", "<=", d, "Statut", "<>", "Suspendue")
    data = DB_Data("T_AFFECTATIONS")
    For Each r In rows
        If IsBlank(data(r, DB_ColIdx("T_AFFECTATIONS", "DateFin"))) Or ToDate(data(r, DB_ColIdx("T_AFFECTATIONS", "DateFin"))) >= d Then
            Fleet_DriverActiveAssignment = ToLng(data(r, DB_ColIdx("T_AFFECTATIONS", "ID")))
            Exit Function
        End If
    Next r
End Function

' Chauffeur affecte au camion a la date (0 si aucun)
Public Function Fleet_DriverFor(ByVal truckId As Long, ByVal d As Date) As Long
    Dim a As Long
    a = Fleet_ActiveAssignment(truckId, d)
    If a > 0 Then Fleet_DriverFor = ToLng(DB_Value("T_AFFECTATIONS", a, "ChauffeurID"))
End Function

'------------------------------------------------------------------ Programme journalier
Public Sub Fleet_BeforeSavePlanning(ByVal rec As Object, ByVal isNew As Boolean)
    Dim cam As Long, d As Date
    cam = ToLng(rec("CamionID")): d = ToDate(rec("DatePlan"))
    If isNew Then
        If DB_CountIf("T_PLANNING", "DatePlan", "=", d, "CamionID", "=", cam, "Poste", "=", ToStr(rec("Poste")), "Statut", "<>", "Annulé") > 0 Then _
            Fail "Le camion " & CodeOf("T_CAMIONS", cam) & " est déjà programmé le " & FmtDate(d) & " (poste " & rec("Poste") & ")."
    End If
    If IsBlank(rec("ChauffeurID")) Then rec("ChauffeurID") = Fleet_DriverFor(cam, d)
    If ToLng(rec("ChauffeurID")) = 0 Then rec("ChauffeurID") = Empty
    If ToDbl(rec("TonnagePrevu")) = 0 And ToLng(rec("RotationsPrevues")) > 0 Then
        rec("TonnagePrevu") = Round(ToLng(rec("RotationsPrevues")) * ToDbl(DB_Value("T_CAMIONS", cam, "CapaciteT")) * 0.95, 1)
    End If
End Sub

' Genere le programme d'une date a partir des affectations actives et camions operationnels
Public Function Fleet_GeneratePlanning(ByVal d As Date, ByVal rotationsPerTruck As Long) As Long
    Dim data As Variant, r As Long, cam As Long, ch As Long, n As Long, rec As Object, dest As Long, orig As Long
    data = DB_Data("T_CAMIONS")
    For r = 1 To ArrayRows(data)
        cam = ToLng(data(r, DB_ColIdx("T_CAMIONS", "ID")))
        If Fleet_IsTruckOperational(cam) Then
            ch = Fleet_DriverFor(cam, d)
            If ch > 0 And DB_CountIf("T_PLANNING", "DatePlan", "=", d, "CamionID", "=", cam) = 0 Then
                ' destination et origine les plus frequentes sur les 14 derniers jours
                dest = Ops_UsualPlace(cam, "DestinationID", d)
                orig = Ops_UsualPlace(cam, "OrigineID", d)
                Set rec = NewRecord("DatePlan", d, "CamionID", cam, "ChauffeurID", ch, "Poste", "Jour", _
                                    "RotationsPrevues", rotationsPerTruck, "Statut", "Planifié")
                If dest > 0 Then rec("DestinationID") = dest
                If orig > 0 Then rec("OrigineID") = orig
                Rec_Save "T_PLANNING", rec
                n = n + 1
            End If
        End If
    Next r
    Fleet_GeneratePlanning = n
End Function

'------------------------------------------------------------------ Statistiques
Public Function Fleet_TruckStats(ByVal truckId As Long, ByVal d1 As Date, ByVal d2 As Date) As Object
    Dim s As Object, ton As Double, rot As Long, liters As Double, fuel As Double, loc As Double, hrs As Double, immo As Double
    Set s = NewDict()
    ton = DB_Sum("T_ROTATIONS", "PoidsNet", "CamionID", "=", truckId, "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    rot = DB_CountIf("T_ROTATIONS", "CamionID", "=", truckId, "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    liters = DB_Sum("T_CARBURANT", "Litres", "CamionID", "=", truckId, "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateOp", ">=", d1, "DateOp", "<=", d2)
    fuel = DB_Sum("T_CARBURANT", "Montant", "CamionID", "=", truckId, "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateOp", ">=", d1, "DateOp", "<=", d2)
    loc = DB_Sum("T_LOCATION", "Montant", "CamionID", "=", truckId, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateReleve", ">=", d1, "DateReleve", "<=", d2)
    hrs = DB_Sum("T_LOCATION", "HeuresRetenues", "CamionID", "=", truckId, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE, "DateReleve", ">=", d1, "DateReleve", "<=", d2)
    immo = DB_Sum("T_LOCATION", "HeuresImmobAtiko", "CamionID", "=", truckId, "DateReleve", ">=", d1, "DateReleve", "<=", d2) + _
           DB_Sum("T_LOCATION", "HeuresImmobExploitant", "CamionID", "=", truckId, "DateReleve", ">=", d1, "DateReleve", "<=", d2)
    s("Tonnage") = ton
    s("Rotations") = rot
    s("Litres") = liters
    s("CoutCarburant") = fuel
    s("CoutLocation") = loc
    s("HeuresRetenues") = hrs
    s("HeuresImmob") = immo
    s("Pneus") = DB_Sum("T_PNEU_MVT", "Cout", "CamionID", "=", truckId, "DateMvt", ">=", d1, "DateMvt", "<=", d2)
    s("CA") = Ops_RevenueFor("CamionID", truckId, d1, d2)
    s("Marge") = s("CA") - fuel - loc - s("Pneus")
    If ton > 0 Then s("CoutTonne") = (fuel + loc + s("Pneus")) / ton Else s("CoutTonne") = 0
    If hrs > 0 Then s("LitresHeure") = liters / hrs Else s("LitresHeure") = 0
    If rot > 0 Then s("TonnageMoyen") = ton / rot Else s("TonnageMoyen") = 0
    Set Fleet_TruckStats = s
End Function
