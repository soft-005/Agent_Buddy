<#
.SYNOPSIS
    Put Buddy shortcuts on the Desktop (and optionally in the Start menu).

.DESCRIPTION
    Creates three shortcuts that run buddy.ps1 with no console window:
      Buddy                 start Buddy, or show it if it's already running
      Buddy - Show or Hide  show or hide Buddy
      Buddy - Quit          quit Buddy and stop its backend
    Run it again after moving the project folder. Delete the shortcuts to remove them.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install-shortcuts.ps1
    powershell -ExecutionPolicy Bypass -File install-shortcuts.ps1 -StartMenu
#>
param([switch]$StartMenu)

$ErrorActionPreference = "Stop"
$Root     = $PSScriptRoot
$Launcher = Join-Path $Root "buddy.ps1"
$Icon     = Join-Path $Root "desktop\assets\icon.ico"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

$folders = @([Environment]::GetFolderPath("Desktop"))
if ($StartMenu) { $folders += Join-Path ([Environment]::GetFolderPath("Programs")) "Buddy" }

$shortcuts = @(
    @{ Name = "Buddy";                Action = "start";  Description = "Start Buddy, or show it if it's running" },
    @{ Name = "Buddy - Show or Hide"; Action = "toggle"; Description = "Show or hide Buddy" },
    @{ Name = "Buddy - Quit";         Action = "stop";   Description = "Quit Buddy and stop its backend" }
)

$shell = New-Object -ComObject WScript.Shell
foreach ($folder in $folders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    foreach ($s in $shortcuts) {
        $path = Join-Path $folder "$($s.Name).lnk"
        $lnk = $shell.CreateShortcut($path)
        $lnk.TargetPath = $PowerShellExe
        $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`" $($s.Action) -Popup"
        $lnk.WorkingDirectory = $Root
        $lnk.IconLocation = "$Icon,0"
        $lnk.WindowStyle = 7   # start minimized, so no console window flashes up
        $lnk.Description = $s.Description
        $lnk.Save()
        Write-Host "Created $path"
    }
}
