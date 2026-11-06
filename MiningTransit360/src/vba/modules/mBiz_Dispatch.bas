Attribute VB_Name = "mBiz_Dispatch"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Dispatch
' Aiguillage des regles metier par table (appele par mBiz_Records).
' Ajouter une regle = completer le Select Case de la table concernee.
'==============================================================================
Option Explicit

' Tables modifiees par un enregistrement (pour la transaction / rollback)
Public Function Biz_TxTables(ByVal tableName As String) As String
    Dim s As String
    s = tableName & "|T_SEQ|T_AUDIT"
    Select Case tableName
        Case "T_ROTATIONS": s = s & "|T_PESEES"
        Case "T_PESEES": s = s & "|T_ROTATIONS"
        Case "T_CARBURANT": s = s & "|T_PARAM"
        Case "T_PNEUS": s = s & "|T_PNEU_MVT"
        Case "T_STOCK_MVT": s = s & "|T_ARTICLES"
        Case "T_INCIDENTS": s = s & "|T_CAMIONS"
        Case "T_CAISSE": s = s
        Case "T_PAIEMENTS": s = s & "|T_FACTURES"
        Case "T_FACTURES": s = s & "|T_ROTATIONS|T_FACTURE_LIGNES"
        Case "T_AFFECTATIONS": s = s & "|T_CAMIONS"
        Case "T_MAINTENANCE": s = s & "|T_INCIDENTS"
    End Select
    Biz_TxTables = s
End Function

Public Sub Biz_BeforeSave(ByVal tableName As String, ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    Select Case tableName
        Case "T_CAMIONS": Fleet_BeforeSaveTruck rec, isNew, old
        Case "T_CHAUFFEURS": Fleet_BeforeSaveDriver rec, isNew, old
        Case "T_AFFECTATIONS": Fleet_BeforeSaveAssignment rec, isNew
        Case "T_PLANNING": Fleet_BeforeSavePlanning rec, isNew
        Case "T_ROTATIONS": Ops_BeforeSaveRotation rec, isNew, old
        Case "T_PESEES": Ops_BeforeSaveTicket rec, isNew, old
        Case "T_OBJECTIFS": Ops_BeforeSaveObjective rec
        Case "T_CARBURANT": Fuel_BeforeSave rec, isNew
        Case "T_LOCATION": Rent_BeforeSave rec, isNew
        Case "T_PNEUS": Tire_BeforeSave rec, isNew, old
        Case "T_ARTICLES": Stock_BeforeSaveArticle rec, isNew
        Case "T_STOCK_MVT": Stock_BeforeSaveMove rec, isNew, old
        Case "T_INCIDENTS": Mnt_BeforeSaveIncident rec, isNew, old
        Case "T_MAINTENANCE": Mnt_BeforeSaveOT rec, isNew
        Case "T_DEPENSES": Fin_BeforeSaveExpense rec, isNew, old
        Case "T_CAISSE": Fin_BeforeSaveCash rec, isNew, old
        Case "T_AVANCES": Fin_BeforeSaveAdvance rec, isNew
        Case "T_PAIEMENTS": Bill_BeforeSavePayment rec, isNew
        Case "T_RELANCES": Bill_BeforeSaveReminder rec
        Case "T_DOCUMENTS": Doc_BeforeSave rec
        Case "T_USERS": Admin_BeforeSaveUser rec, isNew, old
        Case "T_PARAM": Admin_BeforeSaveParam rec, old
        Case "T_CLIENTS", "T_FOURNISSEURS", "T_LIEUX"
            If tableName = "T_LIEUX" Then
                If ToStr(rec("TypeLieu")) = "Déchargement" And IsBlank(rec("ClientID")) Then _
                    Fail "Un lieu de déchargement doit être rattaché à un client (sert à la facturation)."
            End If
    End Select
End Sub

Public Sub Biz_AfterSave(ByVal tableName As String, ByVal id As Long, ByVal isNew As Boolean, ByVal old As Object)
    Select Case tableName
        Case "T_ROTATIONS": Ops_SyncTicketFromRotation id
        Case "T_PESEES": Ops_SyncRotationFromTicket id
        Case "T_CARBURANT": Fuel_RefreshTank
        Case "T_PNEUS": If isNew Then Tire_LogEntry id
        Case "T_STOCK_MVT": Stock_Recompute ToLng(DB_Value("T_STOCK_MVT", id, "ArticleID"))
        Case "T_INCIDENTS": Mnt_SyncTruckStatus ToLng(DB_Value("T_INCIDENTS", id, "CamionID"))
        Case "T_CAISSE": Fin_RecomputeCash
        Case "T_PAIEMENTS": Bill_Recompute ToLng(DB_Value("T_PAIEMENTS", id, "FactureID"))
        Case "T_AFFECTATIONS": Fleet_AfterAssignment id
        Case "T_USERS": If isNew Then Admin_AfterNewUser id
        Case "T_DROITS": Sec_ReloadRights
        Case "T_PARAM": Schema_Reset
    End Select
End Sub

Public Sub Biz_BeforeDelete(ByVal tableName As String, ByVal id As Long, ByVal old As Object)
    Select Case tableName
        Case "T_FACTURES": Bill_ReleaseRotations id: Bill_DeleteLines id
        Case "T_ROTATIONS"
            Dim r As Long
            r = DB_RowOf("T_PESEES", "RotationID", id)
            If r > 0 Then DB_Delete "T_PESEES", DB_Data("T_PESEES")(r, DB_ColIdx("T_PESEES", "ID"))
    End Select
End Sub

Public Sub Biz_AfterDelete(ByVal tableName As String, ByVal id As Long, ByVal old As Object)
    Select Case tableName
        Case "T_STOCK_MVT": Stock_Recompute ToLng(old("ArticleID"))
        Case "T_CARBURANT": Fuel_RefreshTank
        Case "T_CAISSE": Fin_RecomputeCash
        Case "T_INCIDENTS": Mnt_SyncTruckStatus ToLng(old("CamionID"))
    End Select
End Sub

Public Sub Biz_BeforeValidate(ByVal tableName As String, ByVal rec As Object)
    Select Case tableName
        Case "T_ROTATIONS": Ops_CheckRotationValidable rec
        Case "T_DEPENSES"
            If IsBlank(rec("Libelle")) Or ToDbl(rec("Montant")) <= 0 Then Fail "Dépense incomplète : libellé et montant requis."
    End Select
End Sub

Public Sub Biz_AfterStatus(ByVal tableName As String, ByVal id As Long, ByVal newStatus As String, ByVal reason As String)
    Select Case tableName
        Case "T_ROTATIONS": Ops_SyncTicketFromRotation id
        Case "T_CARBURANT": Fuel_RefreshTank
    End Select
End Sub

' Calculs en direct pour les formulaires (aucune ecriture, aucune erreur bloquante)
Public Function Biz_LivePreview(ByVal tableName As String, ByVal rec As Object) As Object
    Dim out As Object
    Set out = NewDict()
    On Error Resume Next
    Select Case tableName
        Case "T_ROTATIONS", "T_PESEES"
            If Not IsBlank(rec("PoidsBrut")) And Not IsBlank(rec("Tare")) Then out("PoidsNet") = Round(ToDbl(rec("PoidsBrut")) - ToDbl(rec("Tare")), 2)
        Case "T_CARBURANT"
            out("Montant") = Round(ToDbl(rec("Litres")) * ToDbl(rec("PrixLitre")), 0)
        Case "T_LOCATION"
            Dim calc As Object
            Set calc = Rent_Compute(rec)
            out("HeuresBrutes") = calc("HeuresBrutes")
            out("HeuresRetenues") = calc("HeuresRetenues")
            out("TarifHoraire") = calc("TarifHoraire")
            out("Montant") = calc("Montant")
        Case "T_STOCK_MVT"
            out("Montant") = Round(ToDbl(rec("Quantite")) * ToDbl(rec("PU")), 0)
        Case "T_AVANCES"
            out("Solde") = ToDbl(rec("Montant")) - ToDbl(rec("MontantRembourse"))
        Case "T_INCIDENTS"
            If Not IsBlank(rec("DateReprise")) And Not IsBlank(rec("DebutImmob")) Then
                out("DureeImmobH") = HoursBetween(ToDate(rec("DebutImmob")), TimeOr0(rec("HeureDebutImmob")), _
                                                  ToDate(rec("DateReprise")), TimeOr0(rec("HeureReprise")))
            End If
    End Select
    Set Biz_LivePreview = out
End Function

' Remplissage automatique des formulaires (valeurs affichees, uniquement si le champ est vide)
Public Function Biz_AutoFill(ByVal tableName As String, ByVal rec As Object) As Object
    Dim out As Object, dc As String, d As Date, ch As Long, cli As Long
    Set out = NewDict()
    On Error Resume Next
    Select Case tableName
        Case "T_ROTATIONS": dc = "DateRotation"
        Case "T_CARBURANT": dc = "DateOp"
        Case "T_INCIDENTS": dc = "DateIncident"
        Case "T_PLANNING": dc = "DatePlan"
    End Select
    If Len(dc) > 0 Then
        If Not IsBlank(rec("CamionID")) And IsBlank(rec("ChauffeurID")) Then
            d = ToDate(rec(dc))
            If d = 0 Then d = App_Today()
            ch = Fleet_DriverFor(ToLng(rec("CamionID")), d)
            If ch > 0 Then out("ChauffeurID") = CodeOf("T_CHAUFFEURS", ch)
        End If
    End If
    If tableName = "T_ROTATIONS" Then
        If Not IsBlank(rec("DestinationID")) And IsBlank(rec("ClientID")) Then
            cli = ToLng(DB_Value("T_LIEUX", rec("DestinationID"), "ClientID"))
            If cli > 0 Then out("ClientID") = CodeOf("T_CLIENTS", cli)
        End If
    End If
    Set Biz_AutoFill = out
End Function
