Attribute VB_Name = "mBiz_Records"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Records
' Pipeline unique d'enregistrement de toute fiche :
'   1. verrous (cloture, enregistrement valide/facture)
'   2. valeurs par defaut + numerotation
'   3. controles generiques issus du schema (obligatoire, unicite, minimum, liste)
'   4. regles metier specifiques a la table (modules mBiz_*)
'   5. ecriture transactionnelle + effets de bord (AfterSave) + audit
' Les formulaires ne contiennent AUCUNE regle metier : ils appellent Rec_Save.
'==============================================================================
Option Explicit

' Tables operationnelles soumises a la cloture journaliere (table -> colonne date)
Public Function Rec_DateCol(ByVal tableName As String) As String
    Select Case tableName
        Case "T_ROTATIONS": Rec_DateCol = "DateRotation"
        Case "T_PESEES": Rec_DateCol = "DatePesee"
        Case "T_CARBURANT": Rec_DateCol = "DateOp"
        Case "T_LOCATION": Rec_DateCol = "DateReleve"
        Case "T_DEPENSES": Rec_DateCol = "DateDep"
        Case "T_CAISSE": Rec_DateCol = "DateOp"
        Case "T_STOCK_MVT": Rec_DateCol = "DateMvt"
    End Select
End Function

Public Function Rec_IsDayClosed(ByVal d As Date) As Boolean
    Dim lastDay As Date
    lastDay = ToDate(Param_Get("CLOTURE_DERNIER_JOUR", ""))
    If lastDay = 0 Then Exit Function
    Rec_IsDayClosed = (d <= lastDay)
End Function

Public Function Rec_IsMonthClosed(ByVal d As Date) As Boolean
    Dim m As String
    m = ToStr(Param_Get("CLOTURE_DERNIER_MOIS", ""))
    If Len(m) = 0 Then Exit Function
    Rec_IsMonthClosed = (MonthKey(d) <= m)
End Function

' Verifie qu'une periode cloturee n'est pas touchee
Public Sub Rec_CheckPeriodOpen(ByVal tableName As String, ByVal d As Date)
    Select Case tableName
        Case "T_ROTATIONS", "T_PESEES", "T_CARBURANT", "T_LOCATION"
            If Rec_IsDayClosed(d) Then Fail "La journée du " & FmtDate(d) & " est clôturée. " & _
                "Les saisies sont verrouillées (réouverture possible par un administrateur, module Clôtures)."
        Case "T_DEPENSES", "T_CAISSE", "T_STOCK_MVT"
            If Rec_IsMonthClosed(d) Then Fail "Le mois " & MonthKey(d) & " est clôturé. Saisie impossible sur cette période."
    End Select
End Sub

' Un enregistrement peut-il etre modifie ? (leve une erreur sinon)
Public Sub Rec_CheckEditable(ByVal tableName As String, ByVal old As Object)
    Dim dc As String
    If old Is Nothing Then Exit Sub
    dc = Rec_DateCol(tableName)
    Select Case tableName
        Case "T_ROTATIONS"
            If Not IsBlank(old("FactureID")) Then Fail "Rotation facturée (" & CodeOf("T_FACTURES", old("FactureID")) & ") : modification interdite. Annulez d'abord la facture."
            If old("Statut") = ST_ANNULEE Then Fail "Rotation annulée : modification interdite."
            If old("Statut") = ST_VALIDEE And Not Sec_Can("ROTATIONS", "V") Then Fail "Rotation validée : seul un valideur peut la modifier.", ERR_RIGHTS
        Case "T_PESEES"
            If old("Statut") = ST_VALIDEE Then Fail "Ticket validé : modifiez la rotation associée (le ticket sera synchronisé)."
        Case "T_CARBURANT", "T_LOCATION", "T_DEPENSES"
            If old("Statut") = ST_ANNULEE Then Fail "Enregistrement annulé : modification interdite."
            If old("Statut") = ST_VALIDEE And Not Sec_Can(Mod_ForTable(tableName), "V") Then Fail "Enregistrement validé : seul un valideur peut le modifier.", ERR_RIGHTS
        Case "T_FACTURES"
            If old("Statut") <> FAC_BROUILLON Then Fail "Facture " & old("Statut") & " : une facture validée n'est jamais modifiée. Utilisez l'annulation (avec motif) puis regénérez."
        Case "T_PAIEMENTS"
            Fail "Un encaissement ne se modifie pas : annulez-le (avec motif) et saisissez-en un nouveau."
        Case "T_INCIDENTS"
            If old("Statut") = "Clôturé" And Not Sec_Can("INCIDENTS", "V") Then Fail "Incident clôturé : modification réservée aux valideurs.", ERR_RIGHTS
        Case "T_PNEU_MVT", "T_AUDIT", "T_SAUVEGARDES", "T_CLOTURES"
            Fail "Historique non modifiable."
    End Select
    ' verrou de periode (apres les verrous propres a l'enregistrement, plus explicites)
    If Len(dc) > 0 Then Rec_CheckPeriodOpen tableName, ToDate(old(dc))
End Sub

'------------------------------------------------------------------ Valeurs par defaut
Public Function Rec_Defaults(ByVal tableName As String) As Object
    Dim d As Object, f As cFieldDef
    Set d = NewDict()
    For Each f In Schema_Fields(tableName)
        If Not IsEmpty(f.DefaultValue) Then
            Select Case f.FType
                Case "NUM", "MNY", "INT", "PCT": d(f.Name) = ToDbl(f.DefaultValue)
                Case Else: d(f.Name) = f.DefaultValue
            End Select
        ElseIf f.FType = "DATE" And f.Required Then
            d(f.Name) = App_Today()
        End If
    Next f
    Select Case tableName
        Case "T_CARBURANT": d("PrixLitre") = Param_Num("CARB_PRIX_DEFAUT", 12000)
        Case "T_LOCATION": d("HDebut") = TimeSerial(6, 30, 0): d("HFin") = TimeSerial(18, 30, 0)
        Case "T_ROTATIONS": d("HDepart") = Time
    End Select
    Set Rec_Defaults = d
End Function

'------------------------------------------------------------------ Controles generiques
Public Sub Rec_ValidateGeneric(ByVal tableName As String, ByVal rec As Object, ByVal id As Long)
    Dim f As cFieldDef, v As Variant
    For Each f In Schema_Fields(tableName)
        If rec.Exists(f.Name) Then v = rec(f.Name) Else v = Empty
        If f.Required And Not f.ReadOnly And Not f.Hidden Then
            If IsBlank(v) Then Fail "Le champ « " & f.Label & " » est obligatoire."
        End If
        If Not IsBlank(v) Then
            If f.HasMin And f.IsNumericType Then
                If ToDbl(v) < f.MinVal Then Fail "« " & f.Label & " » doit être supérieur ou égal à " & f.MinVal & "."
            End If
            If f.FType = "LIST" And Len(f.Vals) > 0 Then
                If Not InList(ToStr(v), f.Vals) Then Fail "« " & f.Label & " » : valeur non autorisée (" & ToStr(v) & ")."
            End If
            If f.FType = "FK" Then
                If DB_RowOf(f.FKTable, "ID", v) = 0 Then Fail "« " & f.Label & " » : référence inexistante."
            End If
            If f.Unique Then
                If DB_Exists(tableName, f.Name, v, IIf(id = 0, Empty, id)) Then _
                    Fail "« " & f.Label & " » : la valeur « " & ToStr(v) & " » existe déjà (doublon interdit)."
            End If
        End If
    Next f
End Sub

'------------------------------------------------------------------ Enregistrement
' rec : valeurs saisies (dictionnaire). id = 0 pour une creation. Retourne l'ID.
Public Function Rec_Save(ByVal tableName As String, ByVal rec As Object, Optional ByVal id As Long = 0) As Long
    Dim isNew As Boolean, old As Object, merged As Object, k As Variant, f As cFieldDef, dc As String, modKey As String
    isNew = (id = 0)
    modKey = Mod_ForTable(tableName)
    If Len(modKey) > 0 Then Sec_Require modKey, IIf(isNew, "C", "M")
    If isNew Then
        Set merged = Rec_Defaults(tableName)
    Else
        Set old = DB_Get(tableName, id)
        If old Is Nothing Then Fail "Enregistrement introuvable (" & tableName & " #" & id & ").", ERR_DATA
        Rec_CheckEditable tableName, old
        Set merged = DB_Get(tableName, id)
    End If
    For Each k In rec.Keys
        merged(k) = rec(k)
    Next k
    ' La periode de la nouvelle date doit etre ouverte
    dc = Rec_DateCol(tableName)
    If Len(dc) > 0 Then
        If ToDate(merged(dc)) > 0 Then Rec_CheckPeriodOpen tableName, ToDate(merged(dc))
    End If
    ' Regles metier (calculs + controles) avant les controles generiques
    Biz_BeforeSave tableName, merged, isNew, old
    Rec_ValidateGeneric tableName, merged, id
    ' Numerotation metier
    If isNew Then
        For Each f In Schema_Fields(tableName)
            If Len(f.SeqPrefix) > 0 And IsBlank(merged(f.Name)) Then
                If Len(dc) > 0 Then
                    merged(f.Name) = Seq_Number(f.SeqPrefix, ToDate(merged(dc)))
                Else
                    merged(f.Name) = Seq_Number(f.SeqPrefix, App_Today())
                End If
            End If
        Next f
    End If
    DB_BeginTx Biz_TxTables(tableName)
    If isNew Then
        merged("ID") = Empty
        id = DB_Insert(tableName, merged)
    Else
        DB_Update tableName, id, merged
    End If
    Biz_AfterSave tableName, id, isNew, old
    If isNew Then
        Log_Audit "CREATION", tableName, id, Rec_Summary(tableName, id)
    Else
        Log_Audit "MODIFICATION", tableName, id, Log_Diff(old, merged)
    End If
    DB_Commit
    Rec_Save = id
End Function

' Libelle court d'un enregistrement pour le journal
Public Function Rec_Summary(ByVal tableName As String, ByVal id As Variant) As String
    Dim d As Object, f As cFieldDef, s As String, n As Long
    Set d = DB_Get(tableName, id)
    If d Is Nothing Then Exit Function
    For Each f In Schema_Fields(tableName)
        If f.InList And f.FType <> "ID" And n < 4 Then
            s = s & f.Label & "=" & f.Display(d(f.Name)) & "; "
            n = n + 1
        End If
    Next f
    Rec_Summary = s
End Function

' Suppression controlee : dependances, statut, droits
Public Sub Rec_Delete(ByVal tableName As String, ByVal id As Long)
    Dim old As Object, usedBy As String, modKey As String
    modKey = Mod_ForTable(tableName)
    If Len(modKey) > 0 Then Sec_Require modKey, "S"
    Set old = DB_Get(tableName, id)
    If old Is Nothing Then Fail "Enregistrement introuvable.", ERR_DATA
    Select Case tableName
        Case "T_ROTATIONS", "T_CARBURANT", "T_LOCATION", "T_DEPENSES", "T_PESEES"
            If old("Statut") <> ST_SAISIE Then Fail "Seuls les enregistrements au statut « Saisie » peuvent être supprimés. " & _
                "Pour un enregistrement validé, utilisez le rejet ou l'annulation (traçabilité)."
            Rec_CheckPeriodOpen tableName, ToDate(old(Rec_DateCol(tableName)))
        Case "T_FACTURES"
            If old("Statut") <> FAC_BROUILLON Then Fail "Seule une facture au statut Brouillon peut être supprimée. Une facture validée s'annule avec un motif."
        Case "T_USERS"
            If ToStr(old("Login")) = Sec_CurrentLogin() Then Fail "Vous ne pouvez pas supprimer votre propre compte."
        Case "T_PNEUS"
            If ToStr(old("Statut")) = PN_MONTE Then Fail "Pneu monté : démontez-le avant suppression."
        Case "T_PARAM", "T_AUDIT", "T_SAUVEGARDES", "T_CLOTURES", "T_PNEU_MVT", "T_PAIEMENTS"
            Fail "Suppression interdite pour cette table (historique)."
    End Select
    If tableName <> "T_FACTURES" And tableName <> "T_ROTATIONS" Then
        If DB_IsReferenced(tableName, id, usedBy) Then
            Fail "Suppression impossible : cet enregistrement est utilisé dans « " & usedBy & " ». " & _
                 "Désactivez-le plutôt (champ Actif / Statut)."
        End If
    End If
    DB_BeginTx Biz_TxTables(tableName)
    Biz_BeforeDelete tableName, id, old
    DB_Delete tableName, id
    Biz_AfterDelete tableName, id, old
    Log_Audit "SUPPRESSION", tableName, id, Log_Diff(NewDict(), old) & " " & Rec_SummaryFromDict(tableName, old)
    DB_Commit
End Sub

Private Function Rec_SummaryFromDict(ByVal tableName As String, ByVal d As Object) As String
    Dim f As cFieldDef, s As String, n As Long
    For Each f In Schema_Fields(tableName)
        If f.InList And n < 5 Then
            s = s & f.Name & "=" & f.Display(d(f.Name)) & "; "
            n = n + 1
        End If
    Next f
    Rec_SummaryFromDict = s
End Function

'------------------------------------------------------------------ Changement de statut
' Valide / rejette / annule un enregistrement operationnel (rotations, carburant, location, depenses)
Public Sub Rec_SetStatus(ByVal tableName As String, ByVal id As Long, ByVal newStatus As String, Optional ByVal reason As String = "")
    Dim old As Object, modKey As String, upd As Object
    modKey = Mod_ForTable(tableName)
    Set old = DB_Get(tableName, id)
    If old Is Nothing Then Fail "Enregistrement introuvable.", ERR_DATA
    If newStatus = ST_VALIDEE Or newStatus = ST_REJETEE Then Sec_Require modKey, "V" Else Sec_Require modKey, "M"
    Rec_CheckPeriodOpen tableName, ToDate(old(Rec_DateCol(tableName)))
    Select Case newStatus
        Case ST_VALIDEE
            If old("Statut") <> ST_SAISIE Then Fail "Seul un enregistrement au statut « Saisie » peut être validé (statut actuel : " & old("Statut") & ")."
            Biz_BeforeValidate tableName, old
        Case ST_REJETEE
            If old("Statut") <> ST_SAISIE Then Fail "Seul un enregistrement au statut « Saisie » peut être rejeté. Un enregistrement validé doit être annulé."
            If Len(Trim$(reason)) = 0 Then Fail "Le motif de rejet est obligatoire."
        Case ST_ANNULEE
            If old("Statut") = ST_ANNULEE Then Fail "Déjà annulé."
            If tableName = "T_ROTATIONS" Then
                If Not IsBlank(old("FactureID")) Then Fail "Rotation facturée : annulation impossible (annulez d'abord la facture)."
            End If
            If Len(Trim$(reason)) = 0 Then Fail "Le motif d'annulation est obligatoire."
    End Select
    DB_BeginTx Biz_TxTables(tableName)
    Set upd = NewRecord("Statut", newStatus)
    If DB_HasCol(tableName, "ValidePar") And (newStatus = ST_VALIDEE Or newStatus = ST_REJETEE) Then upd("ValidePar") = Sec_CurrentLogin()
    If DB_HasCol(tableName, "ValideLe") And (newStatus = ST_VALIDEE Or newStatus = ST_REJETEE) Then upd("ValideLe") = App_Today()
    If Len(reason) > 0 Then
        If DB_HasCol(tableName, "MotifRejet") Then
            upd("MotifRejet") = reason
        ElseIf DB_HasCol(tableName, "Observations") Then
            upd("Observations") = Trim$(ToStr(old("Observations")) & " [" & newStatus & " : " & reason & "]")
        End If
    End If
    DB_Update tableName, id, upd
    Biz_AfterStatus tableName, id, newStatus, reason
    Log_Audit UCase$(newStatus), tableName, id, reason
    DB_Commit
End Sub
