Attribute VB_Name = "mRpt_Catalog"
'==============================================================================
' MINING TRANSIT 360 - mRpt_Catalog
' Catalogue des etats et constructeurs de donnees.
' Chaque etat : cle | categorie | titre | periode requise | filtre | description
' Filtre : "" aucun, TRUCK camion, CLIENT client, DRIVER chauffeur.
'==============================================================================
Option Explicit

Public Function Rpt_Catalog() As Collection
    Dim c As New Collection
    ' Production
    c.Add Array("PRD_DAY", "Production", "Rapport journalier de production", True, "", "Production du jour par camion et détail des rotations")
    c.Add Array("PRD_WEEK", "Production", "Rapport hebdomadaire", True, "", "Tonnage par jour et par camion sur la semaine")
    c.Add Array("PRD_MONTH", "Production", "Rapport mensuel de production", True, "", "Production par jour, camion, client ; objectif vs réalisé")
    c.Add Array("PRD_YEAR", "Production", "Rapport annuel", True, "", "Tonnage, rotations et CA par mois")
    c.Add Array("PRD_TRUCK", "Production", "Production par camion", True, "", "Rotations, tonnage, CA, coûts et marge par camion")
    c.Add Array("PRD_DRIVER", "Production", "Production par chauffeur", True, "", "Performance des chauffeurs")
    c.Add Array("PRD_ROTATIONS", "Production", "Historique des rotations", True, "TRUCK", "Détail des rotations (filtre camion optionnel)")
    c.Add Array("PRD_VALREJ", "Production", "Tonnages validés et rejetés", True, "", "Validation, rejets et motifs")
    c.Add Array("PRD_OBJ", "Production", "Objectifs / réalisations", False, "", "Comparaison mensuelle des objectifs")
    c.Add Array("PRD_WAIT", "Production", "Temps d'attente et heures improductives", True, "", "Analyse par camion")
    ' Location
    c.Add Array("LOC_HOURS", "Location", "Relevé des heures par camion", True, "TRUCK", "Heures brutes, attente, immobilisations, retenues")
    c.Add Array("LOC_COST", "Location", "Coût de location", True, "", "Coût de location par camion")
    c.Add Array("LOC_IMMO", "Location", "Immobilisations", True, "", "Incidents avec immobilisation et responsabilités")
    c.Add Array("LOC_DISPO", "Location", "Disponibilité de la flotte", True, "", "Taux de disponibilité par camion")
    ' Carburant
    c.Add Array("FUEL_CONSO", "Carburant", "Consommation par camion", True, "", "Litres, heures, L/h vs référence")
    c.Add Array("FUEL_COST", "Carburant", "Coût du carburant", True, "", "Distributions, approvisionnements, stock cuve")
    c.Add Array("FUEL_COMPARE", "Carburant", "Comparatif de consommation", True, "", "Période vs période précédente")
    c.Add Array("FUEL_TON", "Carburant", "Coût carburant par tonne", True, "", "GNF/t et L/t par camion")
    c.Add Array("FUEL_ANOM", "Carburant", "Anomalies carburant", True, "", "Surconsommations, doublons, quantités aberrantes")
    ' Pneumatiques
    c.Add Array("TIRE_STOCK", "Pneumatiques", "Stock de pneumatiques", False, "", "Pneus en stock et en réparation")
    c.Add Array("TIRE_MVT", "Pneumatiques", "Mouvements de pneus", True, "", "Entrées, montages, transferts, réparations, réformes")
    c.Add Array("TIRE_TRUCK", "Pneumatiques", "Pneus montés par camion", False, "TRUCK", "Positions et usure par camion")
    c.Add Array("TIRE_WEAR", "Pneumatiques", "Usure et prévision des besoins", False, "", "Heures, profondeur, date prévisionnelle de remplacement")
    c.Add Array("TIRE_COST", "Pneumatiques", "Coût pneumatique par camion", True, "", "Réparations et achats")
    c.Add Array("STOCK_ART", "Pneumatiques", "Stock de consommables", False, "", "Valorisation au PMP et alertes")
    ' Finances
    c.Add Array("FIN_EXP", "Finances", "Dépenses", True, "", "Par catégorie et responsable contractuel")
    c.Add Array("FIN_INV", "Finances", "Factures émises", True, "", "Factures de la période")
    c.Add Array("FIN_PAY", "Finances", "Encaissements", True, "", "Règlements reçus")
    c.Add Array("FIN_REC", "Finances", "Créances clients", False, "", "Soldes dus par client")
    c.Add Array("FIN_LATE", "Finances", "Retards de paiement", False, "", "Factures échues et niveau de relance suggéré")
    c.Add Array("FIN_AGING", "Finances", "Balance âgée", False, "", "Créances par ancienneté")
    c.Add Array("FIN_MARGIN", "Finances", "Marge opérationnelle", True, "", "Compte d'exploitation simplifié")
    c.Add Array("FIN_CASH", "Finances", "Journal de caisse", True, "", "Mouvements et solde")
    c.Add Array("FIN_ADV", "Finances", "Avances au personnel", False, "", "Avances en cours")
    c.Add Array("CLIENT_SIT", "Finances", "Situation client", False, "CLIENT", "Relevé de compte client")
    ' Direction
    c.Add Array("DIR_KPI", "Direction", "Tableau de bord mensuel", True, "", "Indicateurs clés de la période")
    c.Add Array("DIR_GENERAL", "Direction", "Rapport général de gestion", True, "", "Synthèse production, finances, flotte, alertes")
    c.Add Array("DIR_RENTA", "Direction", "Analyse de rentabilité", True, "", "Structure des coûts et marge par camion")
    c.Add Array("DIR_PERF", "Direction", "Rapport de performance", True, "", "Classement camions et chauffeurs")
    c.Add Array("DIR_ACTIVITY", "Direction", "Rapport d'activité", True, "", "Faits marquants de la période")
    ' Fiches
    c.Add Array("CARD_TRUCK", "Fiches", "Fiche camion", True, "TRUCK", "Fiche détaillée et statistiques d'un camion")
    c.Add Array("CARD_DRIVER", "Fiches", "Fiche chauffeur", True, "DRIVER", "Fiche détaillée d'un chauffeur")
    c.Add Array("CHECK_COH", "Fiches", "Contrôle de cohérence", False, "", "Rapport d'intégrité des données")
    Set Rpt_Catalog = c
End Function

Public Function Rpt_Info(ByVal key As String) As Variant
    Dim it As Variant
    For Each it In Rpt_Catalog()
        If it(0) = key Then Rpt_Info = it: Exit Function
    Next it
    Fail "État inconnu : " & key
End Function

' Lance un etat avec selection de periode / filtre puis choix de sortie
Public Sub Rpt_RunByKey(ByVal key As String)
    Dim info As Variant, d1 As Date, d2 As Date, fid As Long, mode As String, rep As cReport, choice As String
    On Error GoTo EH
    Sec_Require "RAPPORTS", "I"
    info = Rpt_Info(key)
    If info(3) Then
        d1 = MonthStart(App_Today()): d2 = App_Today()
        If key = "PRD_DAY" Then d1 = App_Today(): d2 = App_Today()
        If Not UI_AskPeriod(d1, d2, info(2)) Then Exit Sub
    End If
    If Len(info(4)) > 0 Then
        choice = UI_Pick(info(2), Choose(InStr("TRUCKCLIENTDRIVER", info(4)) \ 5 + 1, "Camion", "Client", "Chauffeur"), Rpt_FilterChoices(info(4), key <> "CARD_TRUCK" And key <> "CARD_DRIVER" And key <> "CLIENT_SIT"))
        If Len(choice) = 0 Then Exit Sub
        fid = Rpt_FilterId(info(4), choice)
    End If
    Set rep = Rpt_Build(key, d1, d2, fid)
    mode = Rpt_AskOutput(rep.Title)
    If Len(mode) > 0 Then Rpt_Output rep, mode
    Exit Sub
EH:
    Log_Error "Rapport " & key, Err.Number, Err.Description
End Sub

Public Function Rpt_FilterChoices(ByVal ftype As String, ByVal allowAll As Boolean) As Variant
    Dim items As Variant
    Select Case ftype
        Case "TRUCK": items = Schema_FKChoices(Schema_Field("T_ROTATIONS", "CamionID"))
        Case "CLIENT": items = Schema_FKChoices(Schema_Field("T_ROTATIONS", "ClientID"))
        Case "DRIVER": items = Schema_FKChoices(Schema_Field("T_ROTATIONS", "ChauffeurID"))
    End Select
    If allowAll Then Rpt_FilterChoices = Array_Prepend(items, "(Tous)") Else Rpt_FilterChoices = items
End Function

Public Function Rpt_FilterId(ByVal ftype As String, ByVal pick As String) As Long
    If pick = "(Tous)" Or Len(pick) = 0 Then Exit Function
    Select Case ftype
        Case "TRUCK": Rpt_FilterId = IdOf("T_CAMIONS", pick)
        Case "CLIENT": Rpt_FilterId = IdOf("T_CLIENTS", pick)
        Case "DRIVER": Rpt_FilterId = IdOf("T_CHAUFFEURS", pick)
    End Select
End Function

'==============================================================================
' Utilitaires d'agregation
'==============================================================================
Private Function Agg(ByVal tableName As String, ByVal keyCol As String, ByVal valCol As String, ByVal crit As Variant) As Object
    Dim rows As Collection, r As Variant, data As Variant, d As Object, ck As Long, cv As Long, k As String
    Set d = NewDict()
    Set rows = DB_RowsA(tableName, crit)
    data = DB_Data(tableName)
    ck = DB_ColIdx(tableName, keyCol)
    If Len(valCol) > 0 Then cv = DB_ColIdx(tableName, valCol)
    For Each r In rows
        k = ToStr(data(r, ck))
        If cv > 0 Then d(k) = d(k) + ToDbl(data(r, cv)) Else d(k) = d(k) + 1
    Next r
    Set Agg = d
End Function

Private Function Pick(ByVal d As Object, ByVal k As Variant) As Double
    If d.Exists(ToStr(k)) Then Pick = d(ToStr(k))
End Function

Private Function TruckIds(Optional ByVal onlyId As Long = 0) As Collection
    Dim data As Variant, r As Long, c As New Collection
    data = DB_Data("T_CAMIONS")
    For r = 1 To ArrayRows(data)
        If onlyId = 0 Or ToLng(data(r, DB_ColIdx("T_CAMIONS", "ID"))) = onlyId Then c.Add ToLng(data(r, DB_ColIdx("T_CAMIONS", "ID")))
    Next r
    Set TruckIds = c
End Function

Private Function PeriodCrit(ByVal dateCol As String, ByVal d1 As Date, ByVal d2 As Date, ParamArray extra() As Variant) As Variant
    Dim out() As Variant, i As Long, n As Long
    n = UBound(extra) - LBound(extra) + 1
    ReDim out(0 To 5 + n)
    out(0) = dateCol: out(1) = ">=": out(2) = d1
    out(3) = dateCol: out(4) = "<=": out(5) = d2
    For i = 0 To n - 1
        out(6 + i) = extra(LBound(extra) + i)
    Next i
    PeriodCrit = out
End Function

Private Function NewRep(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date) As cReport
    Dim rep As cReport, info As Variant
    info = Rpt_Info(key)
    Set rep = New cReport
    rep.Title = info(2)
    rep.Category = info(1)
    rep.FileStem = key
    If info(3) Then rep.SetPeriod d1, d2 Else rep.Subtitle = "Situation au " & FmtDate(App_Today())
    Set NewRep = rep
End Function

Private Function Ratio(ByVal a As Double, ByVal b As Double) As Double
    If b <> 0 Then Ratio = a / b
End Function

'==============================================================================
' CONSTRUCTEURS
'==============================================================================
Public Function Rpt_Build(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, Optional ByVal fid As Long = 0) As cReport
    Dim rep As cReport
    Status "Préparation de l'état..."
    Select Case Left$(key, 3)
        Case "PRD": Set rep = BuildProduction(key, d1, d2, fid)
        Case "LOC": Set rep = BuildRental(key, d1, d2, fid)
        Case "FUE": Set rep = BuildFuel(key, d1, d2, fid)
        Case "TIR", "STO": Set rep = BuildTires(key, d1, d2, fid)
        Case "FIN", "CLI": Set rep = BuildFinance(key, d1, d2, fid)
        Case "DIR": Set rep = BuildDirection(key, d1, d2, fid)
        Case "CAR", "CHE": Set rep = BuildCards(key, d1, d2, fid)
        Case Else: Fail "État non implémenté : " & key
    End Select
    Status "État prêt"
    Set Rpt_Build = rep
End Function

'------------------------------------------------------------------ Production
Private Function BuildProduction(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, ByVal fid As Long) As cReport
    Dim rep As cReport, out() As Variant, n As Long, i As Long, cam As Variant, d As Date, ton As Object, rot As Object, crit As Variant
    Dim data As Variant, rows As Collection, r As Variant, k As Object, ca As Object
    Set rep = NewRep(key, d1, d2)
    crit = PeriodCrit("DateRotation", d1, d2, "Statut", "=", ST_VALIDEE)
    Select Case key
        Case "PRD_DAY", "PRD_WEEK", "PRD_MONTH", "PRD_TRUCK"
            Set k = KPI_Period(d1, d2)
            rep.AddInfo "Synthèse", Array("Tonnage validé", FmtNum(k("Tonnage"), 2) & " t", "Rotations validées", k("Rotations"), _
                "Tonnage moyen / rotation", FmtNum(Ratio(k("Tonnage"), k("Rotations")), 2) & " t", "Rotations en attente", k("RotationsAttente"), _
                "CA reconnu", FmtMoney(k("CA")), "Objectif du mois", IIf(k("Objectif") > 0, FmtNum(k("Objectif"), 0) & " t", "non défini"))
            ' par camion
            Set ton = Agg("T_ROTATIONS", "CamionID", "PoidsNet", crit)
            Set rot = Agg("T_ROTATIONS", "CamionID", "", crit)
            Dim liters As Object, fuel As Object, loc As Object, hrs As Object
            Set liters = Agg("T_CARBURANT", "CamionID", "Litres", PeriodCrit("DateOp", d1, d2, "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Set fuel = Agg("T_CARBURANT", "CamionID", "Montant", PeriodCrit("DateOp", d1, d2, "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Set loc = Agg("T_LOCATION", "CamionID", "Montant", PeriodCrit("DateReleve", d1, d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Set hrs = Agg("T_LOCATION", "CamionID", "HeuresRetenues", PeriodCrit("DateReleve", d1, d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Dim trucks As Collection
            Set trucks = TruckIds()
            ReDim out(1 To trucks.Count, 1 To 10)
            For Each cam In trucks
                n = n + 1
                out(n, 1) = CodeOf("T_CAMIONS", cam)
                out(n, 2) = CodeOf("T_CHAUFFEURS", Fleet_DriverFor(cam, d2))
                out(n, 3) = Pick(rot, cam)
                out(n, 4) = Pick(ton, cam)
                out(n, 5) = Ratio(Pick(ton, cam), Pick(rot, cam))
                out(n, 6) = Ops_RevenueFor("CamionID", cam, d1, d2)
                out(n, 7) = Pick(liters, cam)
                out(n, 8) = Pick(fuel, cam) + Pick(loc, cam)
                out(n, 9) = Ratio(Pick(fuel, cam) + Pick(loc, cam), Pick(ton, cam))
                out(n, 10) = out(n, 6) - out(n, 8)
            Next cam
            rep.AddTable "Production et coûts directs par camion", "Camion|Chauffeur|Rotations|Tonnage (t)|t / rotation|CA reconnu|Gasoil (L)|Carburant + location|Coût direct / t|Contribution", _
                         "T|T|N0|N2|N2|M|N0|M|M|M", out, "L|0|1|1|0|1|1|1|0|1"
            If key = "PRD_DAY" Then
                rep.AddTable "Détail des rotations", "N° rotation|Camion|Chauffeur|Départ|Arrivée|Origine|Destination|Ticket|Net (t)|Statut", _
                             "T|T|T|H|H|T|T|T|N2|T", RotationRows(d1, d2, 0, ""), "L|0|0|0|0|0|0|0|1|0"
            Else
                ' par jour
                n = 0
                ReDim out(1 To d2 - d1 + 1, 1 To 4)
                For d = d1 To d2
                    n = n + 1
                    out(n, 1) = d
                    out(n, 2) = DayNameFR(d)
                    out(n, 3) = DB_CountIf("T_ROTATIONS", "DateRotation", "=", d, "Statut", "=", ST_VALIDEE)
                    out(n, 4) = DB_Sum("T_ROTATIONS", "PoidsNet", "DateRotation", "=", d, "Statut", "=", ST_VALIDEE)
                Next d
                rep.AddTable "Production par jour", "Date|Jour|Rotations|Tonnage (t)", "D|T|N0|N2", out, "L|0|1|1"
                ' par client
                Set ton = Agg("T_ROTATIONS", "ClientID", "PoidsNet", crit)
                Set rot = Agg("T_ROTATIONS", "ClientID", "", crit)
                ReDim out(1 To DB_Count("T_CLIENTS"), 1 To 5)
                data = DB_Data("T_CLIENTS")
                For i = 1 To ArrayRows(data)
                    out(i, 1) = data(i, DB_ColIdx("T_CLIENTS", "Code"))
                    out(i, 2) = data(i, DB_ColIdx("T_CLIENTS", "RaisonSociale"))
                    out(i, 3) = Pick(rot, data(i, DB_ColIdx("T_CLIENTS", "ID")))
                    out(i, 4) = Pick(ton, data(i, DB_ColIdx("T_CLIENTS", "ID")))
                    out(i, 5) = Ops_RevenueFor("ClientID", data(i, DB_ColIdx("T_CLIENTS", "ID")), d1, d2)
                Next i
                rep.AddTable "Production par client", "Code|Client|Rotations|Tonnage (t)|CA reconnu", "T|T|N0|N2|M", out, "L|0|1|1|1"
            End If
        Case "PRD_YEAR"
            Dim m As Date
            n = 0
            m = MonthStart(d1)
            ReDim out(1 To 24, 1 To 7)
            Do While m <= d2 And n < 24
                n = n + 1
                out(n, 1) = MonthNameFR(m)
                out(n, 2) = DB_CountIf("T_ROTATIONS", "Statut", "=", ST_VALIDEE, "DateRotation", ">=", m, "DateRotation", "<=", MonthEnd(m))
                out(n, 3) = Ops_Tonnage(m, MonthEnd(m))
                out(n, 4) = ToDbl(DB_Lookup("T_OBJECTIFS", "Periode", MonthKey(m), "TonnageObjectif", 0))
                out(n, 5) = Ratio(out(n, 3), out(n, 4))
                out(n, 6) = Ops_RevenueFor("", Empty, m, MonthEnd(m))
                out(n, 7) = Bill_Invoiced(m, MonthEnd(m))
                m = DateSerial(Year(m), Month(m) + 1, 1)
            Loop
            rep.AddTable "Activité mensuelle", "Mois|Rotations|Tonnage (t)|Objectif (t)|Réalisation|CA reconnu|CA facturé", "T|N0|N2|N0|P|M|M", Trim2D(out, n), "L|1|1|1|0|1|1"
        Case "PRD_DRIVER"
            Set ton = Agg("T_ROTATIONS", "ChauffeurID", "PoidsNet", crit)
            Set rot = Agg("T_ROTATIONS", "ChauffeurID", "", crit)
            Dim inc As Object, anom As Object, rej As Object
            Set inc = Agg("T_INCIDENTS", "ChauffeurID", "", PeriodCrit("DateIncident", d1, d2))
            Set anom = Agg("T_CARBURANT", "ChauffeurID", "", PeriodCrit("DateOp", d1, d2, "Anomalie", "NOTBLANK", ""))
            Set rej = Agg("T_ROTATIONS", "ChauffeurID", "", PeriodCrit("DateRotation", d1, d2, "Statut", "=", ST_REJETEE))
            data = DB_Data("T_CHAUFFEURS")
            ReDim out(1 To ArrayRows(data), 1 To 9)
            For i = 1 To ArrayRows(data)
                cam = data(i, DB_ColIdx("T_CHAUFFEURS", "ID"))
                out(i, 1) = data(i, DB_ColIdx("T_CHAUFFEURS", "Matricule"))
                out(i, 2) = data(i, DB_ColIdx("T_CHAUFFEURS", "Prenom")) & " " & data(i, DB_ColIdx("T_CHAUFFEURS", "Nom"))
                out(i, 3) = Pick(rot, cam)
                out(i, 4) = Pick(ton, cam)
                out(i, 5) = Ratio(Pick(ton, cam), Pick(rot, cam))
                out(i, 6) = Pick(rej, cam)
                out(i, 7) = Pick(inc, cam)
                out(i, 8) = Pick(anom, cam)
                out(i, 9) = Ops_RevenueFor("ChauffeurID", cam, d1, d2)
            Next i
            SortArray2D out, 4, True
            rep.AddTable "Performance des chauffeurs (classés par tonnage)", "Matricule|Nom|Rotations|Tonnage (t)|t / rotation|Rejets|Incidents|Anomalies gasoil|CA généré", _
                         "T|T|N0|N2|N2|N0|N0|N0|M", out, "L|0|1|1|0|1|1|1|1"
        Case "PRD_ROTATIONS"
            If fid > 0 Then rep.Filters = "Camion " & CodeOf("T_CAMIONS", fid)
            rep.AddTable "", "N° rotation|Date|Camion|Chauffeur|Départ|Origine|Destination|Ticket|Net (t)|Statut", _
                         "T|D|T|T|H|T|T|T|N2|T", RotationRows(d1, d2, fid, "LIST"), "L|0|0|0|0|0|0|0|1|0"
            rep.Signatures = False
        Case "PRD_VALREJ"
            ReDim out(1 To 4, 1 To 3)
            Dim sts As Variant
            sts = Array(ST_VALIDEE, ST_REJETEE, ST_ANNULEE, ST_SAISIE)
            For i = 0 To 3
                out(i + 1, 1) = sts(i)
                out(i + 1, 2) = DB_CountIf("T_ROTATIONS", "Statut", "=", sts(i), "DateRotation", ">=", d1, "DateRotation", "<=", d2)
                out(i + 1, 3) = DB_Sum("T_ROTATIONS", "PoidsNet", "Statut", "=", sts(i), "DateRotation", ">=", d1, "DateRotation", "<=", d2)
            Next i
            rep.AddTable "Répartition par statut", "Statut|Rotations|Tonnage (t)", "T|N0|N2", out, "L|1|1"
            Set rows = DB_Rows("T_ROTATIONS", "Statut", "IN", ST_REJETEE & "|" & ST_ANNULEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
            data = DB_Data("T_ROTATIONS")
            If rows.Count > 0 Then
                ReDim out(1 To rows.Count, 1 To 6)
                n = 0
                For Each r In rows
                    n = n + 1
                    out(n, 1) = data(r, DB_ColIdx("T_ROTATIONS", "NumRotation"))
                    out(n, 2) = data(r, DB_ColIdx("T_ROTATIONS", "DateRotation"))
                    out(n, 3) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_ROTATIONS", "CamionID")))
                    out(n, 4) = data(r, DB_ColIdx("T_ROTATIONS", "PoidsNet"))
                    out(n, 5) = data(r, DB_ColIdx("T_ROTATIONS", "Statut"))
                    out(n, 6) = data(r, DB_ColIdx("T_ROTATIONS", "MotifRejet"))
                Next r
                rep.AddTable "Rotations rejetées / annulées", "N° rotation|Date|Camion|Net (t)|Statut|Motif", "T|D|T|N2|T|T", out, "L|0|0|1|0|0"
            End If
        Case "PRD_OBJ"
            data = DB_Data("T_OBJECTIFS")
            ReDim out(1 To ArrayRows(data), 1 To 8)
            For i = 1 To ArrayRows(data)
                m = MonthFromKey(data(i, DB_ColIdx("T_OBJECTIFS", "Periode")))
                out(i, 1) = data(i, DB_ColIdx("T_OBJECTIFS", "Periode"))
                out(i, 2) = data(i, DB_ColIdx("T_OBJECTIFS", "TonnageObjectif"))
                out(i, 3) = Ops_Tonnage(m, MonthEnd(m))
                out(i, 4) = Ratio(out(i, 3), out(i, 2))
                out(i, 5) = data(i, DB_ColIdx("T_OBJECTIFS", "RotationsObjectif"))
                out(i, 6) = DB_CountIf("T_ROTATIONS", "Statut", "=", ST_VALIDEE, "DateRotation", ">=", m, "DateRotation", "<=", MonthEnd(m))
                out(i, 7) = data(i, DB_ColIdx("T_OBJECTIFS", "CAObjectif"))
                out(i, 8) = Ops_RevenueFor("", Empty, m, MonthEnd(m))
            Next i
            rep.AddTable "Objectifs et réalisations", "Période|Objectif (t)|Réalisé (t)|Taux|Objectif rotations|Rotations|Objectif CA|CA reconnu", "T|N0|N2|P|N0|N0|M|M", out, "L|1|1|0|1|1|1|1"
            rep.AddText "Le mois en cours est partiel : le taux de réalisation se lit au prorata des jours écoulés.", "note"
        Case "PRD_WAIT"
            Dim brut As Object, att As Object, imA As Object, imE As Object
            Set brut = Agg("T_LOCATION", "CamionID", "HeuresBrutes", PeriodCrit("DateReleve", d1, d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Set att = Agg("T_LOCATION", "CamionID", "HeuresAttente", PeriodCrit("DateReleve", d1, d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Set imA = Agg("T_LOCATION", "CamionID", "HeuresImmobAtiko", PeriodCrit("DateReleve", d1, d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Set imE = Agg("T_LOCATION", "CamionID", "HeuresImmobExploitant", PeriodCrit("DateReleve", d1, d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Set trucks = TruckIds()
            ReDim out(1 To trucks.Count, 1 To 8)
            n = 0
            For Each cam In trucks
                n = n + 1
                out(n, 1) = CodeOf("T_CAMIONS", cam)
                out(n, 2) = Pick(brut, cam)
                out(n, 3) = Pick(att, cam)
                out(n, 4) = Pick(imA, cam)
                out(n, 5) = Pick(imE, cam)
                out(n, 6) = out(n, 3) + out(n, 4) + out(n, 5)
                out(n, 7) = Ratio(out(n, 6), out(n, 2))
                out(n, 8) = Ratio(out(n, 3), out(n, 2)) * ToDbl(DB_Value("T_CAMIONS", cam, "TarifHoraire")) * out(n, 2)
            Next cam
            rep.AddTable "Heures improductives par camion", "Camion|Heures brutes|Attente|Immob. Atiko|Immob. exploitant|Total improductif|% improductif|Coût de l'attente", _
                         "T|N1|N1|N1|N1|N1|P|M", out, "L|1|1|1|1|1|0|1"
            rep.AddText "Le coût de l'attente = heures d'attente x tarif horaire (heures payées à Atiko sans production).", "note"
    End Select
    Set BuildProduction = rep
End Function

Private Function RotationRows(ByVal d1 As Date, ByVal d2 As Date, ByVal truckId As Long, ByVal layout As String) As Variant
    Dim rows As Collection, r As Variant, data As Variant, out() As Variant, n As Long
    If truckId > 0 Then
        Set rows = DB_Rows("T_ROTATIONS", "DateRotation", ">=", d1, "DateRotation", "<=", d2, "CamionID", "=", truckId)
    Else
        Set rows = DB_Rows("T_ROTATIONS", "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    End If
    If rows.Count = 0 Then RotationRows = Empty: Exit Function
    data = DB_Data("T_ROTATIONS")
    ReDim out(1 To rows.Count, 1 To 10)
    For Each r In rows
        n = n + 1
        out(n, 1) = data(r, DB_ColIdx("T_ROTATIONS", "NumRotation"))
        If layout = "LIST" Then
            out(n, 2) = data(r, DB_ColIdx("T_ROTATIONS", "DateRotation"))
            out(n, 3) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_ROTATIONS", "CamionID")))
            out(n, 4) = CodeOf("T_CHAUFFEURS", data(r, DB_ColIdx("T_ROTATIONS", "ChauffeurID")))
            out(n, 5) = TimeOr0(data(r, DB_ColIdx("T_ROTATIONS", "HDepart")))
        Else
            out(n, 2) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_ROTATIONS", "CamionID")))
            out(n, 3) = CodeOf("T_CHAUFFEURS", data(r, DB_ColIdx("T_ROTATIONS", "ChauffeurID")))
            out(n, 4) = TimeOr0(data(r, DB_ColIdx("T_ROTATIONS", "HDepart")))
            out(n, 5) = TimeOr0(data(r, DB_ColIdx("T_ROTATIONS", "HArrivee")))
        End If
        out(n, 6) = CodeOf("T_LIEUX", data(r, DB_ColIdx("T_ROTATIONS", "OrigineID")))
        out(n, 7) = CodeOf("T_LIEUX", data(r, DB_ColIdx("T_ROTATIONS", "DestinationID")))
        out(n, 8) = data(r, DB_ColIdx("T_ROTATIONS", "NumTicket"))
        out(n, 9) = data(r, DB_ColIdx("T_ROTATIONS", "PoidsNet"))
        out(n, 10) = data(r, DB_ColIdx("T_ROTATIONS", "Statut"))
    Next r
    RotationRows = out
End Function

Private Function Trim2D(ByVal a As Variant, ByVal n As Long) As Variant
    Dim out() As Variant, i As Long, j As Long
    If n = 0 Then Trim2D = Empty: Exit Function
    ReDim out(1 To n, 1 To UBound(a, 2))
    For i = 1 To n
        For j = 1 To UBound(a, 2)
            out(i, j) = a(i, j)
        Next j
    Next i
    Trim2D = out
End Function

'------------------------------------------------------------------ Location
Private Function BuildRental(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, ByVal fid As Long) As cReport
    Dim rep As cReport, out() As Variant, n As Long, cam As Variant, trucks As Collection, rows As Collection, r As Variant, data As Variant
    Dim crit As Variant, a1 As Object, a2 As Object, a3 As Object, a4 As Object, a5 As Object, a6 As Object
    Set rep = NewRep(key, d1, d2)
    crit = PeriodCrit("DateReleve", d1, d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)
    Set trucks = TruckIds(IIf(key = "LOC_HOURS", fid, 0))
    Select Case key
        Case "LOC_HOURS", "LOC_COST", "LOC_DISPO"
            Set a1 = Agg("T_LOCATION", "CamionID", "HeuresBrutes", crit)
            Set a2 = Agg("T_LOCATION", "CamionID", "HeuresAttente", crit)
            Set a3 = Agg("T_LOCATION", "CamionID", "HeuresImmobAtiko", crit)
            Set a4 = Agg("T_LOCATION", "CamionID", "HeuresImmobExploitant", crit)
            Set a5 = Agg("T_LOCATION", "CamionID", "HeuresRetenues", crit)
            Set a6 = Agg("T_LOCATION", "CamionID", "Montant", crit)
            ReDim out(1 To trucks.Count, 1 To 9)
            For Each cam In trucks
                n = n + 1
                out(n, 1) = CodeOf("T_CAMIONS", cam)
                out(n, 2) = DB_Value("T_CAMIONS", cam, "TarifHoraire")
                out(n, 3) = Pick(a1, cam)
                out(n, 4) = Pick(a2, cam)
                out(n, 5) = Pick(a3, cam)
                out(n, 6) = Pick(a4, cam)
                out(n, 7) = Pick(a5, cam)
                out(n, 8) = Pick(a6, cam)
                out(n, 9) = 1 - Ratio(Pick(a3, cam) + Pick(a4, cam), Pick(a1, cam))
            Next cam
            rep.AddInfo "Règles contractuelles appliquées", Array( _
                "Attente déduite", Param_Get("LOC_DEDUIRE_ATTENTE", "Non"), "Immobilisation Atiko déduite", Param_Get("LOC_DEDUIRE_IMMOB_ATIKO", "Oui"), _
                "Immobilisation exploitant déduite", Param_Get("LOC_DEDUIRE_IMMOB_EXPLOITANT", "Non"), "Minimum journalier (h)", Param_Get("LOC_HEURES_MIN_JOUR", "0"))
            rep.AddTable "Heures et coût de location par camion", "Camion|Tarif (GNF/h)|Heures brutes|Attente|Immob. Atiko|Immob. exploitant|Heures retenues|Montant (GNF)|Disponibilité", _
                         "T|M|N2|N2|N2|N2|N2|M|P", out, "L|0|1|1|1|1|1|1|0"
            If key = "LOC_HOURS" And fid > 0 Then
                Set rows = DB_RowsA("T_LOCATION", PeriodCrit("DateReleve", d1, d2, "CamionID", "=", fid))
                data = DB_Data("T_LOCATION")
                If rows.Count > 0 Then
                    ReDim out(1 To rows.Count, 1 To 9)
                    n = 0
                    For Each r In rows
                        n = n + 1
                        out(n, 1) = data(r, DB_ColIdx("T_LOCATION", "DateReleve"))
                        out(n, 2) = TimeOr0(data(r, DB_ColIdx("T_LOCATION", "HDebut")))
                        out(n, 3) = TimeOr0(data(r, DB_ColIdx("T_LOCATION", "HFin")))
                        out(n, 4) = data(r, DB_ColIdx("T_LOCATION", "HeuresBrutes"))
                        out(n, 5) = data(r, DB_ColIdx("T_LOCATION", "HeuresAttente"))
                        out(n, 6) = ToDbl(data(r, DB_ColIdx("T_LOCATION", "HeuresImmobAtiko"))) + ToDbl(data(r, DB_ColIdx("T_LOCATION", "HeuresImmobExploitant")))
                        out(n, 7) = data(r, DB_ColIdx("T_LOCATION", "HeuresRetenues"))
                        out(n, 8) = data(r, DB_ColIdx("T_LOCATION", "Montant"))
                        out(n, 9) = data(r, DB_ColIdx("T_LOCATION", "Statut"))
                    Next r
                    rep.AddTable "Relevé horaire détaillé - " & CodeOf("T_CAMIONS", fid), "Date|Début|Fin|Brutes|Attente|Immob.|Retenues|Montant|Statut", _
                                 "D|H|H|N2|N2|N2|N2|M|T", out, "L|0|0|1|1|1|1|1|0"
                End If
                rep.Filters = "Camion " & CodeOf("T_CAMIONS", fid)
            End If
        Case "LOC_IMMO"
            Set rows = DB_RowsA("T_INCIDENTS", PeriodCrit("DateIncident", d1, d2, "Immobilisation", "=", "Oui"))
            data = DB_Data("T_INCIDENTS")
            If rows.Count > 0 Then
                ReDim out(1 To rows.Count, 1 To 8)
                For Each r In rows
                    n = n + 1
                    out(n, 1) = data(r, DB_ColIdx("T_INCIDENTS", "NumIncident"))
                    out(n, 2) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_INCIDENTS", "CamionID")))
                    out(n, 3) = data(r, DB_ColIdx("T_INCIDENTS", "TypeIncident"))
                    out(n, 4) = data(r, DB_ColIdx("T_INCIDENTS", "DebutImmob"))
                    out(n, 5) = data(r, DB_ColIdx("T_INCIDENTS", "DateReprise"))
                    If IsBlank(data(r, DB_ColIdx("T_INCIDENTS", "DateReprise"))) Then
                        out(n, 6) = HoursBetween(ToDate(data(r, DB_ColIdx("T_INCIDENTS", "DebutImmob"))), TimeOr0(data(r, DB_ColIdx("T_INCIDENTS", "HeureDebutImmob"))), App_Today(), Time)
                    Else
                        out(n, 6) = data(r, DB_ColIdx("T_INCIDENTS", "DureeImmobH"))
                    End If
                    out(n, 7) = data(r, DB_ColIdx("T_INCIDENTS", "Responsable"))
                    out(n, 8) = data(r, DB_ColIdx("T_INCIDENTS", "Statut"))
                Next r
            End If
            rep.AddTable "Immobilisations de la période", "N° incident|Camion|Type|Début|Reprise|Durée (h)|Responsable|Statut", "T|T|T|D|D|N1|T|T", IIf(n > 0, out, Empty), "L|0|0|0|0|1|0|0"
            rep.AddText "Durée en cours calculée jusqu'à maintenant pour les immobilisations non levées.", "note"
    End Select
    Set BuildRental = rep
End Function

'------------------------------------------------------------------ Carburant
Private Function BuildFuel(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, ByVal fid As Long) As cReport
    Dim rep As cReport, out() As Variant, n As Long, cam As Variant, trucks As Collection, rows As Collection, r As Variant, data As Variant
    Dim crit As Variant, lit As Object, mon As Object, hrs As Object, ton As Object, p1 As Date, p2 As Date, litP As Object
    Set rep = NewRep(key, d1, d2)
    crit = PeriodCrit("DateOp", d1, d2, "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)
    Set lit = Agg("T_CARBURANT", "CamionID", "Litres", crit)
    Set mon = Agg("T_CARBURANT", "CamionID", "Montant", crit)
    Set hrs = Agg("T_CARBURANT", "CamionID", "HeuresDepuisPlein", crit)
    Set ton = Agg("T_ROTATIONS", "CamionID", "PoidsNet", PeriodCrit("DateRotation", d1, d2, "Statut", "=", ST_VALIDEE))
    Set trucks = TruckIds()
    Select Case key
        Case "FUEL_CONSO", "FUEL_COST", "FUEL_TON"
            ReDim out(1 To trucks.Count, 1 To 9)
            For Each cam In trucks
                n = n + 1
                out(n, 1) = CodeOf("T_CAMIONS", cam)
                out(n, 2) = Pick(lit, cam)
                out(n, 3) = Pick(hrs, cam)
                out(n, 4) = Ratio(Pick(lit, cam), Pick(hrs, cam))
                out(n, 5) = DB_Value("T_CAMIONS", cam, "ConsoRefLH")
                out(n, 6) = Ratio(out(n, 4), ToDbl(out(n, 5))) - IIf(out(n, 4) > 0, 1, 0)
                out(n, 7) = Pick(mon, cam)
                out(n, 8) = Ratio(Pick(mon, cam), Pick(ton, cam))
                out(n, 9) = Ratio(Pick(lit, cam), Pick(ton, cam))
            Next cam
            rep.AddTable "Consommation et coût par camion", "Camion|Litres|Heures moteur|L / h|Référence L/h|Écart|Coût (GNF)|GNF / t|L / t", _
                         "T|N0|N1|N2|N1|P|M|M|N2", out, "L|1|1|0|0|0|1|0|0"
            If key = "FUEL_COST" Then
                rep.AddInfo "Cuve", Array("Approvisionnements de la période", FmtNum(DB_Sum("T_CARBURANT", "Litres", "TypeOp", "=", "Approvisionnement", "DateOp", ">=", d1, "DateOp", "<=", d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE), 0) & " L", _
                    "Montant des approvisionnements", FmtMoney(DB_Sum("T_CARBURANT", "Montant", "TypeOp", "=", "Approvisionnement", "DateOp", ">=", d1, "DateOp", "<=", d2, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE)), _
                    "Stock théorique actuel", FmtNum(Fuel_TankStock(), 0) & " L", "Seuil d'alerte", Param_Get("CARB_STOCK_ALERTE", "") & " L")
                rep.AddText "La charge d'exploitation retenue est la consommation (distributions) ; les approvisionnements alimentent le stock (pas de double comptabilisation).", "note"
            End If
        Case "FUEL_COMPARE"
            p2 = d1 - 1: p1 = p2 - (d2 - d1)
            Set litP = Agg("T_CARBURANT", "CamionID", "Litres", PeriodCrit("DateOp", p1, p2, "TypeOp", "=", "Distribution", "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE))
            Dim tonP As Object
            Set tonP = Agg("T_ROTATIONS", "CamionID", "PoidsNet", PeriodCrit("DateRotation", p1, p2, "Statut", "=", ST_VALIDEE))
            ReDim out(1 To trucks.Count, 1 To 7)
            For Each cam In trucks
                n = n + 1
                out(n, 1) = CodeOf("T_CAMIONS", cam)
                out(n, 2) = Pick(litP, cam)
                out(n, 3) = Pick(lit, cam)
                out(n, 4) = Ratio(Pick(lit, cam) - Pick(litP, cam), Pick(litP, cam))
                out(n, 5) = Ratio(Pick(litP, cam), Pick(tonP, cam))
                out(n, 6) = Ratio(Pick(lit, cam), Pick(ton, cam))
                out(n, 7) = Ratio(out(n, 6) - out(n, 5), out(n, 5))
            Next cam
            rep.Filters = "Période de comparaison : du " & FmtDate(p1) & " au " & FmtDate(p2)
            rep.AddTable "Comparatif", "Camion|Litres (préc.)|Litres|Variation|L/t (préc.)|L/t|Variation L/t", "T|N0|N0|P|N2|N2|P", out, "L|1|1|0|0|0|0"
        Case "FUEL_ANOM"
            Set rows = DB_RowsA("T_CARBURANT", PeriodCrit("DateOp", d1, d2, "Anomalie", "NOTBLANK", ""))
            data = DB_Data("T_CARBURANT")
            If rows.Count > 0 Then
                ReDim out(1 To rows.Count, 1 To 8)
                For Each r In rows
                    n = n + 1
                    out(n, 1) = data(r, DB_ColIdx("T_CARBURANT", "NumBon"))
                    out(n, 2) = data(r, DB_ColIdx("T_CARBURANT", "DateOp"))
                    out(n, 3) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_CARBURANT", "CamionID")))
                    out(n, 4) = CodeOf("T_CHAUFFEURS", data(r, DB_ColIdx("T_CARBURANT", "ChauffeurID")))
                    out(n, 5) = data(r, DB_ColIdx("T_CARBURANT", "Litres"))
                    out(n, 6) = data(r, DB_ColIdx("T_CARBURANT", "ConsoLH"))
                    out(n, 7) = data(r, DB_ColIdx("T_CARBURANT", "Anomalie"))
                    out(n, 8) = data(r, DB_ColIdx("T_CARBURANT", "Statut"))
                Next r
            End If
            rep.AddTable "Anomalies détectées", "N° bon|Date|Camion|Chauffeur|Litres|L / h|Anomalie|Statut", "T|D|T|T|N0|N2|T|T", IIf(n > 0, out, Empty), "L|0|0|0|1|0|0|0"
            rep.AddText "Seuils : tolérance " & Param_Get("CARB_TOLERANCE_PCT", "") & " % au-delà de la référence, maximum " & Param_Get("CARB_MAX_LITRES", "") & _
                        " L par plein, doublon si moins de " & Param_Get("CARB_DOUBLON_HEURES", "") & " h moteur entre deux pleins.", "note"
    End Select
    Set BuildFuel = rep
End Function

'------------------------------------------------------------------ Pneumatiques / stock
Private Function BuildTires(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, ByVal fid As Long) As cReport
    Dim rep As cReport, out() As Variant, n As Long, rows As Collection, r As Variant, data As Variant, t As Object, cam As Variant, trucks As Collection
    Dim live As Double, perDay As Double, life As Double
    Set rep = NewRep(key, d1, d2)
    data = DB_Data("T_PNEUS")
    Select Case key
        Case "TIRE_STOCK", "TIRE_TRUCK", "TIRE_WEAR"
            If key = "TIRE_STOCK" Then
                Set rows = DB_Rows("T_PNEUS", "Statut", "IN", PN_STOCK & "|" & PN_REPAR)
            ElseIf fid > 0 Then
                Set rows = DB_Rows("T_PNEUS", "Statut", "=", PN_MONTE, "CamionID", "=", fid)
                rep.Filters = "Camion " & CodeOf("T_CAMIONS", fid)
            Else
                Set rows = DB_Rows("T_PNEUS", "Statut", "=", PN_MONTE)
            End If
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 10)
            For Each r In rows
                n = n + 1
                Set t = DB_RowDict("T_PNEUS", r)
                live = Tire_LiveHours(t)
                life = ToDbl(t("DureeVieCibleH"))
                out(n, 1) = t("NumSerie")
                out(n, 2) = t("Marque") & " " & t("Reference")
                out(n, 3) = t("Dimension")
                out(n, 4) = t("Statut")
                out(n, 5) = CodeOf("T_CAMIONS", t("CamionID"))
                out(n, 6) = t("Position")
                out(n, 7) = live
                out(n, 8) = Ratio(live, life)
                out(n, 9) = t("ProfondeurMM")
                ' prevision : heures restantes / moyenne journaliere du camion sur 30 jours
                perDay = Ratio(Tire_TruckHours(ToLng(t("CamionID")), App_Today() - 30, App_Today()), 30)
                If key = "TIRE_STOCK" Then
                    out(n, 10) = FmtMoney(t("PrixAchat"))
                ElseIf (ToDbl(t("ProfondeurMM")) > 0 And ToDbl(t("ProfondeurMM")) < Param_Num("PNEU_PROFONDEUR_MIN", 6)) Or live >= life Then
                    out(n, 10) = "À REMPLACER"
                ElseIf perDay > 0 Then
                    out(n, 10) = Format$(App_Today() + (life - live) / perDay, "dd/mm/yyyy")
                Else
                    out(n, 10) = "-"
                End If
            Next r
            If n > 0 Then SortArray2D out, 8, True
            rep.AddTable IIf(key = "TIRE_STOCK", "Pneus disponibles", "Pneus montés"), "N° série|Modèle|Dimension|Statut|Camion|Position|Heures|% vie|Prof. (mm)|" & _
                         IIf(key = "TIRE_STOCK", "Prix d'achat", "Remplacement prévu"), "T|T|T|T|T|T|N0|P|N1|T", IIf(n > 0, out, Empty)
            If key = "TIRE_WEAR" Then
                rep.AddText "Prévision : date à laquelle la durée de vie cible serait atteinte au rythme moyen des 30 derniers jours. " & _
                            "Un pneu est « à remplacer » si sa profondeur est inférieure à " & Param_Get("PNEU_PROFONDEUR_MIN", "6") & " mm ou s'il a atteint sa durée de vie cible.", "note"
                rep.AddInfo "Prévision des besoins", Array("Pneus à remplacer maintenant", Tire_ToReplace().Count, "Stock disponible", DB_CountIf("T_PNEUS", "Statut", "=", PN_STOCK), _
                            "Stock minimum", Param_Get("PNEU_STOCK_MIN", ""), "Pneus montés", DB_CountIf("T_PNEUS", "Statut", "=", PN_MONTE))
            End If
        Case "TIRE_MVT"
            Set rows = DB_Rows("T_PNEU_MVT", "DateMvt", ">=", d1, "DateMvt", "<=", d2)
            data = DB_Data("T_PNEU_MVT")
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 8)
            For Each r In rows
                n = n + 1
                out(n, 1) = data(r, DB_ColIdx("T_PNEU_MVT", "DateMvt"))
                out(n, 2) = CodeOf("T_PNEUS", data(r, DB_ColIdx("T_PNEU_MVT", "PneuID")))
                out(n, 3) = data(r, DB_ColIdx("T_PNEU_MVT", "TypeMvt"))
                out(n, 4) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_PNEU_MVT", "CamionID")))
                out(n, 5) = data(r, DB_ColIdx("T_PNEU_MVT", "Position"))
                out(n, 6) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_PNEU_MVT", "CamionDestID")))
                out(n, 7) = data(r, DB_ColIdx("T_PNEU_MVT", "Cout"))
                out(n, 8) = data(r, DB_ColIdx("T_PNEU_MVT", "Motif"))
            Next r
            rep.AddTable "", "Date|Pneu|Mouvement|Camion|Position|Vers camion|Coût|Motif", "D|T|T|T|T|T|M|T", IIf(n > 0, out, Empty), "L|0|0|0|0|0|1|0"
        Case "TIRE_COST"
            Dim rep2 As Object, mnt As Object
            Set rep2 = Agg("T_PNEU_MVT", "CamionID", "Cout", PeriodCrit("DateMvt", d1, d2, "TypeMvt", "IN", "Réparation|Retour réparation"))
            Set mnt = Agg("T_PNEU_MVT", "CamionID", "", PeriodCrit("DateMvt", d1, d2, "TypeMvt", "=", "Montage"))
            Set trucks = TruckIds()
            ReDim out(1 To trucks.Count, 1 To 4)
            For Each cam In trucks
                n = n + 1
                out(n, 1) = CodeOf("T_CAMIONS", cam)
                out(n, 2) = Pick(mnt, cam)
                out(n, 3) = Pick(rep2, cam)
                out(n, 4) = DB_CountIf("T_PNEUS", "CamionID", "=", cam, "Statut", "=", PN_MONTE)
            Next cam
            rep.AddTable "Pneumatiques par camion", "Camion|Montages sur la période|Réparations (GNF)|Pneus montés", "T|N0|M|N0", out, "L|1|1|1"
            rep.AddInfo "Achats", Array("Achats de pneus (période)", FmtMoney(DB_Sum("T_PNEU_MVT", "Cout", "TypeMvt", "=", "Entrée", "DateMvt", ">=", d1, "DateMvt", "<=", d2)), _
                        "Charge pneumatique totale", FmtMoney(Tire_Cost(d1, d2)))
        Case "STOCK_ART"
            data = DB_Data("T_ARTICLES")
            ReDim out(1 To ArrayRows(data), 1 To 7)
            For n = 1 To ArrayRows(data)
                out(n, 1) = data(n, DB_ColIdx("T_ARTICLES", "Code"))
                out(n, 2) = data(n, DB_ColIdx("T_ARTICLES", "Designation"))
                out(n, 3) = data(n, DB_ColIdx("T_ARTICLES", "Stock"))
                out(n, 4) = data(n, DB_ColIdx("T_ARTICLES", "StockMin"))
                out(n, 5) = data(n, DB_ColIdx("T_ARTICLES", "PrixMoyen"))
                out(n, 6) = ToDbl(out(n, 3)) * ToDbl(out(n, 5))
                out(n, 7) = IIf(ToDbl(out(n, 3)) < ToDbl(out(n, 4)), "SOUS LE MINIMUM", "")
            Next n
            rep.AddTable "Stock valorisé (prix moyen pondéré)", "Code|Désignation|Stock|Minimum|PMP (GNF)|Valeur (GNF)|Alerte", "T|T|N1|N1|M|M|T", out, "L|0|0|0|0|1|0"
    End Select
    Set BuildTires = rep
End Function

'------------------------------------------------------------------ Finances
Private Function BuildFinance(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, ByVal fid As Long) As cReport
    Dim rep As cReport, out() As Variant, n As Long, rows As Collection, r As Variant, data As Variant, i As Long, k As Object, late As Long
    Set rep = NewRep(key, d1, d2)
    Select Case key
        Case "FIN_EXP"
            Dim cats As Object, kk As Variant
            Set cats = Agg("T_DEPENSES", "Categorie", "Montant", PeriodCrit("DateDep", d1, d2, "Statut", "=", ST_VALIDEE, "Responsable", "=", RESP_EXPL))
            ReDim out(1 To IIf(cats.Count = 0, 1, cats.Count), 1 To 2)
            For Each kk In cats.Keys
                n = n + 1
                out(n, 1) = kk: out(n, 2) = cats(kk)
            Next kk
            rep.AddTable "Dépenses validées à la charge de l'exploitant (par catégorie)", "Catégorie|Montant (GNF)", "T|M", IIf(n > 0, out, Empty), "L|1"
            Set rows = DB_Rows("T_DEPENSES", "DateDep", ">=", d1, "DateDep", "<=", d2)
            data = DB_Data("T_DEPENSES")
            n = 0
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 8)
            For Each r In rows
                n = n + 1
                out(n, 1) = data(r, DB_ColIdx("T_DEPENSES", "NumPiece"))
                out(n, 2) = data(r, DB_ColIdx("T_DEPENSES", "DateDep"))
                out(n, 3) = data(r, DB_ColIdx("T_DEPENSES", "Categorie"))
                out(n, 4) = data(r, DB_ColIdx("T_DEPENSES", "Libelle"))
                out(n, 5) = CodeOf("T_FOURNISSEURS", data(r, DB_ColIdx("T_DEPENSES", "FournisseurID")))
                out(n, 6) = data(r, DB_ColIdx("T_DEPENSES", "Responsable"))
                out(n, 7) = data(r, DB_ColIdx("T_DEPENSES", "Montant"))
                out(n, 8) = data(r, DB_ColIdx("T_DEPENSES", "Statut"))
            Next r
            rep.AddTable "Détail", "N° pièce|Date|Catégorie|Libellé|Fourn.|Responsable|Montant|Statut", "T|D|T|T|T|T|M|T", IIf(n > 0, out, Empty), "L|0|0|0|0|0|1|0"
            rep.AddText "Les dépenses « Atiko (contrat) » et « Refacturable client » sont tracées mais exclues de la marge de l'exploitant.", "note"
        Case "FIN_INV"
            Set rows = DB_Rows("T_FACTURES", "DateFacture", ">=", d1, "DateFacture", "<=", d2)
            data = DB_Data("T_FACTURES")
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 9)
            For Each r In rows
                n = n + 1
                out(n, 1) = data(r, DB_ColIdx("T_FACTURES", "NumFacture"))
                out(n, 2) = data(r, DB_ColIdx("T_FACTURES", "DateFacture"))
                out(n, 3) = CodeOf("T_CLIENTS", data(r, DB_ColIdx("T_FACTURES", "ClientID")))
                out(n, 4) = Format$(data(r, DB_ColIdx("T_FACTURES", "PeriodeDebut")), "mm/yyyy")
                out(n, 5) = data(r, DB_ColIdx("T_FACTURES", "Tonnage"))
                out(n, 6) = data(r, DB_ColIdx("T_FACTURES", "MontantTTC"))
                out(n, 7) = data(r, DB_ColIdx("T_FACTURES", "MontantPaye"))
                out(n, 8) = data(r, DB_ColIdx("T_FACTURES", "Solde"))
                out(n, 9) = data(r, DB_ColIdx("T_FACTURES", "Statut"))
            Next r
            rep.AddTable "", "N° facture|Date|Client|Période|Tonnage (t)|TTC (GNF)|Réglé|Solde|Statut", "T|D|T|T|N2|M|M|M|T", IIf(n > 0, out, Empty), "L|0|0|0|1|1|1|1|0"
        Case "FIN_PAY"
            Set rows = DB_Rows("T_PAIEMENTS", "DatePaiement", ">=", d1, "DatePaiement", "<=", d2)
            data = DB_Data("T_PAIEMENTS")
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 7)
            For Each r In rows
                n = n + 1
                out(n, 1) = data(r, DB_ColIdx("T_PAIEMENTS", "NumRecu"))
                out(n, 2) = data(r, DB_ColIdx("T_PAIEMENTS", "DatePaiement"))
                out(n, 3) = CodeOf("T_FACTURES", data(r, DB_ColIdx("T_PAIEMENTS", "FactureID")))
                out(n, 4) = CodeOf("T_CLIENTS", DB_Value("T_FACTURES", data(r, DB_ColIdx("T_PAIEMENTS", "FactureID")), "ClientID"))
                out(n, 5) = data(r, DB_ColIdx("T_PAIEMENTS", "ModePaiement"))
                out(n, 6) = data(r, DB_ColIdx("T_PAIEMENTS", "Montant"))
                out(n, 7) = data(r, DB_ColIdx("T_PAIEMENTS", "Statut"))
            Next r
            rep.AddTable "", "N° encaissement|Date|Facture|Client|Mode|Montant (GNF)|Statut", "T|D|T|T|T|M|T", IIf(n > 0, out, Empty), "L|0|0|0|0|1|0"
        Case "FIN_REC", "FIN_AGING"
            data = DB_Data("T_CLIENTS")
            ReDim out(1 To ArrayRows(data), 1 To 8)
            For i = 1 To ArrayRows(data)
                AgingFor ToLng(data(i, DB_ColIdx("T_CLIENTS", "ID"))), out, i
                out(i, 1) = data(i, DB_ColIdx("T_CLIENTS", "Code"))
                out(i, 2) = data(i, DB_ColIdx("T_CLIENTS", "RaisonSociale"))
            Next i
            rep.AddTable "Créances par ancienneté (jours de retard après échéance)", "Code|Client|Non échu|1-30 j|31-60 j|61-90 j|> 90 j|Total dû", "T|T|M|M|M|M|M|M", out, "L|0|1|1|1|1|1|1"
        Case "FIN_LATE"
            Set rows = Bill_Overdue(App_Today())
            data = DB_Data("T_FACTURES")
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 7)
            For Each r In rows
                n = n + 1
                late = App_Today() - ToDate(data(r, DB_ColIdx("T_FACTURES", "Echeance")))
                out(n, 1) = data(r, DB_ColIdx("T_FACTURES", "NumFacture"))
                out(n, 2) = CodeOf("T_CLIENTS", data(r, DB_ColIdx("T_FACTURES", "ClientID")))
                out(n, 3) = data(r, DB_ColIdx("T_FACTURES", "Echeance"))
                out(n, 4) = late
                out(n, 5) = data(r, DB_ColIdx("T_FACTURES", "Solde"))
                out(n, 6) = Bill_SuggestedLevel(late)
                out(n, 7) = DB_CountIf("T_RELANCES", "FactureID", "=", data(r, DB_ColIdx("T_FACTURES", "ID")))
            Next r
            rep.AddTable "Factures échues", "Facture|Client|Échéance|Jours de retard|Solde dû|Relance suggérée|Relances faites", "T|T|D|N0|M|T|N0", IIf(n > 0, out, Empty), "L|0|0|0|1|0|1"
        Case "FIN_MARGIN"
            Set k = KPI_Period(d1, d2)
            ReDim out(1 To 14, 1 To 3)
            MarginLine out, 1, "Chiffre d'affaires reconnu (tonnage validé x tarif)", k("CA"), k("CA")
            MarginLine out, 2, "  Location des camions (Atiko)", -k("Location"), k("CA")
            MarginLine out, 3, "  Carburant consommé", -k("Carburant"), k("CA")
            MarginLine out, 4, "  Pneumatiques (achats + réparations)", -k("Pneus"), k("CA")
            MarginLine out, 5, "  Consommables (sorties de stock)", -k("Consommables"), k("CA")
            MarginLine out, 6, "  Personnel (salaires chargés + primes)", -k("Personnel"), k("CA")
            MarginLine out, 7, "  Dépenses diverses", -k("Depenses"), k("CA")
            MarginLine out, 8, "  Charges exceptionnelles", -k("Exceptionnelles"), k("CA")
            MarginLine out, 9, "Total des charges de l'exploitant", -k("Charges"), k("CA")
            MarginLine out, 10, "MARGE OPÉRATIONNELLE ESTIMÉE", k("Marge"), k("CA")
            MarginLine out, 11, "Pour mémoire : charges Atiko (hors marge)", k("ChargesAtiko"), k("CA")
            MarginLine out, 12, "Pour mémoire : CA facturé (période)", k("CAFacture"), k("CA")
            MarginLine out, 13, "Pour mémoire : encaissements", k("Encaissements"), k("CA")
            MarginLine out, 14, "Coût complet par tonne (GNF/t)", k("CoutTonne"), 0
            rep.AddTable "Compte d'exploitation simplifié", "Poste|Montant (GNF)|% du CA", "T|M|P", out
            rep.AddInfo "Volumes", Array("Tonnage validé", FmtNum(k("Tonnage"), 2) & " t", "Rotations", k("Rotations"), "Heures de location retenues", FmtNum(k("HeuresLocation"), 1), _
                        "Litres consommés", FmtNum(k("Litres"), 0), "Disponibilité flotte", FmtPct(k("Disponibilite"), 1), "Taux de marge", FmtPct(k("TauxMarge"), 1))
            rep.AddText "Méthode : marge = CA reconnu - charges réellement imputables à l'exploitant. Les achats de carburant (cuve) ne sont pas comptés " & _
                        "(seule la consommation l'est) ; la maintenance contractuelle Atiko est exclue.", "note"
        Case "FIN_CASH"
            Set rows = DB_Rows("T_CAISSE", "DateOp", ">=", d1, "DateOp", "<=", d2)
            data = DB_Data("T_CAISSE")
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 6)
            For Each r In rows
                n = n + 1
                out(n, 1) = data(r, DB_ColIdx("T_CAISSE", "DateOp"))
                out(n, 2) = data(r, DB_ColIdx("T_CAISSE", "Reference"))
                out(n, 3) = data(r, DB_ColIdx("T_CAISSE", "Libelle"))
                If data(r, DB_ColIdx("T_CAISSE", "Sens")) = "Entrée" Then out(n, 4) = data(r, DB_ColIdx("T_CAISSE", "Montant")) Else out(n, 5) = data(r, DB_ColIdx("T_CAISSE", "Montant"))
                out(n, 6) = data(r, DB_ColIdx("T_CAISSE", "SoldeApres"))
            Next r
            rep.AddTable "", "Date|Référence|Libellé|Entrée|Sortie|Solde", "D|T|T|M|M|M", IIf(n > 0, out, Empty), "L|0|0|1|1|0"
            rep.AddInfo "", Array("Solde de caisse actuel", FmtMoney(Fin_CashBalance()))
        Case "FIN_ADV"
            Set rows = DB_Rows("T_AVANCES", "Statut", "=", "En cours")
            data = DB_Data("T_AVANCES")
            If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 6)
            For Each r In rows
                n = n + 1
                out(n, 1) = data(r, DB_ColIdx("T_AVANCES", "DateAvance"))
                out(n, 2) = data(r, DB_ColIdx("T_AVANCES", "Beneficiaire"))
                out(n, 3) = data(r, DB_ColIdx("T_AVANCES", "Motif"))
                out(n, 4) = data(r, DB_ColIdx("T_AVANCES", "Montant"))
                out(n, 5) = data(r, DB_ColIdx("T_AVANCES", "MontantRembourse"))
                out(n, 6) = data(r, DB_ColIdx("T_AVANCES", "Solde"))
            Next r
            rep.AddTable "Avances en cours", "Date|Bénéficiaire|Motif|Montant|Remboursé|Reste dû", "D|T|T|M|M|M", IIf(n > 0, out, Empty), "L|0|0|1|1|1"
        Case "CLIENT_SIT"
            Set rep = ClientStatement(fid)
    End Select
    Set BuildFinance = rep
End Function

Private Sub MarginLine(ByRef out() As Variant, ByVal i As Long, ByVal label As String, ByVal v As Double, ByVal ca As Double)
    out(i, 1) = label
    out(i, 2) = Round(v, 0)
    If ca <> 0 Then out(i, 3) = v / ca Else out(i, 3) = Empty
End Sub

Private Sub AgingFor(ByVal clientId As Long, ByRef out() As Variant, ByVal i As Long)
    Dim rows As Collection, r As Variant, data As Variant, late As Long, s As Double, b As Long, j As Long
    For j = 3 To 8
        out(i, j) = 0
    Next j
    Set rows = DB_Rows("T_FACTURES", "ClientID", "=", clientId, "Statut", "IN", FAC_VALIDEE & "|" & FAC_PARTIELLE)
    data = DB_Data("T_FACTURES")
    For Each r In rows
        s = ToDbl(data(r, DB_ColIdx("T_FACTURES", "Solde")))
        late = App_Today() - ToDate(data(r, DB_ColIdx("T_FACTURES", "Echeance")))
        If late <= 0 Then
            b = 3
        ElseIf late <= 30 Then
            b = 4
        ElseIf late <= 60 Then
            b = 5
        ElseIf late <= 90 Then
            b = 6
        Else
            b = 7
        End If
        out(i, b) = out(i, b) + s
        out(i, 8) = out(i, 8) + s
    Next r
End Sub

Private Function ClientStatement(ByVal clientId As Long) As cReport
    Dim rep As cReport, c As Object, rows As Collection, r As Variant, data As Variant, out() As Variant, n As Long, ag() As Variant
    If clientId = 0 Then Fail "Choisissez un client."
    Set c = DB_Get("T_CLIENTS", clientId)
    Set rep = New cReport
    rep.Title = "Situation du client " & c("Code")
    rep.Category = "Finances"
    rep.FileStem = "Situation_" & c("Code")
    rep.Subtitle = "Relevé de compte au " & FmtDate(App_Today())
    rep.AddInfo "Client", Array("Raison sociale", c("RaisonSociale"), "Contrat", c("RefContrat"), "Adresse", c("Adresse"), "Tarif", FmtMoney(c("TarifTonne")) & " / t", _
                "Contact", c("Contact"), "Délai de paiement", c("DelaiPaiementJ") & " jours")
    Set rows = DB_Rows("T_FACTURES", "ClientID", "=", clientId, "Statut", "<>", FAC_BROUILLON)
    data = DB_Data("T_FACTURES")
    If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 8)
    For Each r In rows
        n = n + 1
        out(n, 1) = data(r, DB_ColIdx("T_FACTURES", "NumFacture"))
        out(n, 2) = data(r, DB_ColIdx("T_FACTURES", "DateFacture"))
        out(n, 3) = data(r, DB_ColIdx("T_FACTURES", "Echeance"))
        out(n, 4) = data(r, DB_ColIdx("T_FACTURES", "MontantTTC"))
        out(n, 5) = data(r, DB_ColIdx("T_FACTURES", "MontantPaye"))
        out(n, 6) = data(r, DB_ColIdx("T_FACTURES", "Solde"))
        out(n, 7) = data(r, DB_ColIdx("T_FACTURES", "Statut"))
        out(n, 8) = IIf(data(r, DB_ColIdx("T_FACTURES", "Litige")) = "Oui", "LITIGE", "")
    Next r
    rep.AddTable "Factures", "Facture|Date|Échéance|TTC|Réglé|Solde|Statut|Litige", "T|D|D|M|M|M|T|T", IIf(n > 0, out, Empty), "L|0|0|1|1|1|0|0"
    ReDim ag(1 To 1, 1 To 8)
    AgingFor clientId, ag, 1
    rep.AddInfo "Ancienneté des créances", Array("Non échu", FmtMoney(ag(1, 3)), "1 à 30 jours", FmtMoney(ag(1, 4)), "31 à 60 jours", FmtMoney(ag(1, 5)), _
                "61 à 90 jours", FmtMoney(ag(1, 6)), "Plus de 90 jours", FmtMoney(ag(1, 7)), "TOTAL DÛ", FmtMoney(ag(1, 8)))
    Set ClientStatement = rep
End Function

Public Sub Rpt_ClientStatement(ByVal clientId As Long)
    Dim rep As cReport, mode As String
    Set rep = ClientStatement(clientId)
    mode = Rpt_AskOutput(rep.Title)
    If Len(mode) > 0 Then Rpt_Output rep, mode
End Sub

'------------------------------------------------------------------ Direction
Private Function BuildDirection(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, ByVal fid As Long) As cReport
    Dim rep As cReport, k As Object, fl As Object, out() As Variant, n As Long, cam As Variant, trucks As Collection, s As Object, a As Variant
    Set rep = NewRep(key, d1, d2)
    Set k = KPI_Period(d1, d2)
    Set fl = KPI_Fleet()
    Select Case key
        Case "DIR_KPI", "DIR_GENERAL"
            rep.AddInfo "Production", Array("Tonnage validé", FmtNum(k("Tonnage"), 2) & " t", "Rotations", k("Rotations"), _
                "Objectif mensuel", IIf(k("Objectif") > 0, FmtNum(k("Objectif"), 0) & " t", "-"), "Réalisation", IIf(k("Objectif") > 0, FmtPct(Ratio(k("Tonnage"), k("Objectif"))), "-"), _
                "Tonnage rejeté", FmtNum(k("TonnageRejete"), 2) & " t", "Rotations en attente", k("RotationsAttente"))
            rep.AddInfo "Finances", Array("CA reconnu", FmtMoney(k("CA")), "CA facturé", FmtMoney(k("CAFacture")), "Encaissements", FmtMoney(k("Encaissements")), _
                "Créances clients", FmtMoney(k("Creances")), "Total des charges", FmtMoney(k("Charges")), "Marge estimée", FmtMoney(k("Marge")) & " (" & FmtPct(k("TauxMarge")) & ")")
            rep.AddInfo "Flotte et exploitation", Array("Camions actifs", fl("Actifs") & " / " & fl("Total"), "Disponibilité (heures)", FmtPct(k("Disponibilite")), _
                "Heures de location retenues", FmtNum(k("HeuresLocation"), 1), "Coût de location", FmtMoney(k("Location")), _
                "Consommation carburant", FmtNum(k("Litres"), 0) & " L (" & FmtNum(k("LitresTonne"), 2) & " L/t)", "Heures d'immobilisation", FmtNum(k("HeuresImmob"), 1), _
                "Coût complet / tonne", FmtMoney(k("CoutTonne")), "Taux d'attente", FmtPct(k("TauxAttente")))
            ReDim out(1 To 4, 1 To 3)
            a = Array("Carburant", k("Carburant"), "Pneumatiques", k("Pneus"), "Location", k("Location"), "Autres charges", k("AutresCharges"))
            For n = 1 To 4
                out(n, 1) = a((n - 1) * 2): out(n, 2) = a((n - 1) * 2 + 1): out(n, 3) = Ratio(a((n - 1) * 2 + 1), k("Charges"))
            Next n
            rep.AddTable "Répartition des charges", "Poste|Montant (GNF)|Part", "T|M|P", out, "L|1|0"
            If key = "DIR_GENERAL" Then
                Dim al As Collection, ao() As Variant
                Set al = Alerts_Compute()
                If al.Count > 0 Then
                    ReDim ao(1 To al.Count, 1 To 3)
                    n = 0
                    For Each s In al
                        n = n + 1
                        ao(n, 1) = UCase$(s("Niveau")): ao(n, 2) = s("Titre"): ao(n, 3) = s("Detail")
                    Next s
                    rep.AddTable "Alertes en cours", "Niveau|Alerte|Détail", "T|T|T", ao
                End If
                rep.AddTable "Production et contribution par camion", "Camion|Rotations|Tonnage|CA|Carburant|Location|Contribution", "T|N0|N2|M|M|M|M", TruckMargins(d1, d2), "L|1|1|1|1|1|1"
            End If
        Case "DIR_RENTA"
            rep.AddTable "Rentabilité par camion (contribution = CA - carburant - location - pneus)", "Camion|Rotations|Tonnage|CA|Carburant|Location|Contribution", _
                         "T|N0|N2|M|M|M|M", TruckMargins(d1, d2), "L|1|1|1|1|1|1"
            rep.AddInfo "Structure de coût", Array("Coût complet / tonne", FmtMoney(k("CoutTonne")), "Carburant / tonne", FmtMoney(k("CarburantTonne")), _
                "Location / tonne", FmtMoney(Ratio(k("Location"), k("Tonnage"))), "Prix moyen de vente / tonne", FmtMoney(Ratio(k("CA"), k("Tonnage"))), _
                "Marge / tonne", FmtMoney(Ratio(k("Marge"), k("Tonnage"))), "Taux de marge", FmtPct(k("TauxMarge")))
            Dim wm As Variant
            wm = KPI_WeeklyMargins(d2, 8)
            ReDim out(1 To 8, 1 To 3)
            For n = 1 To 8
                out(n, 1) = "Semaine du " & wm(n, 1): out(n, 2) = wm(n, 2): out(n, 3) = wm(n, 3)
            Next n
            rep.AddTable "Évolution hebdomadaire de la marge", "Semaine|Marge (GNF)|Taux", "T|M|P", out
        Case "DIR_PERF"
            rep.AddTable "Classement des camions (tonnage)", "Camion|Rotations|Tonnage|CA|Carburant|Location|Contribution", "T|N0|N2|M|M|M|M", SortedDesc(TruckMargins(d1, d2), 3), "L|1|1|1|1|1|1"
            Set rep = MergeReport(rep, BuildProduction("PRD_DRIVER", d1, d2, 0))
        Case "DIR_ACTIVITY"
            rep.AddText "Faits marquants", "title"
            rep.AddText "• Production : " & FmtNum(k("Tonnage"), 0) & " t validées en " & k("Rotations") & " rotations (" & FmtNum(Ratio(k("Tonnage"), k("Rotations")), 1) & " t/rotation)."
            rep.AddText "• Flotte : " & fl("Actifs") & " camions actifs sur " & fl("Total") & " ; " & DB_CountIf("T_INCIDENTS", "DateIncident", ">=", d1, "DateIncident", "<=", d2) & _
                        " incident(s), " & FmtNum(k("HeuresImmob"), 0) & " h d'immobilisation."
            rep.AddText "• Finances : CA reconnu " & FmtMoney(k("CA")) & ", encaissements " & FmtMoney(k("Encaissements")) & ", créances " & FmtMoney(k("Creances")) & "."
            rep.AddText "• Marge estimée : " & FmtMoney(k("Marge")) & " (" & FmtPct(k("TauxMarge")) & ")."
            Set rep = MergeReport(rep, BuildRental("LOC_IMMO", d1, d2, 0))
    End Select
    Set BuildDirection = rep
End Function

Private Function TruckMargins(ByVal d1 As Date, ByVal d2 As Date) As Variant
    Dim trucks As Collection, cam As Variant, out() As Variant, n As Long, s As Object
    Set trucks = TruckIds()
    ReDim out(1 To trucks.Count, 1 To 7)
    For Each cam In trucks
        n = n + 1
        Set s = Fleet_TruckStats(cam, d1, d2)
        out(n, 1) = CodeOf("T_CAMIONS", cam)
        out(n, 2) = s("Rotations")
        out(n, 3) = s("Tonnage")
        out(n, 4) = s("CA")
        out(n, 5) = s("CoutCarburant")
        out(n, 6) = s("CoutLocation")
        out(n, 7) = s("Marge")
    Next cam
    TruckMargins = out
End Function

Private Function SortedDesc(ByVal a As Variant, ByVal col As Long) As Variant
    SortArray2D a, col, True
    SortedDesc = a
End Function

Private Function MergeReport(ByVal target As cReport, ByVal src As cReport) As cReport
    Dim b As Object
    For Each b In src.Blocks()
        target.Blocks().Add b
    Next b
    Set MergeReport = target
End Function

'------------------------------------------------------------------ Fiches
Private Function BuildCards(ByVal key As String, ByVal d1 As Date, ByVal d2 As Date, ByVal fid As Long) As cReport
    Select Case key
        Case "CARD_TRUCK": Set BuildCards = TruckCard(fid, d1, d2)
        Case "CARD_DRIVER": Set BuildCards = DriverCard(fid, d1, d2)
        Case "CHECK_COH"
            Dim rep As cReport
            Set rep = NewRep(key, d1, d2)
            rep.AddTable "Résultats", "Gravité|Contrôle|Détail", "T|T|T", Check_Coherence()
            Set BuildCards = rep
    End Select
End Function

Public Function TruckCard(ByVal truckId As Long, ByVal d1 As Date, ByVal d2 As Date) As cReport
    Dim rep As cReport, t As Object, s As Object, out() As Variant, rows As Collection, r As Variant, data As Variant, n As Long
    If truckId = 0 Then Fail "Choisissez un camion."
    Set t = DB_Get("T_CAMIONS", truckId)
    Set rep = New cReport
    rep.Title = "Fiche camion " & t("Code")
    rep.Category = "Flotte"
    rep.FileStem = "Fiche_" & t("Code")
    rep.SetPeriod d1, d2
    rep.AddInfo "Identification", Array("Code", t("Code"), "Immatriculation", t("Immatriculation"), "Marque / modèle", t("Marque") & " " & t("Modele"), _
        "Capacité utile", FmtNum(t("CapaciteT"), 1) & " t", "Propriétaire", t("Proprietaire"), "Mise en service", FmtDate(t("DateMiseService")), _
        "N° châssis", t("NumChassis"), "Statut", t("Statut"), "Tarif horaire", FmtMoney(t("TarifHoraire")), "Compteur moteur", FmtNum(t("CompteurHeures"), 1) & " h", _
        "Assurance jusqu'au", FmtDate(t("AssuranceExp")), "Visite technique jusqu'au", FmtDate(t("VisiteTechExp")), _
        "Chauffeur affecté", CodeOf("T_CHAUFFEURS", Fleet_DriverFor(truckId, App_Today())) & " " & DriverName(Fleet_DriverFor(truckId, App_Today())), _
        "Consommation de référence", FmtNum(t("ConsoRefLH"), 1) & " L/h")
    Set s = Fleet_TruckStats(truckId, d1, d2)
    rep.AddInfo "Statistiques de la période", Array("Rotations", s("Rotations"), "Tonnage", FmtNum(s("Tonnage"), 2) & " t", "Tonnage moyen", FmtNum(s("TonnageMoyen"), 2) & " t", _
        "CA généré", FmtMoney(s("CA")), "Heures retenues", FmtNum(s("HeuresRetenues"), 1), "Coût de location", FmtMoney(s("CoutLocation")), _
        "Gasoil", FmtNum(s("Litres"), 0) & " L (" & FmtNum(s("LitresHeure"), 2) & " L/h)", "Coût carburant", FmtMoney(s("CoutCarburant")), _
        "Heures d'immobilisation", FmtNum(s("HeuresImmob"), 1), "Coût direct / tonne", FmtMoney(s("CoutTonne")), "Contribution", FmtMoney(s("Marge")), "", "")
    ' affectations
    Set rows = DB_Rows("T_AFFECTATIONS", "CamionID", "=", truckId)
    data = DB_Data("T_AFFECTATIONS")
    If rows.Count > 0 Then
        ReDim out(1 To rows.Count, 1 To 5)
        For Each r In rows
            n = n + 1
            out(n, 1) = CodeOf("T_CHAUFFEURS", data(r, DB_ColIdx("T_AFFECTATIONS", "ChauffeurID")))
            out(n, 2) = DriverName(data(r, DB_ColIdx("T_AFFECTATIONS", "ChauffeurID")))
            out(n, 3) = data(r, DB_ColIdx("T_AFFECTATIONS", "DateDebut"))
            out(n, 4) = data(r, DB_ColIdx("T_AFFECTATIONS", "DateFin"))
            out(n, 5) = data(r, DB_ColIdx("T_AFFECTATIONS", "Statut"))
        Next r
        rep.AddTable "Historique des affectations", "Matricule|Chauffeur|Début|Fin|Statut", "T|T|D|D|T", out
    End If
    ' incidents
    Set rows = DB_Rows("T_INCIDENTS", "CamionID", "=", truckId)
    data = DB_Data("T_INCIDENTS")
    If rows.Count > 0 Then
        n = 0
        ReDim out(1 To rows.Count, 1 To 6)
        For Each r In rows
            n = n + 1
            out(n, 1) = data(r, DB_ColIdx("T_INCIDENTS", "DateIncident"))
            out(n, 2) = data(r, DB_ColIdx("T_INCIDENTS", "TypeIncident"))
            out(n, 3) = data(r, DB_ColIdx("T_INCIDENTS", "Description"))
            out(n, 4) = data(r, DB_ColIdx("T_INCIDENTS", "Responsable"))
            out(n, 5) = data(r, DB_ColIdx("T_INCIDENTS", "DureeImmobH"))
            out(n, 6) = data(r, DB_ColIdx("T_INCIDENTS", "Statut"))
        Next r
        rep.AddTable "Incidents", "Date|Type|Description|Responsable|Immob. (h)|Statut", "D|T|T|T|N1|T", out
    End If
    ' pneus montes
    Dim tr As cReport
    Set tr = BuildTires("TIRE_TRUCK", d1, d2, truckId)
    Set rep = MergeReport(rep, tr)
    Set TruckCard = rep
End Function

Public Sub Rpt_TruckCard(ByVal truckId As Long)
    Dim d1 As Date, d2 As Date, rep As cReport, mode As String
    d1 = MonthStart(App_Today()): d2 = App_Today()
    If Not UI_AskPeriod(d1, d2, "Statistiques de la fiche camion") Then Exit Sub
    Set rep = TruckCard(truckId, d1, d2)
    mode = Rpt_AskOutput(rep.Title)
    If Len(mode) > 0 Then Rpt_Output rep, mode
End Sub

Public Function DriverCard(ByVal driverId As Long, ByVal d1 As Date, ByVal d2 As Date) As cReport
    Dim rep As cReport, c As Object, rows As Collection, r As Variant, data As Variant, out() As Variant, n As Long, ton As Double, rot As Long
    If driverId = 0 Then Fail "Choisissez un chauffeur."
    Set c = DB_Get("T_CHAUFFEURS", driverId)
    Set rep = New cReport
    rep.Title = "Fiche chauffeur " & c("Matricule")
    rep.Category = "Flotte"
    rep.FileStem = "Fiche_" & c("Matricule")
    rep.SetPeriod d1, d2
    rep.AddInfo "Identité", Array("Matricule", c("Matricule"), "Nom", c("Prenom") & " " & c("Nom"), "Téléphone", c("Telephone"), "Adresse", c("Adresse"), _
        "Contact d'urgence", c("ContactUrgence") & " " & c("TelUrgence"), "Embauche", FmtDate(c("DateEmbauche")), _
        "Permis", c("NumPermis") & " (cat. " & c("CategoriePermis") & ")", "Expiration du permis", FmtDate(c("ExpPermis")) & IIf(ToDate(c("ExpPermis")) < App_Today(), "  EXPIRÉ", ""), _
        "Statut", c("Statut"), "Camion affecté", CodeOf("T_CAMIONS", ToLng(DB_Value("T_AFFECTATIONS", Fleet_DriverActiveAssignment(driverId, App_Today()), "CamionID"))))
    ton = DB_Sum("T_ROTATIONS", "PoidsNet", "ChauffeurID", "=", driverId, "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    rot = DB_CountIf("T_ROTATIONS", "ChauffeurID", "=", driverId, "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2)
    rep.AddInfo "Production de la période", Array("Rotations validées", rot, "Tonnage", FmtNum(ton, 2) & " t", "Tonnage moyen", FmtNum(Ratio(ton, rot), 2) & " t", _
        "Rejets", DB_CountIf("T_ROTATIONS", "ChauffeurID", "=", driverId, "Statut", "=", ST_REJETEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2), _
        "Incidents", DB_CountIf("T_INCIDENTS", "ChauffeurID", "=", driverId, "DateIncident", ">=", d1, "DateIncident", "<=", d2), _
        "Anomalies carburant", DB_CountIf("T_CARBURANT", "ChauffeurID", "=", driverId, "Anomalie", "NOTBLANK", "", "DateOp", ">=", d1, "DateOp", "<=", d2))
    Set rows = DB_Rows("T_INCIDENTS", "ChauffeurID", "=", driverId)
    data = DB_Data("T_INCIDENTS")
    If rows.Count > 0 Then
        ReDim out(1 To rows.Count, 1 To 4)
        For Each r In rows
            n = n + 1
            out(n, 1) = data(r, DB_ColIdx("T_INCIDENTS", "DateIncident"))
            out(n, 2) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_INCIDENTS", "CamionID")))
            out(n, 3) = data(r, DB_ColIdx("T_INCIDENTS", "TypeIncident"))
            out(n, 4) = data(r, DB_ColIdx("T_INCIDENTS", "Description"))
        Next r
        rep.AddTable "Historique des incidents", "Date|Camion|Type|Description", "D|T|T|T", out
    End If
    Set DriverCard = rep
End Function

Public Sub Rpt_DriverCard(ByVal driverId As Long)
    Dim d1 As Date, d2 As Date, rep As cReport, mode As String
    d1 = MonthStart(App_Today()): d2 = App_Today()
    If Not UI_AskPeriod(d1, d2, "Statistiques de la fiche chauffeur") Then Exit Sub
    Set rep = DriverCard(driverId, d1, d2)
    mode = Rpt_AskOutput(rep.Title)
    If Len(mode) > 0 Then Rpt_Output rep, mode
End Sub

Public Sub Rpt_TireHistory(ByVal tireId As Long)
    Dim rep As cReport, t As Object, rows As Collection, r As Variant, data As Variant, out() As Variant, n As Long, mode As String
    Set t = DB_Get("T_PNEUS", tireId)
    Set rep = New cReport
    rep.Title = "Historique du pneu " & t("NumSerie")
    rep.Category = "Pneumatiques"
    rep.FileStem = "Pneu_" & t("NumSerie")
    rep.Subtitle = t("Marque") & " " & t("Reference") & " " & t("Dimension")
    rep.Signatures = False
    rep.AddInfo "Situation", Array("Statut", t("Statut"), "Camion / position", CodeOf("T_CAMIONS", t("CamionID")) & " " & t("Position"), _
        "Heures cumulées", FmtNum(Tire_LiveHours(t), 0) & " h / " & FmtNum(t("DureeVieCibleH"), 0) & " h", "Profondeur", FmtNum(t("ProfondeurMM"), 1) & " mm", _
        "Prix d'achat", FmtMoney(t("PrixAchat")), "Entrée", FmtDate(t("DateEntree")))
    Set rows = DB_Rows("T_PNEU_MVT", "PneuID", "=", tireId)
    data = DB_Data("T_PNEU_MVT")
    If rows.Count > 0 Then ReDim out(1 To rows.Count, 1 To 6)
    For Each r In rows
        n = n + 1
        out(n, 1) = data(r, DB_ColIdx("T_PNEU_MVT", "DateMvt"))
        out(n, 2) = data(r, DB_ColIdx("T_PNEU_MVT", "TypeMvt"))
        out(n, 3) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_PNEU_MVT", "CamionID"))) & " " & data(r, DB_ColIdx("T_PNEU_MVT", "Position"))
        out(n, 4) = CodeOf("T_CAMIONS", data(r, DB_ColIdx("T_PNEU_MVT", "CamionDestID"))) & " " & data(r, DB_ColIdx("T_PNEU_MVT", "PositionDest"))
        out(n, 5) = data(r, DB_ColIdx("T_PNEU_MVT", "Cout"))
        out(n, 6) = data(r, DB_ColIdx("T_PNEU_MVT", "Motif"))
    Next r
    rep.AddTable "Mouvements", "Date|Mouvement|Camion / position|Vers|Coût|Motif", "D|T|T|T|M|T", IIf(n > 0, out, Empty), "L|0|0|0|1|0"
    mode = Rpt_AskOutput(rep.Title)
    If Len(mode) > 0 Then Rpt_Output rep, mode
End Sub
