<#
=============================================================================
 MINING TRANSIT 360 - Script de construction (Windows + Microsoft Excel)
=============================================================================
 Assemble MINING_TRANSIT_360.xlsm a partir des sources :
   1. ouvre le classeur de donnees dist\MT360_base.xlsx (genere par tools\build_workbook.py)
   2. importe les modules (.bas), classes (.cls), formulaires (.frm) et ThisWorkbook
   3. COMPILE le projet VBA (Debogage > Compiler VBAProject) et verifie le resultat
   4. execute Install_Run (icones, tableau de bord, mots de passe demo)
   5. execute la recette automatique Test_RunAll et enregistre le resultat
   6. exporte les composants (dont .frm/.frx reels) dans dist\src_export
   7. produit le dossier livrable dist\MINING_TRANSIT_360\

 Prerequis : Excel 2016/2019/2021/365 (32 ou 64 bits) ; option
   "Faire confiance a l'acces au modele d'objet du projet VBA" activee
   (ou lancer avec -EnableVBOM pour l'activer temporairement).

 Usage :
   powershell -ExecutionPolicy Bypass -File build\Build-MT360.ps1 [-EnableVBOM] [-SkipTests] [-RegenerateData] [-Visible]
=============================================================================
#>
param(
    [switch]$EnableVBOM,
    [switch]$SkipTests,
    [switch]$RegenerateData,
    [switch]$Visible
)

$ErrorActionPreference = "Stop"
$Root    = Split-Path -Parent $PSScriptRoot
$Src     = Join-Path $Root "src\vba"
$Dist    = Join-Path $Root "dist"
$Out     = Join-Path $Dist "MINING_TRANSIT_360"
$Base    = Join-Path $Dist "MT360_base.xlsx"
$Target  = Join-Path $Out "MINING_TRANSIT_360.xlsm"
$Log     = Join-Path $Dist "build_log.txt"

function Say($msg) {
    $line = "{0:HH:mm:ss}  {1}" -f (Get-Date), $msg
    Write-Host $line
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

if (Test-Path $Log) { Remove-Item $Log -Force }
Say "MINING TRANSIT 360 - construction"

# --- 0. donnees (optionnel) ------------------------------------------------
if ($RegenerateData) {
    Say "Regeneration du classeur de donnees (python tools\build_workbook.py)"
    & python (Join-Path $Root "tools\gen_assets.py")
    & python (Join-Path $Root "tools\build_workbook.py")
}
if (-not (Test-Path $Base)) { throw "Classeur de base introuvable : $Base (lancez python tools\build_workbook.py)" }

# --- 1. confiance VBOM -----------------------------------------------------
$vbomKeys = @()
if ($EnableVBOM) {
    foreach ($v in @("16.0", "15.0")) {
        $k = "HKCU:\Software\Microsoft\Office\$v\Excel\Security"
        if (Test-Path (Split-Path $k)) {
            if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
            $old = (Get-ItemProperty -Path $k -Name AccessVBOM -ErrorAction SilentlyContinue).AccessVBOM
            $vbomKeys += ,@($k, $old)
            Set-ItemProperty -Path $k -Name AccessVBOM -Value 1 -Type DWord
            Say "AccessVBOM active temporairement ($v)"
        }
    }
}

# --- 2. Excel --------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if (Test-Path $Target) { Remove-Item $Target -Force }
$xl = New-Object -ComObject Excel.Application
$xl.Visible = [bool]$Visible
$xl.DisplayAlerts = $false
$xl.AutomationSecurity = 1        # msoAutomationSecurityLow : macros du classeur construit autorisees
$exitCode = 0
try {
    $wb = $xl.Workbooks.Open($Base)
    try { $vbp = $wb.VBProject; $null = $vbp.VBComponents.Count }
    catch { throw "Acces au projet VBA refuse. Activez : Fichier > Options > Centre de gestion de la confidentialite > Parametres des macros > 'Acces approuve au modele d'objet du projet VBA', ou relancez avec -EnableVBOM." }

    # Enregistrement immediat en .xlsm (format 52) : le nom du classeur sert a Application.Run
    $wb.SaveAs($Target, 52)
    $vbp = $wb.VBProject

    function Get-Code([string]$path) {
        $txt = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
        $lines = $txt -split "`r?`n"
        $out = New-Object System.Collections.Generic.List[string]
        $inHdr = $false
        foreach ($l in $lines) {
            $t = $l.Trim()
            if ($t -like "VERSION *" -or $t -eq "BEGIN" -or $t -like "Begin {*") { $inHdr = $true; continue }
            if ($inHdr) { if ($t -eq "END" -or $t -eq "End") { $inHdr = $false }; continue }
            if ($t -like "Attribute *") { continue }
            $out.Add($l)
        }
        return ($out -join "`r`n")
    }
    function Get-FormProp([string]$path, [string]$prop, $default) {
        $m = Select-String -Path $path -Pattern ("^\s*" + $prop + "\s*=\s*(.+)$") -Encoding UTF8 | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value.Trim().Trim('"') } else { return $default }
    }

    # Modules standard (type 1) et classes (type 2)
    foreach ($spec in @(@("modules", "*.bas", 1), @("classes", "*.cls", 2))) {
        foreach ($f in Get-ChildItem (Join-Path $Src $spec[0]) -Filter $spec[1] | Sort-Object Name) {
            $c = $vbp.VBComponents.Add($spec[2])
            $c.Name = $f.BaseName
            $cm = $c.CodeModule
            if ($cm.CountOfLines -gt 0) { $cm.DeleteLines(1, $cm.CountOfLines) }
            $cm.AddFromString((Get-Code $f.FullName))
            Say ("  + {0}" -f $f.Name)
        }
    }
    # Formulaires (type 3) : la fenetre est creee vide, les controles sont construits a l'execution
    foreach ($f in Get-ChildItem (Join-Path $Src "forms") -Filter "*.frm" | Sort-Object Name) {
        $c = $vbp.VBComponents.Add(3)
        $c.Name = $f.BaseName
        # Titre et dimensions : definis par le code de chaque fenetre a l'execution (Kit_SizeForm, Me.Caption).
        # On ne les fixe pas ici : l'interface Properties du concepteur rejette certains types selon la version d'Excel.
        $cm = $c.CodeModule
        if ($cm.CountOfLines -gt 0) { $cm.DeleteLines(1, $cm.CountOfLines) }
        $cm.AddFromString((Get-Code $f.FullName))
        Say ("  + {0} (UserForm)" -f $f.Name)
    }
    # ThisWorkbook
    $doc = $vbp.VBComponents.Item($wb.CodeName)
    $cm = $doc.CodeModule
    if ($cm.CountOfLines -gt 0) { $cm.DeleteLines(1, $cm.CountOfLines) }
    $cm.AddFromString((Get-Code (Join-Path $Src "document\ThisWorkbook.cls")))
    Say "  + ThisWorkbook"
    $wb.Save()

    # --- 3. compilation ----------------------------------------------------
    Say "Compilation du projet VBA..."
    $ctl = $xl.VBE.CommandBars.FindControl(1, 578)
    if ($ctl -eq $null) { Say "ATTENTION : commande Compiler introuvable - compilation non verifiee" }
    else {
        if (-not $Visible) { $xl.Visible = $true }   # une erreur de compilation affiche une boite de dialogue
        # Surveillant : lit le texte de la boite d'erreur VBA, la ferme et le renvoie (construction sans intervention)
        $watcher = Start-Job -ScriptBlock {
            Add-Type -TypeDefinition @"
using System; using System.Text; using System.Runtime.InteropServices;
public class VbeDlg {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h, EnumProc f, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  static string found;
  static bool Child(IntPtr ch, IntPtr l) {
    StringBuilder c = new StringBuilder(256); GetClassName(ch, c, 256);
    StringBuilder t = new StringBuilder(2048); GetWindowText(ch, t, 2048);
    if (c.ToString() == "Static" && t.Length > 0) found += t.ToString() + " ";
    return true;
  }
  static bool Top(IntPtr h, IntPtr l) {
    if (!IsWindowVisible(h)) return true;
    StringBuilder c = new StringBuilder(256); GetClassName(h, c, 256);
    StringBuilder t = new StringBuilder(512); GetWindowText(h, t, 512);
    if (c.ToString() == "#32770" && t.ToString().Contains("Visual Basic")) {
      found = "";
      EnumChildWindows(h, Child, IntPtr.Zero);
      SendMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero);
      return false;
    }
    return true;
  }
  public static string Grab() { found = null; EnumWindows(Top, IntPtr.Zero); return found; }
}
"@
            for ($i = 0; $i -lt 120; $i++) {
                $r = [VbeDlg]::Grab()
                if ($r -ne $null) { return $r.Trim() }
                Start-Sleep -Milliseconds 250
            }
            return ""
        }
        Start-Sleep -Seconds 2
        $ctl.Execute()
        Start-Sleep -Milliseconds 500
        $vbeMsg = ""
        if ($watcher.State -eq "Completed") { $vbeMsg = [string](Receive-Job $watcher) }
        Stop-Job $watcher -ErrorAction SilentlyContinue; Remove-Job $watcher -Force -ErrorAction SilentlyContinue
        $ctl = $xl.VBE.CommandBars.FindControl(1, 578)
        if ($ctl.Enabled) {
            try {
                $pane = $xl.VBE.ActiveCodePane
                $sl = 0; $sc = 0; $el = 0; $ec = 0
                $pane.GetSelection([ref]$sl, [ref]$sc, [ref]$el, [ref]$ec)
                $mod = $pane.CodeModule.Parent.Name
                $txt = $pane.CodeModule.Lines($sl, 1).Trim()
                Say ("ERREUR DE COMPILATION dans {0}, ligne {1} : {2}" -f $mod, $sl, $txt)
                Say ("Message VBA : " + $vbeMsg)
                $sel = $pane.CodeModule.Lines($sl, 1)
                if ($sc -gt 0 -and $ec -gt $sc) { Say ("Element signale : " + $sel.Substring($sc - 1, [Math]::Min($ec - $sc, $sel.Length - $sc + 1))) }
            } catch { Say "Emplacement de l'erreur non determine." }
            Say "ECHEC DE COMPILATION : le classeur est conserve dans dist\MINING_TRANSIT_360 ; ouvrez-le, Alt+F11, Debogage > Compiler VBAProject pour voir le message."
            $exitCode = 2
            throw "Compilation VBA en echec"
        }
        Say "Compilation VBA : OK"
        if (-not $Visible) { $xl.Visible = $false }
    }

    # --- 4. installation ---------------------------------------------------
    $assets = Join-Path $Root "assets"
    Say "Installation (icones, tableau de bord)..."
    $xl.Run("'" + $wb.Name + "'!Install_Run", $assets)
    $wb.Save()
    Say "Installation : OK"

    # --- 5. recette ------------------------------------------------------
    if (-not $SkipTests) {
        Say "Recette automatique (Test_RunAll)..."
        $res = $xl.Run("'" + $wb.Name + "'!Test_RunAll", $true)
        Say ("Recette : " + $res)
        $ws = $wb.Worksheets.Item("TESTS")
        $used = $ws.UsedRange
        $rows = $used.Rows.Count
        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add("Recette automatique VBA - " + (Get-Date -Format "yyyy-MM-dd HH:mm") + " - " + $res)
        for ($r = 2; $r -le $rows; $r++) {
            $vals = @()
            for ($cc = 1; $cc -le 5; $cc++) { $vals += [string]$ws.Cells.Item($r, $cc).Text }
            if ($vals[0] -ne "") { $lines.Add(($vals -join " | ")) }
        }
        [System.IO.File]::WriteAllLines((Join-Path $Dist "test_results_vba.txt"), $lines, [System.Text.Encoding]::UTF8)
        if ($res -notmatch " 0 ECHEC") { $exitCode = 3 }
    }
    $wb.Worksheets.Item("ACCUEIL").Activate()
    $wb.Save()

    # --- 6. export des sources reelles (.bas/.cls/.frm/.frx) --------------
    $exp = Join-Path $Dist "src_export"
    if (Test-Path $exp) { Remove-Item $exp -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $exp | Out-Null
    foreach ($c in $vbp.VBComponents) {
        $ext = switch ($c.Type) { 1 { ".bas" } 2 { ".cls" } 3 { ".frm" } 100 { ".cls" } default { ".txt" } }
        $c.Export((Join-Path $exp ($c.Name + $ext)))
    }
    Say "Sources exportees : $exp"
    $wb.Close($true)
}
catch {
    Say ("ERREUR : " + $_.Exception.Message)
    if ($exitCode -eq 0) { $exitCode = 1 }
    try { $wb.Close($false) } catch {}
}
finally {
    $xl.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl) | Out-Null
    foreach ($kv in $vbomKeys) {
        if ($kv[1] -eq $null) { Remove-ItemProperty -Path $kv[0] -Name AccessVBOM -ErrorAction SilentlyContinue }
        else { Set-ItemProperty -Path $kv[0] -Name AccessVBOM -Value $kv[1] -Type DWord }
        Say "AccessVBOM restaure"
    }
}

# --- 7. dossier livrable ----------------------------------------------------
if ($exitCode -eq 0 -or $exitCode -eq 3) {
    Copy-Item (Join-Path $Root "assets") (Join-Path $Out "assets") -Recurse -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $Out "data") | Out-Null
    Copy-Item (Join-Path $Root "data\demo_dataset.xlsx") (Join-Path $Out "data") -Force
    Copy-Item (Join-Path $Root "docs") (Join-Path $Out "docs") -Recurse -Force
    Say "Livrable : $Out"
}
Say ("Fin (code {0})" -f $exitCode)
exit $exitCode
