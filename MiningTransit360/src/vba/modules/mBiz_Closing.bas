Attribute VB_Name = "mBiz_Closing"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Closing
' Cloture journaliere, cloture mensuelle, reouverture, controle de coherence.
'
' Cloture journaliere : aucune rotation / carburant en "Saisie", releves de
'   location presents pour chaque camion ayant roule -> verrouillage des saisies.
' Cloture mensuelle : tous les jours clotures, location et carburant valides,
'   facture emise pour chaque client ayant de la production validee.
'==============================================================================
Option Explicit

Private Function DayIssues(ByVal d As Date) As Collection
    Dim res As New Collection, n As Long, data As Variant, r As Long, cam As Long, trucks As Object, k As Variant
    n = DB_CountIf("T_ROTATIONS", "DateRotation", "=", d, "Statut", "=", ST_SAISIE)
    If n > 0 Then res.Add n & " rotation(s) au statut Saisie : validez-les ou rejetez-les."
    n = DB_CountIf("T_CARBURANT", "DateOp", "=", d, "Statut", "=", ST_SAISIE)
    If n > 0 Then res.Add n & " opération(s) carburant au statut Saisie."
    Set trucks = DB_Distinct("T_ROTATIONS", "CamionID", "DateRotation", "=", d, "Statut", "=", ST_VALIDEE)
    For Each k In trucks.Keys
        If DB_CountIf("T_LOCATION", "CamionID", "=", ToLng(k), "DateReleve", "=", d, "Statut", "IN", ST_SAISIE & "|" & ST_VALIDEE) = 0 Then
            res.Add "Relevé horaire manquant pour " & CodeOf("T_CAMIONS", k) & " (utilisez « Générer relevés » du module Location)."
        End If
    Next k
    Set DayIssues = res
End Function

Public Function Close_NextDay() As Date
    Dim lastDay As Date
    lastDay = ToDate(Param_Get("CLOTURE_DERNIER_JOUR", ""))
    If lastDay = 0 Then lastDay = App_Today() - 1 Else lastDay = lastDay + 1
    Close_NextDay = lastDay
End Function

Public Sub Close_Day(Optional ByVal d As Date = 0, Optional ByVal silent As Boolean = False)
    Dim issues As Collection, msg As String, v As Variant, ton As Double, n As Long, lastDay As Date
    Sec_Require "CLOTURES", "V"
    If d = 0 Then d = Close_NextDay()
    lastDay = ToDate(Param_Get("CLOTURE_DERNIER_JOUR", ""))
    If lastDay > 0 And d <= lastDay Then Fail "La journée du " & FmtDate(d) & " est déjà clôturée."
    If lastDay > 0 And d > lastDay + 1 Then
        ' jours intermediaires sans activite (dimanches) : autorises s'ils n'ont aucune saisie
        Dim x As Date
        For x = lastDay + 1 To d - 1
            If DB_CountIf("T_ROTATIONS", "DateRotation", "=", x) > 0 Then Fail "La journée du " & FmtDate(x) & " doit être clôturée avant celle du " & FmtDate(d) & "."
        Next x
    End If
    If d >= App_Today() Then Fail "On ne clôture pas la journée en cours ni une journée future."
    Set issues = DayIssues(d)
    If issues.Count > 0 Then
        For Each v In issues
            msg = msg & "• " & v & vbLf
        Next v
        Fail "Clôture du " & FmtDate(d) & " impossible :" & vbLf & msg
    End If
    If Not silent Then
        If Not UI_Confirm("Clôturer la journée du " & FmtDate(d) & " ?" & vbLf & "Les saisies de cette journée seront verrouillées.", "Clôture journalière", "Clôturer") Then Exit Sub
    End If
    ton = Ops_Tonnage(d, d)
    n = DB_CountIf("T_ROTATIONS", "DateRotation", "=", d, "Statut", "=", ST_VALIDEE)
    DB_BeginTx "T_CLOTURES|T_PARAM|T_AUDIT|T_SEQ"
    DB_Insert "T_CLOTURES", NewRecord("TypeCloture", "Journalière", "Periode", IsoDate(d), "DateCloture", App_Today(), "Tonnage", ton, _
                                      "NbRotations", n, "EnAttente", 0, "Anomalies", DB_CountIf("T_CARBURANT", "DateOp", "=", d, "Anomalie", "NOTBLANK", ""), _
                                      "Statut", "Clôturée", "Commentaire", "")
    Param_Set "CLOTURE_DERNIER_JOUR", IsoDate(d), , "Exploitation", "Date"
    Log_Audit "CLOTURE-JOUR", "T_CLOTURES", IsoDate(d), FmtNum(ton, 2) & " t - " & n & " rotations"
    DB_Commit
    If Not silent Then UI_Alert "Journée du " & FmtDate(d) & " clôturée : " & FmtNum(ton, 2) & " t validées, " & n & " rotations.", "success", "Clôture"
End Sub

Public Sub Close_Month(Optional ByVal mk As String = "")
    Dim d1 As Date, d2 As Date, issues As String, x As Date, lastDay As Date, lastMonth As String, data As Variant, r As Long, cid As Long
    Sec_Require "CLOTURES", "V"
    If Len(mk) = 0 Then
        If Not UI_AskMonth(mk, "Mois à clôturer") Then Exit Sub
    End If
    d1 = MonthFromKey(mk): d2 = MonthEnd(d1)
    lastMonth = ToStr(Param_Get("CLOTURE_DERNIER_MOIS", ""))
    If Len(lastMonth) > 0 And mk <= lastMonth Then Fail "Le mois " & mk & " est déjà clôturé."
    If d2 >= App_Today() Then Fail "Le mois " & mk & " n'est pas terminé."
    lastDay = ToDate(Param_Get("CLOTURE_DERNIER_JOUR", ""))
    For x = d1 To d2
        If x > lastDay And DB_CountIf("T_ROTATIONS", "DateRotation", "=", x) > 0 Then issues = issues & "• Journée du " & FmtDate(x) & " non clôturée" & vbLf: Exit For
    Next x
    If DB_CountIf("T_LOCATION", "Statut", "=", ST_SAISIE, "DateReleve", ">=", d1, "DateReleve", "<=", d2) > 0 Then issues = issues & "• Relevés de location non validés" & vbLf
    If DB_CountIf("T_CARBURANT", "Statut", "=", ST_SAISIE, "DateOp", ">=", d1, "DateOp", "<=", d2) > 0 Then issues = issues & "• Opérations carburant non validées" & vbLf
    If DB_CountIf("T_DEPENSES", "Statut", "=", ST_SAISIE, "DateDep", ">=", d1, "DateDep", "<=", d2) > 0 Then issues = issues & "• Dépenses non validées" & vbLf
    data = DB_Data("T_CLIENTS")
    For r = 1 To ArrayRows(data)
        cid = ToLng(data(r, DB_ColIdx("T_CLIENTS", "ID")))
        If Bill_BillableRows(cid, d1, d2).Count > 0 Then issues = issues & "• Production validée non facturée pour " & data(r, DB_ColIdx("T_CLIENTS", "Code")) & vbLf
    Next r
    If DB_CountIf("T_FACTURES", "Statut", "=", FAC_BROUILLON, "PeriodeDebut", "=", d1) > 0 Then issues = issues & "• Factures en brouillon sur le mois" & vbLf
    If Len(issues) > 0 Then Fail "Clôture mensuelle " & mk & " impossible :" & vbLf & issues
    If Not UI_Confirm("Clôturer le mois " & mk & " ?" & vbLf & "Dépenses, caisse et stocks du mois seront verrouillés. Une sauvegarde sera effectuée.", "Clôture mensuelle", "Clôturer le mois") Then Exit Sub
    DB_BeginTx "T_CLOTURES|T_PARAM|T_AUDIT|T_SEQ"
    DB_Insert "T_CLOTURES", NewRecord("TypeCloture", "Mensuelle", "Periode", mk, "DateCloture", App_Today(), "Tonnage", Ops_Tonnage(d1, d2), _
                                      "NbRotations", DB_CountIf("T_ROTATIONS", "Statut", "=", ST_VALIDEE, "DateRotation", ">=", d1, "DateRotation", "<=", d2), _
                                      "EnAttente", 0, "Anomalies", 0, "Statut", "Clôturée", "Commentaire", "Clôture mensuelle " & mk)
    Param_Set "CLOTURE_DERNIER_MOIS", mk, , "Exploitation", "Texte"
    Log_Audit "CLOTURE-MOIS", "T_CLOTURES", mk, ""
    DB_Commit
    On Error Resume Next
    Backup_Run "Clôture", True
    On Error GoTo 0
    UI_Alert "Mois " & mk & " clôturé.", "success", "Clôture mensuelle"
End Sub

Public Sub Close_Reopen(ByVal clotureId As Long)
    Dim c As Object, reason As String
    Sec_Require "CLOTURES", "V"
    If Sec_CurrentProfile() <> "Administrateur" Then Fail "Réouverture réservée à l'administrateur.", ERR_RIGHTS
    Set c = DB_Get("T_CLOTURES", clotureId)
    If c("Statut") <> "Clôturée" Then Fail "Clôture déjà réouverte."
    reason = UI_AskReason("Motif de réouverture de la période " & c("Periode") & " :", "Réouverture", "Réouvrir")
    If Len(reason) = 0 Then Exit Sub
    DB_BeginTx "T_CLOTURES|T_PARAM|T_AUDIT"
    DB_Update "T_CLOTURES", clotureId, NewRecord("Statut", "Réouverte", "Commentaire", Trim$(ToStr(c("Commentaire")) & " [Réouverte : " & reason & "]"))
    If c("TypeCloture") = "Journalière" Then
        If ToDate(c("Periode")) <= ToDate(Param_Get("CLOTURE_DERNIER_JOUR", "")) Then Param_Set "CLOTURE_DERNIER_JOUR", IsoDate(ToDate(c("Periode")) - 1)
    Else
        If ToStr(c("Periode")) <= ToStr(Param_Get("CLOTURE_DERNIER_MOIS", "")) Then Param_Set "CLOTURE_DERNIER_MOIS", MonthKey(MonthFromKey(c("Periode")) - 1)
    End If
    Log_Audit "REOUVERTURE", "T_CLOTURES", clotureId, c("Periode") & " : " & reason
    DB_Commit
End Sub

'------------------------------------------------------------------ Controle de coherence
' Retourne un tableau (n x 3) : Gravite | Controle | Detail
Public Function Check_Coherence() As Variant
    Dim res As New Collection, t As Variant, f As cFieldDef, data As Variant, r As Long, c As Long, n As Long
    Dim out() As Variant, i As Long, v As Variant
    Dim idSets As Object
    Set idSets = NewDict()
    Status "Contrôle de cohérence..."
    ' 1. references orphelines
    For Each t In Schema_AllTables()
        If Left$(t, 2) = "T_" And t <> "T_SCHEMA" And t <> "T_TABLES" And t <> "T_MODULES" Then
            data = DB_Data(CStr(t))
            For Each f In Schema_Fields(CStr(t))
                If f.FType = "FK" Then
                    c = DB_ColIdx(CStr(t), f.Name)
                    n = 0
                    If Not idSets.Exists(f.FKTable) Then idSets.Add f.FKTable, DB_Distinct(f.FKTable, "ID")
                    For r = 1 To ArrayRows(data)
                        If Not IsBlank(data(r, c)) Then
                            If Not idSets(f.FKTable).Exists(ToStr(data(r, c))) Then n = n + 1
                        End If
                    Next r
                    If n > 0 Then res.Add Array("ERREUR", "Référence orpheline", n & " ligne(s) de " & t & "." & f.Name & " pointent vers un enregistrement absent de " & f.FKTable)
                End If
            Next f
            If DB_HasCol(CStr(t), "ID") Then
                If DB_Distinct(CStr(t), "ID").Count <> DB_Count(CStr(t)) Then res.Add Array("ERREUR", "Identifiants en double", CStr(t))
            End If
        End If
    Next t
    ' 2. poids nets
    data = DB_Data("T_ROTATIONS")
    n = 0
    For r = 1 To ArrayRows(data)
        If Abs(ToDbl(data(r, DB_ColIdx("T_ROTATIONS", "PoidsBrut"))) - ToDbl(data(r, DB_ColIdx("T_ROTATIONS", "Tare"))) - ToDbl(data(r, DB_ColIdx("T_ROTATIONS", "PoidsNet")))) > 0.011 Then n = n + 1
    Next r
    If n > 0 Then res.Add Array("ERREUR", "Poids net incohérent", n & " rotation(s) : net <> brut - tare")
    ' 3. factures : empreinte, paiements, rotations
    data = DB_Data("T_FACTURES")
    For r = 1 To ArrayRows(data)
        Dim fid As Long, paid As Double, ton As Double
        fid = ToLng(data(r, DB_ColIdx("T_FACTURES", "ID")))
        If Not Bill_VerifyFingerprint(fid) Then res.Add Array("CRITIQUE", "Facture modifiée après validation", data(r, DB_ColIdx("T_FACTURES", "NumFacture")) & " : l'empreinte d'intégrité ne correspond plus")
        If data(r, DB_ColIdx("T_FACTURES", "Statut")) <> FAC_ANNULEE And data(r, DB_ColIdx("T_FACTURES", "Statut")) <> FAC_BROUILLON Then
            paid = DB_Sum("T_PAIEMENTS", "Montant", "FactureID", "=", fid, "Statut", "<>", "Annulé")
            If Abs(paid - ToDbl(data(r, DB_ColIdx("T_FACTURES", "MontantPaye")))) > 1 Then res.Add Array("ERREUR", "Montant réglé incohérent", data(r, DB_ColIdx("T_FACTURES", "NumFacture")))
            ton = DB_Sum("T_ROTATIONS", "PoidsNet", "FactureID", "=", fid)
            If Abs(ton - ToDbl(data(r, DB_ColIdx("T_FACTURES", "Tonnage")))) > 0.05 Then res.Add Array("ERREUR", "Tonnage facturé <> rotations rattachées", _
                data(r, DB_ColIdx("T_FACTURES", "NumFacture")) & " : " & FmtNum(data(r, DB_ColIdx("T_FACTURES", "Tonnage")), 2) & " t facturées / " & FmtNum(ton, 2) & " t rattachées")
        End If
    Next r
    n = DB_CountIf("T_ROTATIONS", "FactureID", "NOTBLANK", "", "Statut", "<>", ST_VALIDEE)
    If n > 0 Then res.Add Array("ERREUR", "Rotation facturée non validée", n & " rotation(s)")
    ' 4. pneus : deux pneus a la meme position
    Dim pos As Object
    Set pos = DB_Distinct("T_PNEUS", "CamionID", "Statut", "=", PN_MONTE)
    data = DB_Data("T_PNEUS")
    Dim seen As Object
    Set seen = NewDict()
    For r = 1 To ArrayRows(data)
        If data(r, DB_ColIdx("T_PNEUS", "Statut")) = PN_MONTE Then
            v = data(r, DB_ColIdx("T_PNEUS", "CamionID")) & "|" & data(r, DB_ColIdx("T_PNEUS", "Position"))
            If seen.Exists(v) Then res.Add Array("ERREUR", "Position pneu en double", v) Else seen(v) = 1
        End If
    Next r
    ' 5. stocks negatifs
    n = DB_CountIf("T_ARTICLES", "Stock", "<", 0)
    If n > 0 Then res.Add Array("ERREUR", "Stock négatif", n & " article(s)")
    If Fuel_TankStock() < 0 Then res.Add Array("ALERTE", "Stock cuve négatif", FmtNum(Fuel_TankStock(), 0) & " L : approvisionnements manquants ?")
    ' 6. location : montant = heures x tarif
    data = DB_Data("T_LOCATION")
    n = 0
    For r = 1 To ArrayRows(data)
        If Abs(ToDbl(data(r, DB_ColIdx("T_LOCATION", "HeuresRetenues"))) * ToDbl(data(r, DB_ColIdx("T_LOCATION", "TarifHoraire"))) - ToDbl(data(r, DB_ColIdx("T_LOCATION", "Montant")))) > 1 Then n = n + 1
    Next r
    If n > 0 Then res.Add Array("ERREUR", "Montant de location incohérent", n & " relevé(s)")
    ' 7. caisse
    If Fin_CashBalance() < 0 Then res.Add Array("ERREUR", "Solde de caisse négatif", FmtMoney(Fin_CashBalance()))
    ' 8. affectations qui se chevauchent
    data = DB_Data("T_AFFECTATIONS")
    Dim r2 As Long, a1 As Date, b1 As Date, a2 As Date, b2 As Date
    For r = 1 To ArrayRows(data)
        For r2 = r + 1 To ArrayRows(data)
            If data(r, DB_ColIdx("T_AFFECTATIONS", "ChauffeurID")) = data(r2, DB_ColIdx("T_AFFECTATIONS", "ChauffeurID")) Then
                a1 = ToDate(data(r, DB_ColIdx("T_AFFECTATIONS", "DateDebut"))): b1 = ToDate(data(r, DB_ColIdx("T_AFFECTATIONS", "DateFin")))
                a2 = ToDate(data(r2, DB_ColIdx("T_AFFECTATIONS", "DateDebut"))): b2 = ToDate(data(r2, DB_ColIdx("T_AFFECTATIONS", "DateFin")))
                If b1 = 0 Then b1 = DateSerial(2999, 1, 1)
                If b2 = 0 Then b2 = DateSerial(2999, 1, 1)
                If a1 <= b2 And a2 <= b1 Then res.Add Array("ERREUR", "Affectations qui se chevauchent", CodeOf("T_CHAUFFEURS", data(r, DB_ColIdx("T_AFFECTATIONS", "ChauffeurID"))))
            End If
        Next r2
    Next r
    ' 9. depenses en categories reservees
    n = DB_CountIf("T_DEPENSES", "Categorie", "IN", "Carburant|Pneumatiques|Location camions", "Responsable", "=", RESP_EXPL, "Statut", "=", ST_VALIDEE)
    If n > 0 Then res.Add Array("ALERTE", "Risque de double comptabilisation", n & " dépense(s) carburant/pneus/location validées hors modules dédiés")
    Status "Contrôle terminé"
    If res.Count = 0 Then
        ReDim out(1 To 1, 1 To 3)
        out(1, 1) = "OK": out(1, 2) = "Aucune anomalie détectée": out(1, 3) = DB_Count("T_ROTATIONS") & " rotations, " & DB_Count("T_FACTURES") & " factures contrôlées"
    Else
        ReDim out(1 To res.Count, 1 To 3)
        For Each v In res
            i = i + 1
            out(i, 1) = v(0): out(i, 2) = v(1): out(i, 3) = v(2)
        Next v
    End If
    Log_Audit "CONTROLE-COHERENCE", "", "", res.Count & " anomalie(s)"
    Check_Coherence = out
End Function
