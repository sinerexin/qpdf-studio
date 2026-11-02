param(
    [switch]$ShowResult
)
$ErrorActionPreference = "Stop"
$studio = Split-Path -Parent $MyInvocation.MyCommand.Path
$localRuntime = Join-Path $studio "..\..\..\.venv\Scripts\pythonw.exe"
$portableRuntime = Join-Path $studio ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $studio "launch_desktop.pyw"
$icon = Join-Path $studio "assets\qpdf-studio.ico"
$desktop = [Environment]::GetFolderPath("DesktopDirectory")

if (-not (Test-Path -LiteralPath $localRuntime)) {
    $localRuntime = $portableRuntime
}
if (-not (Test-Path -LiteralPath $localRuntime)) {
    throw "qpdf Studio Python environment is missing. See README.md."
}
if (-not (Test-Path -LiteralPath $icon)) {
    throw "qpdf Studio icon missing. Run assets/build_icon.py."
}
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "qpdf Studio launcher is missing."
}
if (-not (Test-Path -LiteralPath $desktop)) {
    throw "Windows Desktop folder was not found."
}

$shortcutFile = Join-Path $desktop "qpdf Studio.lnk"
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutFile) {
    $existing = $shell.CreateShortcut($shortcutFile)
    if ($existing.TargetPath -and
        -not ($existing.TargetPath -ieq (Resolve-Path -LiteralPath $localRuntime).Path)) {
        throw "An unrelated shortcut already exists at the requested Desktop path; refusing to overwrite it."
    }
}
$shortcut = $shell.CreateShortcut($shortcutFile)
$shortcut.TargetPath = (Resolve-Path -LiteralPath $localRuntime).Path
$shortcut.Arguments = '"' + (Resolve-Path -LiteralPath $launcher).Path + '"'
$shortcut.WorkingDirectory = (Resolve-Path -LiteralPath $studio).Path
$shortcut.IconLocation = (Resolve-Path -LiteralPath $icon).Path + ",0"
$shortcut.Description = "qpdf Studio - PDF structure, comparison and digital signatures"
$shortcut.Save()
if ($ShowResult) {
    Write-Output ("Shortcut: " + $shortcutFile)
    Write-Output ("Target: " + $shortcut.TargetPath)
    Write-Output ("Icon: " + $shortcut.IconLocation)
}
