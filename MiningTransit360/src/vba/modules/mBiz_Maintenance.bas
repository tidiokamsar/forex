Attribute VB_Name = "mBiz_Maintenance"
'==============================================================================
' MINING TRANSIT 360 - mBiz_Maintenance
' Incidents, immobilisations, ordres de travail, documents.
'
' Responsabilite contractuelle : la maintenance mecanique contractuelle est a
' la charge d'Atiko. Le cout d'un incident n'est impute a l'exploitant que si
' le responsable est "Exploitant" (CoutExploitant). Les couts Atiko sont suivis
' "pour memoire" et ne sont JAMAIS integres a la marge de l'exploitant.
'==============================================================================
Option Explicit

Public Sub Mnt_BeforeSaveIncident(ByVal rec As Object, ByVal isNew As Boolean, ByVal old As Object)
    Dim d1 As Date, d2 As Date
    If ToDate(rec("DateIncident")) > App_Today() Then Fail "Date d'incident dans le futur."
    If IsBlank(rec("ChauffeurID")) Then
        If Fleet_DriverFor(ToLng(rec("CamionID")), ToDate(rec("DateIncident"))) > 0 Then rec("ChauffeurID") = Fleet_DriverFor(ToLng(rec("CamionID")), ToDate(rec("DateIncident")))
    End If
    If ToStr(rec("Immobilisation")) = "Oui" Then
        If IsBlank(rec("DebutImmob")) Then rec("DebutImmob") = rec("DateIncident")
        If IsBlank(rec("HeureDebutImmob")) Then rec("HeureDebutImmob") = TimeSerial(8, 0, 0)
        d1 = ToDate(rec("DebutImmob"))
        If Not IsBlank(rec("DateReprise")) Then
            d2 = ToDate(rec("DateReprise"))
            rec("DureeImmobH") = HoursBetween(d1, TimeOr0(rec("HeureDebutImmob")), d2, TimeOr0(rec("HeureReprise")))
            If rec("DureeImmobH") < 0 Then Fail "La reprise ne peut pas précéder le début de l'immobilisation."
        Else
            rec("DureeImmobH") = Empty
        End If
    Else
        rec("DebutImmob") = Empty: rec("HeureDebutImmob") = Empty
        rec("DureeImmobH") = 0
    End If
    If ToStr(rec("Responsable")) = RESP_EXPL Then rec("CoutExploitant") = ToDbl(rec("Cout")) Else rec("CoutExploitant") = 0
    If isNew Then
        If ToStr(rec("Immobilisation")) = "Oui" And IsBlank(rec("DateReprise")) Then rec("Statut") = "En cours" Else rec("Statut") = "Ouvert"
    End If
End Sub

' Met a jour le statut du camion selon les immobilisations ouvertes
Public Sub Mnt_SyncTruckStatus(ByVal cam As Long)
    Dim rows As Collection, data As Variant, r As Variant, st As String, newSt As String, typ As String
    If cam = 0 Then Exit Sub
    st = ToStr(DB_Value("T_CAMIONS", cam, "Statut"))
    If st = CAM_HS Then Exit Sub
    Set rows = DB_Rows("T_INCIDENTS", "CamionID", "=", cam, "Immobilisation", "=", "Oui", "DateReprise", "ISBLANK", "", "Statut", "<>", "Clôturé")
    data = DB_Data("T_INCIDENTS")
    If rows.Count > 0 Then
        newSt = CAM_IMMO
        For Each r In rows
            typ = ToStr(data(r, DB_ColIdx("T_INCIDENTS", "TypeIncident")))
            If Left$(typ, 5) = "Panne" Then newSt = CAM_PANNE
        Next r
    Else
        If Fleet_ActiveAssignment(cam, App_Today()) > 0 Then newSt = CAM_SERVICE Else newSt = CAM_DISPO
    End If
    If newSt <> st Then
        DB_SetValue "T_CAMIONS", cam, "Statut", newSt
        Log_Audit "STATUT-CAMION", "T_CAMIONS", cam, st & " -> " & newSt
    End If
End Sub

Public Sub Mnt_SetInProgress(ByVal id As Long)
    Dim i As Object
    Set i = DB_Get("T_INCIDENTS", id)
    If i("Statut") = "Clôturé" Then Fail "Incident déjà clôturé."
    Rec_Save "T_INCIDENTS", NewRecord("Statut", "En cours"), id
End Sub

' Cloture d'un incident (workflow) : reprise, intervention, responsable, cout
Public Sub Mnt_CloseDialog(ByVal id As Long)
    Dim i As Object, f As frmInput, rec As Object
    Sec_Require "INCIDENTS", "V"
    Set i = DB_Get("T_INCIDENTS", id)
    If i Is Nothing Then Fail "Incident introuvable."
    If i("Statut") = "Clôturé" Then Fail "Incident déjà clôturé."
    Set f = Dlg_New("Clôture de l'incident " & i("NumIncident"), CodeOf("T_CAMIONS", i("CamionID")) & " - " & i("TypeIncident"), CLR_RED(), 560)
    If i("Immobilisation") = "Oui" Then
        f.AddField "DR", "Date de reprise", "DATE", App_Today(), , True
        f.AddField "HR", "Heure de reprise", "TIME", Format$(Time, "hh:mm"), , True
    End If
    f.AddField "RESP", "Responsable contractuel", "LIST", IIf(i("Responsable") = "À déterminer", "", i("Responsable")), _
               Array(RESP_ATIKO, RESP_EXPL, "Tiers / assurance"), True
    f.AddField "INT", "Intervention réalisée", "MEMO", ToStr(i("Intervention")), , True
    f.AddField "COUT", "Coût total (GNF)", "MONEY", ToStr(i("Cout")), , True, "Imputé à l'exploitant uniquement si le responsable est « Exploitant »"
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "OK", "Clôturer", CLR_RED(), True
    If f.ShowDialog = "OK" Then
        Set rec = NewRecord("Responsable", f.FieldValue("RESP"), "Intervention", f.FieldValue("INT"), "Cout", f.FieldValue("COUT"), "Statut", "Clôturé")
        If i("Immobilisation") = "Oui" Then
            rec("DateReprise") = f.FieldValue("DR")
            rec("HeureReprise") = f.FieldValue("HR")
        End If
        f.Dispose
        Rec_Save "T_INCIDENTS", rec, id
        Log_Audit "CLOTURE-INCIDENT", "T_INCIDENTS", id, "Responsable : " & rec("Responsable")
    Else
        f.Dispose
    End If
End Sub

Public Sub Mnt_BeforeSaveOT(ByVal rec As Object, ByVal isNew As Boolean)
    If Not IsBlank(rec("DateFin")) And Not IsBlank(rec("DateDebut")) Then
        If ToDate(rec("DateFin")) < ToDate(rec("DateDebut")) Then Fail "La date de fin précède la date de début."
    End If
    If Not IsBlank(rec("IncidentID")) Then
        If ToLng(DB_Value("T_INCIDENTS", rec("IncidentID"), "CamionID")) <> ToLng(rec("CamionID")) Then Fail "L'incident lié concerne un autre camion."
    End If
    If ToStr(rec("Statut")) = "Terminée" And IsBlank(rec("DateFin")) Then rec("DateFin") = App_Today()
End Sub

Public Sub Mnt_CompleteOT(ByVal id As Long)
    Dim f As frmInput, o As Object
    Set o = DB_Get("T_MAINTENANCE", id)
    If o("Statut") = "Terminée" Then Fail "Intervention déjà terminée."
    Set f = Dlg_New("Fin d'intervention " & o("NumOT"), CodeOf("T_CAMIONS", o("CamionID")), CLR_RED())
    f.AddField "DF", "Date de fin", "DATE", App_Today(), , True
    f.AddField "COUT", "Coût (GNF)", "MONEY", ToStr(o("Cout")), , True
    f.AddAction "NO", "Annuler", CLR_GREY(), False, True
    f.AddAction "OK", "Terminer", CLR_RED(), True
    If f.ShowDialog = "OK" Then
        Dim rec As Object
        Set rec = NewRecord("DateFin", f.FieldValue("DF"), "Cout", f.FieldValue("COUT"), "Statut", "Terminée")
        f.Dispose
        Rec_Save "T_MAINTENANCE", rec, id
    Else
        f.Dispose
    End If
End Sub

' Heures d'immobilisation (incidents) d'une periode, par responsable ("" = tous)
Public Function Mnt_ImmobHours(ByVal d1 As Date, ByVal d2 As Date, Optional ByVal resp As String = "") As Double
    If Len(resp) = 0 Then
        Mnt_ImmobHours = DB_Sum("T_LOCATION", "HeuresImmobAtiko", "DateReleve", ">=", d1, "DateReleve", "<=", d2) + _
                         DB_Sum("T_LOCATION", "HeuresImmobExploitant", "DateReleve", ">=", d1, "DateReleve", "<=", d2)
    ElseIf resp = RESP_ATIKO Then
        Mnt_ImmobHours = DB_Sum("T_LOCATION", "HeuresImmobAtiko", "DateReleve", ">=", d1, "DateReleve", "<=", d2)
    Else
        Mnt_ImmobHours = DB_Sum("T_LOCATION", "HeuresImmobExploitant", "DateReleve", ">=", d1, "DateReleve", "<=", d2)
    End If
End Function

'------------------------------------------------------------------ Documents
Public Sub Doc_BeforeSave(ByVal rec As Object)
    If Not IsBlank(rec("DateExpiration")) And Not IsBlank(rec("DateEmission")) Then
        If ToDate(rec("DateExpiration")) < ToDate(rec("DateEmission")) Then Fail "La date d'expiration précède la date d'émission."
    End If
    ' Synchronisation des echeances sur les fiches
    Select Case ToStr(rec("Entite"))
        Case "Camion"
            Dim cam As Long
            cam = IdOf("T_CAMIONS", ToStr(rec("EntiteRef")))
            If cam = 0 Then Fail "Camion inconnu : " & rec("EntiteRef")
            If rec("TypeDoc") = "Assurance" And Not IsBlank(rec("DateExpiration")) Then DB_SetValue "T_CAMIONS", cam, "AssuranceExp", rec("DateExpiration")
            If rec("TypeDoc") = "Visite technique" And Not IsBlank(rec("DateExpiration")) Then DB_SetValue "T_CAMIONS", cam, "VisiteTechExp", rec("DateExpiration")
        Case "Chauffeur"
            Dim ch As Long
            ch = IdOf("T_CHAUFFEURS", ToStr(rec("EntiteRef")))
            If ch = 0 Then Fail "Chauffeur inconnu : " & rec("EntiteRef")
            If rec("TypeDoc") = "Permis" And Not IsBlank(rec("DateExpiration")) Then DB_SetValue "T_CHAUFFEURS", ch, "ExpPermis", rec("DateExpiration")
    End Select
End Sub

' Joindre un justificatif : copie du fichier dans le dossier Justificatifs (chemin relatif conserve)
Public Function Doc_Attach(ByVal tableName As String, ByVal id As Long, ByVal col As String) As String
    Dim src As Variant, dest As String, fname As String, folder As String
    src = Application.GetOpenFilename("Justificatifs (*.pdf;*.jpg;*.jpeg;*.png;*.tif;*.docx;*.xlsx),*.pdf;*.jpg;*.jpeg;*.png;*.tif;*.docx;*.xlsx", , "Choisir le justificatif")
    If VarType(src) = vbBoolean Then Exit Function
    folder = EnsureFolder(SubFolder("DOSSIER_JUSTIFICATIFS", "Justificatifs") & Application.PathSeparator & Replace(tableName, "T_", ""))
    fname = Format$(Now, "yyyymmdd_hhnnss") & "_" & id & "_" & SafeFileName(FSO().GetFileName(src))
    dest = folder & Application.PathSeparator & fname
    FSO().CopyFile src, dest, False
    DB_SetValue tableName, id, col, dest
    Log_Audit "JUSTIFICATIF", tableName, id, dest
    Doc_Attach = dest
End Function
