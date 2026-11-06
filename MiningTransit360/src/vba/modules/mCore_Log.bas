Attribute VB_Name = "mCore_Log"
'==============================================================================
' MINING TRANSIT 360 - mCore_Log
' Journal d'audit (T_AUDIT) et journal technique des erreurs (fichier texte).
' Toute operation sensible (creation, modification, validation, suppression,
' facturation, restauration, connexion...) est tracee.
'==============================================================================
Option Explicit

Public Sub Log_Audit(ByVal action As String, Optional ByVal tableName As String = "", _
                     Optional ByVal recId As Variant = "", Optional ByVal details As String = "")
    On Error GoTo Fallback
    DB_Insert "T_AUDIT", NewRecord("Horodatage", Stamp(), "Utilisateur", Sec_CurrentLogin(), "Action", action, _
                                   "TableCible", tableName, "EnregID", ToStr(recId), "Details", Left$(details, 500)), False
    Exit Sub
Fallback:
    Log_File "AUDIT-ECHEC " & action & " " & tableName & " " & ToStr(recId) & " : " & Err.Description
End Sub

' Description lisible des differences entre deux versions d'un enregistrement
Public Function Log_Diff(ByVal oldRec As Object, ByVal newRec As Object) As String
    Dim k As Variant, s As String
    If oldRec Is Nothing Then Exit Function
    For Each k In newRec.Keys
        If oldRec.Exists(k) Then
            If CompareValues(oldRec(k), newRec(k)) <> 0 Then
                If InStr(1, "|ModifPar|ModifLe|CreePar|CreeLe|", "|" & k & "|", vbTextCompare) = 0 Then
                    s = s & k & ": " & Left$(ToStr(oldRec(k)), 40) & " -> " & Left$(ToStr(newRec(k)), 40) & "; "
                End If
            End If
        End If
    Next k
    Log_Diff = s
End Function

Public Sub Log_File(ByVal msg As String)
    On Error Resume Next
    Dim f As Integer, p As String
    p = AppFolder() & Application.PathSeparator & "MT360_journal.log"
    f = FreeFile
    Open p For Append As #f
    Print #f, Stamp() & vbTab & Sec_CurrentLogin() & vbTab & msg
    Close #f
End Sub

' Gestion centralisee des erreurs des points d'entree UI
Public Sub Log_Error(ByVal source As String, ByVal errNum As Long, ByVal errDesc As String, Optional ByVal silent As Boolean = False)
    If DB_InTx() Then DB_Rollback
    ScreenOn True
    If errNum = ERR_CANCEL Then Exit Sub
    If IsBizError(errNum) Then
        If Not silent Then UI_Alert errDesc, "warning", "Contrôle"
    Else
        Log_File "ERREUR " & source & " #" & errNum & " : " & errDesc
        If Not silent Then UI_Alert "Une erreur inattendue s'est produite dans « " & source & " »." & vbLf & vbLf & _
                                    errDesc & " (n° " & errNum & ")" & vbLf & vbLf & _
                                    "L'opération a été annulée. Le détail est consigné dans MT360_journal.log.", "error", "Erreur"
    End If
End Sub
