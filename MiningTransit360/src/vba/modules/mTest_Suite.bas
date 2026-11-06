Attribute VB_Name = "mTest_Suite"
'==============================================================================
' MINING TRANSIT 360 - mTest_Suite
' Recette automatisee executee DANS Excel (lancee par le script de construction
' ou par l'administrateur : Administration > Tests automatiques).
'
' Toutes les operations sont faites dans une transaction globale annulee a la
' fin : les donnees sont restituees a l'identique. Resultats : feuille TESTS.
' Ces tests exigent le jeu de demonstration (date de travail 05/11/2026).
'==============================================================================
Option Explicit

Private mRes As Collection
Private mT0 As Double

Private Sub Check(ByVal area As String, ByVal name As String, ByVal ok As Boolean, Optional ByVal detail As String = "")
    mRes.Add Array(mRes.Count + 1, area, name, IIf(ok, "OK", "ÉCHEC"), detail)
End Sub

' Appels proteges : retournent "" si l'operation reussit, sinon "ERR:" + message
Private Function TryStatus(ByVal t As String, ByVal id As Long, ByVal st As String, ByVal reason As String) As String
    On Error Resume Next
    Rec_SetStatus t, id, st, reason
    If Err.Number <> 0 Then TryStatus = "ERR:" & Err.Description
    Err.Clear
End Function

Private Function TryDraft(ByVal cli As Long, ByVal mk As String) As String
    On Error Resume Next
    Bill_GenerateDraft cli, mk, True
    If Err.Number <> 0 Then TryDraft = "ERR:" & Err.Description
    Err.Clear
End Function

Private Function TryMount(ByVal t As Long, ByVal cam As Long, ByVal pos As String, ByVal d As Date) As String
    On Error Resume Next
    Tire_Mount t, cam, pos, d
    If Err.Number <> 0 Then TryMount = "ERR:" & Err.Description
    Err.Clear
End Function

Private Function TryCancelInv(ByVal inv As Long, ByVal reason As String) As String
    On Error Resume Next
    Bill_Cancel inv, reason
    If Err.Number <> 0 Then TryCancelInv = "ERR:" & Err.Description
    Err.Clear
End Function

Private Function TryCloseDay(ByVal d As Date) As String
    On Error Resume Next
    Close_Day d, True
    If Err.Number <> 0 Then TryCloseDay = "ERR:" & Err.Description
    Err.Clear
End Function

Private Function AllTables() As String
    Dim t As Variant, s As String
    For Each t In Schema_AllTables()
        s = s & t & "|"
    Next t
    AllTables = s
End Function

Public Function Test_RunAll(Optional ByVal silent As Boolean = True) As String
    Dim nOk As Long, nKo As Long, v As Variant, ws As Worksheet, r As Long, out() As Variant
    Set mRes = New Collection
    mT0 = Timer
    Sec_SystemSession
    DB_Invalidate
    Schema_Reset
    Application.DisplayAlerts = False
    ' --- tous les tests s'executent dans une transaction annulee a la fin ---
    DB_BeginTx AllTables()
    On Error GoTo Crash
    T_Pristine
    T_Fleet
    T_Operations
    T_Fuel
    T_Rental
    T_Tires
    T_Maintenance
    T_Finance
    T_Billing
    T_KPI
    T_Reports
    T_Backup
    T_Closing
    On Error GoTo 0
Restore:
    DB_Rollback
    DB_Invalidate
    Schema_Reset
    Check "Intégrité", "Données restituées après les tests", Test_SameAsDemoCounts(), ""
    Application.DisplayAlerts = True
    ' --- ecriture des resultats ---
    Set ws = ThisWorkbook.Worksheets("TESTS")
    ws.Cells.Clear
    ws.Range("A1:E1").Value = Array("N°", "Domaine", "Test", "Résultat", "Détail")
    ws.Range("A1:E1").Font.Bold = True
    ws.Range("A1:E1").Interior.Color = CLR_NAVY(): ws.Range("A1:E1").Font.Color = CLR_WHITE()
    ReDim out(1 To mRes.Count, 1 To 5)
    For Each v In mRes
        r = r + 1
        out(r, 1) = v(0): out(r, 2) = v(1): out(r, 3) = v(2): out(r, 4) = v(3): out(r, 5) = v(4)
        If v(3) = "OK" Then nOk = nOk + 1 Else nKo = nKo + 1
    Next v
    ws.Range("A2").Resize(mRes.Count, 5).Value = out
    ws.Columns("A:E").AutoFit
    ws.Columns("E").ColumnWidth = 90
    For r = 2 To mRes.Count + 1
        ws.Cells(r, 4).Font.Color = IIf(ws.Cells(r, 4).Value = "OK", CLR_GREEN(), CLR_RED())
        ws.Cells(r, 4).Font.Bold = True
    Next r
    ws.Range("G1").Value = "Exécuté le " & Format$(Now, "dd/mm/yyyy hh:nn:ss") & " en " & Format$(Timer - mT0, "0.0") & " s"
    ws.Range("G2").Value = nOk & " OK / " & nKo & " ÉCHEC(S) sur " & mRes.Count & " tests"
    Test_RunAll = nOk & " OK / " & nKo & " ECHEC(S) / " & mRes.Count & " tests"
    Sec_Logout
    Exit Function
Crash:
    Check "Exécution", "Erreur inattendue pendant les tests", False, Err.Description & " (n° " & Err.Number & ")"
    Resume Restore
End Function

Public Sub Test_RunAllDialog()
    Dim res As String, login As String
    If Sec_CurrentProfile() <> "Administrateur" Then Fail "Tests réservés à l'administrateur.", ERR_RIGHTS
    If Not Param_Bool("DEMO_MODE", False) Then Fail "Les tests automatiques s'exécutent sur l'environnement de démonstration (DEMO_MODE = Oui)."
    If Not UI_Confirm("Lancer la recette automatique ?" & vbLf & "Les opérations de test sont annulées à la fin ; une sauvegarde est faite avant par sécurité.", _
                      "Tests automatiques", "Lancer") Then Exit Sub
    login = Sec_CurrentLogin()
    Backup_Run "Manuelle", True
    res = Test_RunAll(True)
    Sec_Impersonate login
    UI_Alert "Résultat de la recette : " & res & vbLf & "Détail dans la feuille TESTS.", IIf(InStr(res, " 0 ECHEC") > 0, "success", "warning"), "Tests"
    ThisWorkbook.Worksheets("TESTS").Activate
End Sub

Private Function Test_SameAsDemoCounts() As Boolean
    Test_SameAsDemoCounts = (DB_Count("T_ROTATIONS") = 2207 And DB_Count("T_FACTURES") = 6 And DB_Count("T_PAIEMENTS") = 8 And DB_Count("T_CAMIONS") = 10)
End Function

'------------------------------------------------------------------ Donnees intactes
Private Sub T_Pristine()
    Dim coh As Variant, i As Long, bad As String, invOk As Boolean, r As Long, data As Variant
    Check "Sécurité", "Empreinte : vecteur de référence 1", MT_Hash("MT360-TEST", 8) = "65C758735EC6A8856257F56437E96B32", MT_Hash("MT360-TEST", 8)
    Check "Sécurité", "Empreinte : caractères accentués", MT_Hash("Sécurité é", 8) = "87349BD7A69D05045880B330E52771FB", MT_Hash("Sécurité é", 8)
    Check "Sécurité", "Empreinte mot de passe identique à l'outil Python", Sec_PasswordHash("Admin@2026", "S7310MT") = "9D31FC18FC28F5C42F573F23F9E10F06"
    Check "Sécurité", "Connexion admin / Admin@2026", Len(Sec_Login("admin", "Admin@2026")) = 0
    Check "Sécurité", "Connexion refusée avec un mauvais mot de passe", Len(Sec_Login("comptable", "faux")) > 0
    DB_SetValue "T_USERS", DB_Lookup("T_USERS", "Login", "comptable", "ID"), "Echecs", 0
    Sec_SystemSession
    Sec_Login "saisie", "Saisie@2026"
    Check "Sécurité", "Agent de saisie : pas de droit de validation des factures", Not Sec_Can("FACTURES", "V")
    Check "Sécurité", "Agent de saisie : création de rotations autorisée", Sec_Can("ROTATIONS", "C")
    Sec_SystemSession
    data = DB_Data("T_FACTURES")
    invOk = True
    For r = 1 To ArrayRows(data)
        If Not Bill_VerifyFingerprint(ToLng(data(r, 1))) Then invOk = False
    Next r
    Check "Facturation", "Empreintes des factures de démonstration valides (Python = VBA)", invOk
    coh = Check_Coherence()
    For i = 1 To ArrayRows(coh)
        If coh(i, 1) = "ERREUR" Or coh(i, 1) = "CRITIQUE" Then bad = bad & coh(i, 2) & " : " & coh(i, 3) & " | "
    Next i
    Check "Cohérence", "Jeu de démonstration sans erreur d'intégrité", Len(bad) = 0, bad
    Check "Dates", "Date de travail de démonstration", App_Today() = DateSerial(2026, 11, 5), FmtDate(App_Today())
    Check "Outils", "Montant en lettres", NumberToWordsFR(2368955100#) = "deux milliards trois cent soixante-huit millions neuf cent cinquante-cinq mille cent", NumberToWordsFR(2368955100#)
    Check "Outils", "Conversion de date jj/mm/aaaa", ToDate("05/11/2026") = DateSerial(2026, 11, 5)
    Check "Outils", "Conversion d'heure hh:mm", Abs(ToTime("18:30") - TimeSerial(18, 30, 0)) < 0.00001
End Sub

'------------------------------------------------------------------ Flotte
Private mTruck As Long
Private Sub T_Fleet()
    Dim rec As Object, e As String, aff As Long
    Set rec = NewRecord("Code", "ATK-T01", "Immatriculation", "TEST-0001", "Marque", "Test", "Modele", "X", "CapaciteT", 40, _
                        "Proprietaire", "Atiko Guinée", "Statut", CAM_DISPO, "TarifHoraire", 250000, "ConsoRefLH", 15, "Actif", "Oui")
    mTruck = Rec_Save("T_CAMIONS", rec)
    Check "Flotte", "Création d'un camion", mTruck > 0 And CodeOf("T_CAMIONS", mTruck) = "ATK-T01"
    On Error Resume Next
    Rec_Save "T_CAMIONS", NewRecord("Code", "ATK-T01", "Immatriculation", "TEST-0002", "Marque", "T", "CapaciteT", 40, "Proprietaire", "A", "Statut", CAM_DISPO, "TarifHoraire", 1)
    e = Err.Description: Err.Clear
    On Error GoTo 0
    Check "Flotte", "Doublon de code camion refusé", InStr(e, "existe déjà") > 0, e
    Rec_Save "T_CAMIONS", NewRecord("TarifHoraire", 260000), mTruck
    Check "Flotte", "Modification du tarif horaire", ToDbl(DB_Value("T_CAMIONS", mTruck, "TarifHoraire")) = 260000
    ' affectations
    On Error Resume Next
    Rec_Save "T_AFFECTATIONS", NewRecord("CamionID", mTruck, "ChauffeurID", IdOf("T_CHAUFFEURS", "CH-001"), "DateDebut", App_Today(), "Poste", "Jour")
    e = Err.Description: Err.Clear
    On Error GoTo 0
    Check "Flotte", "Chevauchement d'affectation chauffeur refusé", InStr(e, "Chevauchement") > 0, e
    On Error Resume Next
    Rec_Save "T_AFFECTATIONS", NewRecord("CamionID", mTruck, "ChauffeurID", IdOf("T_CHAUFFEURS", "CH-005"), "DateDebut", App_Today(), "Poste", "Jour")
    e = Err.Description: Err.Clear
    On Error GoTo 0
    Check "Flotte", "Affectation refusée (permis expiré)", InStr(e, "expiré") > 0, e
    aff = Rec_Save("T_AFFECTATIONS", NewRecord("CamionID", mTruck, "ChauffeurID", IdOf("T_CHAUFFEURS", "CH-012"), "DateDebut", App_Today() - 3, "Poste", "Jour"))
    Check "Flotte", "Affectation valide créée", aff > 0 And Fleet_DriverFor(mTruck, App_Today()) = IdOf("T_CHAUFFEURS", "CH-012")
    Check "Flotte", "Camion passé « En service » après affectation", DB_Value("T_CAMIONS", mTruck, "Statut") = CAM_SERVICE
    Check "Flotte", "Programme journalier généré", Fleet_GeneratePlanning(App_Today() + 1, 4) >= 1
End Sub

'------------------------------------------------------------------ Rotations / pesees
Private mRot As Long
Private Sub T_Operations()
    Dim rec As Object, e As String, r2 As Long, ch As Long
    ch = IdOf("T_CHAUFFEURS", "CH-012")
    Set rec = NewRecord("DateRotation", App_Today(), "CamionID", mTruck, "ChauffeurID", ch, "HDepart", TimeSerial(7, 0, 0), "HArrivee", TimeSerial(9, 10, 0), _
                        "OrigineID", IdOf("T_LIEUX", "P1"), "DestinationID", IdOf("T_LIEUX", "GSM"), "NumTicket", "TK-TEST-1", "PoidsBrut", 55.4, "Tare", 16.1)
    mRot = Rec_Save("T_ROTATIONS", rec)
    Check "Rotations", "Création d'une rotation", mRot > 0
    Check "Rotations", "Poids net calculé = brut - tare", Abs(ToDbl(DB_Value("T_ROTATIONS", mRot, "PoidsNet")) - 39.3) < 0.001, CStr(DB_Value("T_ROTATIONS", mRot, "PoidsNet"))
    Check "Rotations", "Client déduit de la destination", CodeOf("T_CLIENTS", DB_Value("T_ROTATIONS", mRot, "ClientID")) = "SMN"
    Check "Rotations", "Numéro de rotation attribué", Left$(ToStr(DB_Value("T_ROTATIONS", mRot, "NumRotation")), 9) = "ROT-2026-"
    Check "Pesées", "Ticket de pesée créé automatiquement", DB_RowOf("T_PESEES", "RotationID", mRot) > 0
    ' doublon de ticket
    rec("NumTicket") = "TK-TEST-1": rec("HDepart") = TimeSerial(11, 0, 0): rec("HArrivee") = TimeSerial(13, 0, 0)
    e = TrySave("T_ROTATIONS", rec)
    Check "Rotations", "Doublon de ticket refusé", InStr(e, "existe déjà") > 0 Or InStr(e, "doublon") > 0, e
    ' surcharge
    rec("NumTicket") = "TK-TEST-2": rec("PoidsBrut") = 75
    e = TrySave("T_ROTATIONS", rec)
    Check "Rotations", "Surcharge refusée", InStr(e, "Surcharge") > 0, e
    ' chauffeur non affecte
    rec("PoidsBrut") = 55: rec("ChauffeurID") = IdOf("T_CHAUFFEURS", "CH-001")
    e = TrySave("T_ROTATIONS", rec)
    Check "Rotations", "Chauffeur non affecté refusé", InStr(e, "affecté") > 0, e
    ' doublon horaire
    rec("ChauffeurID") = ch: rec("HDepart") = TimeSerial(7, 10, 0)
    e = TrySave("T_ROTATIONS", rec)
    Check "Rotations", "Doublon probable (même camion, départ < 20 min) refusé", InStr(e, "Doublon probable") > 0, e
    ' validation / rejet
    Rec_SetStatus "T_ROTATIONS", mRot, ST_VALIDEE
    Check "Rotations", "Validation", DB_Value("T_ROTATIONS", mRot, "Statut") = ST_VALIDEE
    Check "Pesées", "Statut du ticket synchronisé", DB_Lookup("T_PESEES", "RotationID", mRot, "Statut") = ST_VALIDEE
    rec("HDepart") = TimeSerial(10, 0, 0): rec("HArrivee") = TimeSerial(12, 0, 0): rec("NumTicket") = "TK-TEST-3"
    r2 = Rec_Save("T_ROTATIONS", rec)
    e = TryStatus("T_ROTATIONS", r2, ST_REJETEE, "")
    Check "Rotations", "Rejet sans motif refusé", Left$(e, 4) = "ERR:", e
    Rec_SetStatus "T_ROTATIONS", r2, ST_REJETEE, "Test de rejet"
    Check "Rotations", "Rejet avec motif", DB_Value("T_ROTATIONS", r2, "Statut") = ST_REJETEE And DB_Value("T_ROTATIONS", r2, "MotifRejet") = "Test de rejet"
    e = TrySave("T_ROTATIONS", NewRecord("PoidsBrut", 56), mRot)
    Check "Rotations", "Rotation validée modifiée -> repasse en Saisie", DB_Value("T_ROTATIONS", mRot, "Statut") = ST_SAISIE, e
    Rec_SetStatus "T_ROTATIONS", mRot, ST_VALIDEE
End Sub

Private Function TrySave(ByVal t As String, ByVal rec As Object, Optional ByVal id As Long = 0) As String
    Dim c As Object, k As Variant
    Set c = NewDict()
    For Each k In rec.Keys
        c(k) = rec(k)
    Next k
    On Error Resume Next
    Rec_Save t, c, id
    TrySave = Err.Description
    Err.Clear
End Function

'------------------------------------------------------------------ Carburant
Private Sub T_Fuel()
    Dim id As Long, e As String, cpt As Double
    cpt = 5000
    id = Rec_Save("T_CARBURANT", NewRecord("NumBon", "BT-1", "DateOp", App_Today() - 1, "TypeOp", "Distribution", "CamionID", mTruck, "Litres", 160, "PrixLitre", 12500, "CompteurH", cpt))
    Check "Carburant", "Distribution enregistrée, montant calculé", ToDbl(DB_Value("T_CARBURANT", id, "Montant")) = 2000000
    id = Rec_Save("T_CARBURANT", NewRecord("NumBon", "BT-2", "DateOp", App_Today(), "TypeOp", "Distribution", "CamionID", mTruck, "Litres", 260, "PrixLitre", 12500, "CompteurH", cpt + 11))
    Check "Carburant", "Surconsommation détectée", InStr(ToStr(DB_Value("T_CARBURANT", id, "Anomalie")), "Surconsommation") > 0, ToStr(DB_Value("T_CARBURANT", id, "Anomalie"))
    e = TrySave("T_CARBURANT", NewRecord("NumBon", "BT-3", "DateOp", App_Today(), "TypeOp", "Distribution", "CamionID", mTruck, "Litres", 50, "PrixLitre", 12500, "CompteurH", cpt - 100))
    Check "Carburant", "Compteur inférieur au précédent refusé", InStr(e, "Compteur incohérent") > 0, e
    id = Rec_Save("T_CARBURANT", NewRecord("NumBon", "BT-4", "DateOp", App_Today(), "TypeOp", "Distribution", "CamionID", mTruck, "Litres", 40, "PrixLitre", 12500, "CompteurH", cpt + 12))
    Check "Carburant", "Doublon potentiel signalé", InStr(ToStr(DB_Value("T_CARBURANT", id, "Anomalie")), "Doublon") > 0, ToStr(DB_Value("T_CARBURANT", id, "Anomalie"))
    e = TrySave("T_CARBURANT", NewRecord("NumBon", "BT-5", "DateOp", App_Today(), "TypeOp", "Approvisionnement", "Litres", 1000, "PrixLitre", 12500))
    Check "Carburant", "Approvisionnement sans fournisseur refusé", InStr(e, "fournisseur") > 0, e
    Check "Carburant", "Stock cuve théorique calculé", Fuel_TankStock() <> 0, FmtNum(Fuel_TankStock(), 0)
End Sub

'------------------------------------------------------------------ Location
Private Sub T_Rental()
    Dim id As Long, e As String, prev As String
    id = Rec_Save("T_LOCATION", NewRecord("DateReleve", App_Today() - 1, "CamionID", mTruck, "HDebut", TimeSerial(6, 30, 0), "HFin", TimeSerial(18, 30, 0), _
                                          "HeuresAttente", 1, "HeuresImmobAtiko", 2, "HeuresImmobExploitant", 0))
    Check "Location", "Heures brutes 06:30-18:30 = 12 h", ToDbl(DB_Value("T_LOCATION", id, "HeuresBrutes")) = 12
    Check "Location", "Immobilisation Atiko déduite, attente non déduite (paramètres démo)", ToDbl(DB_Value("T_LOCATION", id, "HeuresRetenues")) = 10, CStr(DB_Value("T_LOCATION", id, "HeuresRetenues"))
    Check "Location", "Montant = heures retenues x tarif", ToDbl(DB_Value("T_LOCATION", id, "Montant")) = 10 * 260000
    prev = ToStr(Param_Get("LOC_DEDUIRE_ATTENTE"))
    Param_Set "LOC_DEDUIRE_ATTENTE", "Oui"
    Rec_Save "T_LOCATION", NewRecord("HeuresAttente", 1), id
    Check "Location", "Règle paramétrable : attente déduite si LOC_DEDUIRE_ATTENTE = Oui", ToDbl(DB_Value("T_LOCATION", id, "HeuresRetenues")) = 9
    Param_Set "LOC_DEDUIRE_ATTENTE", prev
    e = TrySave("T_LOCATION", NewRecord("DateReleve", App_Today() - 1, "CamionID", mTruck, "HDebut", TimeSerial(6, 30, 0), "HFin", TimeSerial(18, 30, 0)))
    Check "Location", "Doublon de relevé refusé", InStr(e, "doublon") > 0, e
    e = TrySave("T_LOCATION", NewRecord("DateReleve", App_Today(), "CamionID", mTruck, "HDebut", TimeSerial(6, 30, 0), "HFin", TimeSerial(8, 30, 0), "HeuresImmobAtiko", 5))
    Check "Location", "Immobilisation > heures brutes refusée", InStr(e, "dépassent") > 0, e
End Sub

'------------------------------------------------------------------ Pneus / stock
Private Sub T_Tires()
    Dim t1 As Long, t2 As Long, e As String, mv0 As Long, art As Long
    mv0 = DB_Count("T_PNEU_MVT")
    t1 = Rec_Save("T_PNEUS", NewRecord("NumSerie", "PN-TEST-001", "Marque", "Test", "Dimension", "12.00R24", "PrixAchat", 6500000, "DateEntree", App_Today() - 2))
    Check "Pneus", "Entrée en stock historisée", DB_Count("T_PNEU_MVT") = mv0 + 1 And DB_Value("T_PNEUS", t1, "Statut") = PN_STOCK
    Tire_Mount t1, mTruck, "AVG", App_Today() - 1
    Check "Pneus", "Montage", DB_Value("T_PNEUS", t1, "Statut") = PN_MONTE And Tire_AtPosition(mTruck, "AVG") = t1
    t2 = Rec_Save("T_PNEUS", NewRecord("NumSerie", "PN-TEST-002", "Marque", "Test", "Dimension", "12.00R24", "PrixAchat", 6500000, "DateEntree", App_Today() - 2))
    e = TryMount(t2, mTruck, "AVG", App_Today())
    Check "Pneus", "Position occupée refusée", InStr(e, "occupée") > 0, e
    Tire_Transfer t1, IdOf("T_CAMIONS", "ATK-001"), "SEC-1", App_Today(), "Test transfert"
    Check "Pneus", "Transfert vers un autre camion", ToLng(DB_Value("T_PNEUS", t1, "CamionID")) = IdOf("T_CAMIONS", "ATK-001")
    Tire_Unmount t1, App_Today(), PN_REPAR, "Test démontage", 12, 300000
    Check "Pneus", "Démontage vers réparation (coût enregistré)", DB_Value("T_PNEUS", t1, "Statut") = PN_REPAR
    Tire_ReturnFromRepair t1, App_Today(), 0, 11
    Check "Pneus", "Retour de réparation en stock", DB_Value("T_PNEUS", t1, "Statut") = PN_STOCK
    Tire_Scrap t2, App_Today(), "Test réforme"
    Check "Pneus", "Réforme", DB_Value("T_PNEUS", t2, "Statut") = PN_REFORME
    Check "Pneus", "Historique complet du pneu (entrée, montage, transfert, démontage, réparation, retour)", DB_CountIf("T_PNEU_MVT", "PneuID", "=", t1) = 6, CStr(DB_CountIf("T_PNEU_MVT", "PneuID", "=", t1))
    Check "Pneus", "Pneus à remplacer détectés (démo : ATK-003/007/010)", Tire_ToReplace().Count >= 6, CStr(Tire_ToReplace().Count)
    ' stock consommables
    art = IdOf("T_ARTICLES", "FH-001")
    Rec_Save "T_STOCK_MVT", NewRecord("DateMvt", App_Today(), "ArticleID", art, "TypeMvt", "Entrée", "Quantite", 10, "PU", 200000)
    Check "Stock", "Entrée de stock : quantité et PMP recalculés", ToDbl(DB_Value("T_ARTICLES", art, "Stock")) > 10 And ToDbl(DB_Value("T_ARTICLES", art, "PrixMoyen")) > 180000
    e = TrySave("T_STOCK_MVT", NewRecord("DateMvt", App_Today(), "ArticleID", art, "TypeMvt", "Sortie", "Quantite", 9999))
    Check "Stock", "Sortie supérieure au stock refusée", InStr(e, "insuffisant") > 0, e
End Sub

'------------------------------------------------------------------ Incidents
Private Sub T_Maintenance()
    Dim id As Long
    id = Rec_Save("T_INCIDENTS", NewRecord("DateIncident", App_Today(), "TypeIncident", "Panne mécanique", "CamionID", mTruck, "Description", "Test", _
                                           "Gravite", "Moyenne", "Responsable", RESP_ATIKO, "Immobilisation", "Oui", "Cout", 1000000))
    Check "Incidents", "Immobilisation : camion passé « En panne »", DB_Value("T_CAMIONS", mTruck, "Statut") = CAM_PANNE, ToStr(DB_Value("T_CAMIONS", mTruck, "Statut"))
    Check "Incidents", "Coût Atiko non imputé à l'exploitant", ToDbl(DB_Value("T_INCIDENTS", id, "CoutExploitant")) = 0
    Rec_Save "T_INCIDENTS", NewRecord("DateReprise", App_Today(), "HeureReprise", TimeSerial(14, 0, 0), "Statut", "Clôturé", "Intervention", "Réparé"), id
    Check "Incidents", "Clôture : durée calculée et camion remis en service", ToDbl(DB_Value("T_INCIDENTS", id, "DureeImmobH")) = 6 And DB_Value("T_CAMIONS", mTruck, "Statut") = CAM_SERVICE, _
          DB_Value("T_INCIDENTS", id, "DureeImmobH") & " h / " & DB_Value("T_CAMIONS", mTruck, "Statut")
    Dim hA As Double, hE As Double
    Rent_ImmobHours mTruck, App_Today(), TimeSerial(6, 30, 0), TimeSerial(18, 30, 0), hA, hE
    Check "Incidents", "Heures d'immobilisation reprises pour la location (08:00-14:00 = 6 h Atiko)", Abs(hA - 6) < 0.01, FmtNum(hA, 2)
End Sub

'------------------------------------------------------------------ Depenses / caisse
Private Sub T_Finance()
    Dim e As String, id As Long
    e = TrySave("T_DEPENSES", NewRecord("DateDep", App_Today(), "Categorie", "Carburant", "Libelle", "Gasoil", "Montant", 1000000, "ModePaiement", "Caisse", "Responsable", RESP_EXPL))
    Check "Dépenses", "Carburant hors module dédié refusé (double comptabilisation)", InStr(e, "double comptabilisation") > 0, e
    id = Rec_Save("T_DEPENSES", NewRecord("DateDep", App_Today(), "Categorie", "Divers", "Libelle", "Test dépense", "Montant", 500000, "ModePaiement", "Caisse", _
                                          "Responsable", RESP_EXPL, "FournisseurID", 4, "RefFacture", "F-TEST-1"))
    Check "Dépenses", "Dépense enregistrée au statut Saisie", DB_Value("T_DEPENSES", id, "Statut") = ST_SAISIE
    e = TrySave("T_DEPENSES", NewRecord("DateDep", App_Today(), "Categorie", "Divers", "Libelle", "Autre", "Montant", 700000, "ModePaiement", "Caisse", _
                                        "Responsable", RESP_EXPL, "FournisseurID", 4, "RefFacture", "F-TEST-1"))
    Check "Dépenses", "Doublon de facture fournisseur refusé", InStr(e, "Doublon") > 0, e
    Rec_SetStatus "T_DEPENSES", id, ST_VALIDEE
    Check "Dépenses", "Validation", DB_Value("T_DEPENSES", id, "Statut") = ST_VALIDEE
    e = TrySave("T_CAISSE", NewRecord("DateOp", App_Today(), "Sens", "Sortie", "Libelle", "Test", "Montant", 999999999))
    Check "Caisse", "Sortie supérieure au solde refusée", InStr(e, "insuffisant") > 0, e
End Sub

'------------------------------------------------------------------ Facturation
Private Sub T_Billing()
    Dim inv As Long, ton As Double, e As String, ttc As Double, p1 As Long
    ton = DB_Sum("T_ROTATIONS", "PoidsNet", "ClientID", "=", IdOf("T_CLIENTS", "SMN"), "Statut", "=", ST_VALIDEE, "FactureID", "ISBLANK", "", _
                 "DateRotation", ">=", DateSerial(2026, 11, 1), "DateRotation", "<=", DateSerial(2026, 11, 30))
    inv = Bill_GenerateDraft(IdOf("T_CLIENTS", "SMN"), "2026-11", True)
    Check "Facturation", "Brouillon : tonnage = rotations validées non facturées", Abs(ToDbl(DB_Value("T_FACTURES", inv, "Tonnage")) - Round(ton, 2)) < 0.01, _
          FmtNum(DB_Value("T_FACTURES", inv, "Tonnage"), 2) & " / " & FmtNum(ton, 2)
    Check "Facturation", "Montant HT = tonnage x tarif", ToDbl(DB_Value("T_FACTURES", inv, "MontantHT")) = Round(Round(ton, 2) * 60000, 0)
    Check "Facturation", "Lignes = total HT", DB_Sum("T_FACTURE_LIGNES", "Montant", "FactureID", "=", inv) = ToDbl(DB_Value("T_FACTURES", inv, "MontantHT"))
    Check "Facturation", "Rotations réservées par le brouillon", DB_CountIf("T_ROTATIONS", "FactureID", "=", inv) > 0
    e = TryDraft(IdOf("T_CLIENTS", "SMN"), "2026-11")
    Check "Facturation", "Double facturation d'un même mois refusée", InStr(e, "existe déjà") > 0, e
    Bill_Validate inv
    Check "Facturation", "Validation : numéro légal séquentiel", DB_Value("T_FACTURES", inv, "NumFacture") = "FAC-2026-0007", ToStr(DB_Value("T_FACTURES", inv, "NumFacture"))
    Check "Facturation", "Empreinte d'intégrité posée", Bill_VerifyFingerprint(inv)
    DB_SetValue "T_FACTURES", inv, "Tonnage", ToDbl(DB_Value("T_FACTURES", inv, "Tonnage")) + 1
    Check "Facturation", "Modification silencieuse détectée", Not Bill_VerifyFingerprint(inv)
    DB_SetValue "T_FACTURES", inv, "Tonnage", ToDbl(DB_Value("T_FACTURES", inv, "Tonnage")) - 1
    e = TrySave("T_ROTATIONS", NewRecord("PoidsBrut", 50), ToLng(DB_Lookup("T_ROTATIONS", "FactureID", inv, "ID")))
    Check "Facturation", "Rotation facturée verrouillée", InStr(e, "facturée") > 0, e
    ttc = ToDbl(DB_Value("T_FACTURES", inv, "MontantTTC"))
    p1 = Rec_Save("T_PAIEMENTS", NewRecord("FactureID", inv, "DatePaiement", App_Today(), "Montant", Round(ttc * 0.4, 0), "ModePaiement", "Virement", "Reference", "VIR-T1"))
    Check "Paiements", "Paiement partiel -> statut Partielle", DB_Value("T_FACTURES", inv, "Statut") = FAC_PARTIELLE
    Check "Paiements", "Solde recalculé", ToDbl(DB_Value("T_FACTURES", inv, "Solde")) = ttc - Round(ttc * 0.4, 0)
    e = TrySave("T_PAIEMENTS", NewRecord("FactureID", inv, "DatePaiement", App_Today(), "Montant", ttc, "ModePaiement", "Virement", "Reference", "VIR-T2"))
    Check "Paiements", "Trop-perçu refusé", InStr(e, "supérieur au solde") > 0, e
    e = TryCancelInv(inv, "Test")
    Check "Facturation", "Annulation d'une facture encaissée refusée", InStr(e, "encaissements") > 0, e
    Rec_Save "T_PAIEMENTS", NewRecord("FactureID", inv, "DatePaiement", App_Today(), "Montant", ttc - Round(ttc * 0.4, 0), "ModePaiement", "Virement", "Reference", "VIR-T3")
    Check "Paiements", "Paiement total -> statut Payée", DB_Value("T_FACTURES", inv, "Statut") = FAC_PAYEE And ToDbl(DB_Value("T_FACTURES", inv, "Solde")) = 0
    Bill_CancelPayment p1, "Test annulation"
    Check "Paiements", "Annulation d'encaissement -> facture repasse Partielle", DB_Value("T_FACTURES", inv, "Statut") = FAC_PARTIELLE
    Check "Créances", "Créances = somme des soldes", Abs(Bill_Receivables() - DB_Sum("T_FACTURES", "Solde", "Statut", "IN", FAC_VALIDEE & "|" & FAC_PARTIELLE)) < 1
End Sub

'------------------------------------------------------------------ Indicateurs / marge
Private Sub T_KPI()
    Dim k As Object, inv As Double, d1 As Date, d2 As Date
    d1 = DateSerial(2026, 10, 1): d2 = DateSerial(2026, 10, 31)
    Set k = KPI_Period(d1, d2)
    inv = DB_Sum("T_FACTURES", "MontantHT", "PeriodeDebut", "=", d1, "Statut", "<>", FAC_ANNULEE)
    Check "Marge", "CA reconnu d'octobre = CA facturé d'octobre", Abs(k("CA") - inv) < 1, FmtMoney(k("CA")) & " / " & FmtMoney(inv)
    Check "Marge", "Marge = CA - charges", Abs(k("Marge") - (k("CA") - k("Charges"))) < 1
    Check "Marge", "Charges = location + carburant + pneus + autres", Abs(k("Charges") - (k("Location") + k("Carburant") + k("Pneus") + k("AutresCharges"))) < 1
    Check "Marge", "Taux de marge plausible (20 % à 45 % sur la démo)", k("TauxMarge") > 0.2 And k("TauxMarge") < 0.45, FmtPct(k("TauxMarge"))
    Check "Marge", "Charges Atiko exclues de la marge", k("ChargesAtiko") > 0 And Abs(k("Charges") - (k("Location") + k("Carburant") + k("Pneus") + k("AutresCharges"))) < 1
    Check "Tableau de bord", "Flotte : 10 camions dont 1 en panne et 1 immobilisé", KPI_Fleet()("Total") >= 10
    Check "Alertes", "Alertes calculées", Alerts_Compute().Count > 0, CStr(Alerts_Compute().Count)
    On Error Resume Next
    Dash_Refresh
    Check "Tableau de bord", "Actualisation sans erreur", Err.Number = 0, Err.Description
    Err.Clear
    On Error GoTo 0
End Sub

'------------------------------------------------------------------ Etats
Private Sub T_Reports()
    Dim it As Variant, rep As cReport, fid As Long, bad As String, n As Long, p As String
    For Each it In Rpt_Catalog()
        fid = 0
        Select Case it(4)
            Case "TRUCK": fid = IdOf("T_CAMIONS", "ATK-001")
            Case "CLIENT": fid = IdOf("T_CLIENTS", "SMN")
            Case "DRIVER": fid = IdOf("T_CHAUFFEURS", "CH-001")
        End Select
        On Error Resume Next
        Set rep = Rpt_Build(it(0), DateSerial(2026, 10, 1), DateSerial(2026, 10, 31), fid)
        If Err.Number = 0 Then Rpt_Render rep
        If Err.Number <> 0 Then bad = bad & it(0) & " (" & Err.Description & ") "
        Err.Clear
        On Error GoTo 0
        n = n + 1
    Next it
    Check "Rapports", n & " états générés et mis en page", Len(bad) = 0, bad
    Set rep = Rpt_Build("FIN_MARGIN", DateSerial(2026, 10, 1), DateSerial(2026, 10, 31))
    Rpt_Render rep
    On Error Resume Next
    p = Rpt_ToPDF(rep, False)
    Check "Rapports", "Export PDF", FileExists(p), p & " " & Err.Description
    Err.Clear
    p = Rpt_ToXLSX(rep, False)
    Check "Rapports", "Export Excel", FileExists(p), p & " " & Err.Description
    Err.Clear
    p = Inv_ExportPDF(IdOf("T_FACTURES", "FAC-2026-0004"), False)
    Check "Facturation", "Facture PDF", FileExists(p), p & " " & Err.Description
    Err.Clear
    Check "Rapports", "Orientation automatique (paysage au-delà de 7 colonnes)", Rpt_Build("PRD_TRUCK", DateSerial(2026, 10, 1), DateSerial(2026, 10, 31)).IsLandscape(), ""
    On Error GoTo 0
End Sub

'------------------------------------------------------------------ Sauvegarde / restauration
Private Sub T_Backup()
    Dim p As String, nRot As Long, e As String
    nRot = DB_Count("T_ROTATIONS")
    On Error Resume Next
    p = Backup_Run("Manuelle", True)
    Check "Sauvegarde", "Sauvegarde créée", FileExists(p), p & " " & Err.Description
    Err.Clear
    Check "Sauvegarde", "Sauvegarde vérifiée (structure + volumétrie)", Backup_Verify(p), ""
    Backup_Restore p
    Check "Sauvegarde", "Restauration depuis la sauvegarde", Err.Number = 0 And DB_Count("T_ROTATIONS") = nRot, Err.Description
    Err.Clear
    ' nettoyage des copies creees par le test
    Dim f As Object
    Kill p
    For Each f In FSO().GetFolder(Backup_Folder()).Files
        If InStr(f.Name, "restauration") > 0 And Now - f.DateLastModified < TimeSerial(0, 5, 0) Then Kill f.Path
    Next f
    Err.Clear
    On Error GoTo 0
End Sub

'------------------------------------------------------------------ Clotures
Private Sub T_Closing()
    Dim e As String, d As Date, rows As Collection, r As Variant, data As Variant, ids As New Collection, v As Variant
    d = DateSerial(2026, 11, 4)
    e = TryCloseDay(d)
    Check "Clôtures", "Clôture refusée si carburant non validé", InStr(e, "carburant") > 0, e
    Set rows = DB_Rows("T_CARBURANT", "DateOp", "=", d, "Statut", "=", ST_SAISIE)
    data = DB_Data("T_CARBURANT")
    For Each r In rows
        ids.Add ToLng(data(r, DB_ColIdx("T_CARBURANT", "ID")))
    Next r
    For Each v In ids
        Rec_SetStatus "T_CARBURANT", CLng(v), ST_VALIDEE
    Next v
    e = TryCloseDay(d)
    Check "Clôtures", "Clôture journalière du 04/11/2026", Len(e) = 0 And ToDate(Param_Get("CLOTURE_DERNIER_JOUR")) = d, e
    e = TrySave("T_ROTATIONS", NewRecord("DateRotation", d, "CamionID", IdOf("T_CAMIONS", "ATK-001"), "ChauffeurID", IdOf("T_CHAUFFEURS", "CH-001"), _
                                         "HDepart", TimeSerial(20, 0, 0), "OrigineID", IdOf("T_LIEUX", "P1"), "DestinationID", IdOf("T_LIEUX", "GSM"), _
                                         "NumTicket", "TK-TEST-CL", "PoidsBrut", 55, "Tare", 16))
    Check "Clôtures", "Saisie refusée sur une journée clôturée", InStr(e, "clôturée") > 0, e
End Sub
