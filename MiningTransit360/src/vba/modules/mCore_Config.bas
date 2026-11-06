Attribute VB_Name = "mCore_Config"
'==============================================================================
' MINING TRANSIT 360 - mCore_Config
' Constantes globales : version, couleurs de la charte, statuts metier,
' codes d'erreur. AUCUNE logique metier ici.
'==============================================================================
Option Explicit

Public Const APP_NAME As String = "MINING TRANSIT 360"
Public Const APP_VERSION As String = "1.0.0"
Public Function APP_TAGLINE() As String
    APP_TAGLINE = "Performance " & ChrW(8226) & " Sécurité " & ChrW(8226) & " Rentabilité"
End Function

' --- Erreurs metier (levees par les couches metier, affichees par l'UI) -------
Public Const ERR_BIZ As Long = vbObjectError + 1000      ' regle de gestion violee
Public Const ERR_DATA As Long = vbObjectError + 1100     ' integrite des donnees
Public Const ERR_RIGHTS As Long = vbObjectError + 1200   ' droits insuffisants
Public Const ERR_CANCEL As Long = vbObjectError + 1300   ' annulation utilisateur

' --- Statuts transverses -------------------------------------------------------
Public Const ST_SAISIE As String = "Saisie"
Public Const ST_VALIDEE As String = "Validée"
Public Const ST_REJETEE As String = "Rejetée"
Public Const ST_ANNULEE As String = "Annulée"

' Camions
Public Const CAM_DISPO As String = "Disponible"
Public Const CAM_SERVICE As String = "En service"
Public Const CAM_PANNE As String = "En panne"
Public Const CAM_IMMO As String = "Immobilisé"
Public Const CAM_HS As String = "Hors service"

' Factures
Public Const FAC_BROUILLON As String = "Brouillon"
Public Const FAC_VALIDEE As String = "Validée"
Public Const FAC_PARTIELLE As String = "Partielle"
Public Const FAC_PAYEE As String = "Payée"
Public Const FAC_ANNULEE As String = "Annulée"

' Pneus
Public Const PN_STOCK As String = "Stock"
Public Const PN_MONTE As String = "Monté"
Public Const PN_REPAR As String = "Réparation"
Public Const PN_REFORME As String = "Réformé"

' Responsables contractuels
Public Const RESP_ATIKO As String = "Atiko (contrat)"
Public Const RESP_EXPL As String = "Exploitant"

' --- Charte graphique (reprise de la maquette) --------------------------------
Public Function CLR_NAVY() As Long: CLR_NAVY = RGB(11, 42, 85): End Function
Public Function CLR_NAVY2() As Long: CLR_NAVY2 = RGB(18, 52, 100): End Function
Public Function CLR_NAVY3() As Long: CLR_NAVY3 = RGB(28, 66, 122): End Function
Public Function CLR_BLUE() As Long: CLR_BLUE = RGB(30, 111, 217): End Function
Public Function CLR_BLUE_D() As Long: CLR_BLUE_D = RGB(21, 82, 170): End Function
Public Function CLR_GREEN() As Long: CLR_GREEN = RGB(22, 150, 72): End Function
Public Function CLR_GREEN_D() As Long: CLR_GREEN_D = RGB(15, 112, 52): End Function
Public Function CLR_ORANGE() As Long: CLR_ORANGE = RGB(247, 148, 29): End Function
Public Function CLR_ORANGE_D() As Long: CLR_ORANGE_D = RGB(222, 112, 10): End Function
Public Function CLR_PURPLE() As Long: CLR_PURPLE = RGB(118, 62, 199): End Function
Public Function CLR_PURPLE_D() As Long: CLR_PURPLE_D = RGB(88, 40, 160): End Function
Public Function CLR_TEAL() As Long: CLR_TEAL = RGB(22, 160, 196): End Function
Public Function CLR_TEAL_D() As Long: CLR_TEAL_D = RGB(14, 120, 160): End Function
Public Function CLR_RED() As Long: CLR_RED = RGB(220, 53, 53): End Function
Public Function CLR_YELLOW() As Long: CLR_YELLOW = RGB(251, 176, 34): End Function
Public Function CLR_GREY() As Long: CLR_GREY = RGB(140, 148, 160): End Function
Public Function CLR_BG() As Long: CLR_BG = RGB(232, 238, 246): End Function
Public Function CLR_CARD() As Long: CLR_CARD = RGB(255, 255, 255): End Function
Public Function CLR_LINE() As Long: CLR_LINE = RGB(214, 222, 234): End Function
Public Function CLR_TEXT() As Long: CLR_TEXT = RGB(24, 34, 52): End Function
Public Function CLR_MUTED() As Long: CLR_MUTED = RGB(96, 108, 128): End Function
Public Function CLR_WHITE() As Long: CLR_WHITE = RGB(255, 255, 255): End Function
Public Function CLR_ROWALT() As Long: CLR_ROWALT = RGB(243, 246, 251): End Function

' Couleur d'un module (registre SYS_MODULES, code hexadecimal RRGGBB)
Public Function HexToRGB(ByVal hexColor As String) As Long
    hexColor = Replace(hexColor, "#", "")
    If Len(hexColor) <> 6 Then HexToRGB = CLR_NAVY: Exit Function
    HexToRGB = RGB(CLng("&H" & Mid$(hexColor, 1, 2)), CLng("&H" & Mid$(hexColor, 3, 2)), CLng("&H" & Mid$(hexColor, 5, 2)))
End Function

' Police d'interface
Public Function UI_FONT() As String: UI_FONT = "Segoe UI": End Function
Public Function UI_FONT_B() As String: UI_FONT_B = "Segoe UI Semibold": End Function
