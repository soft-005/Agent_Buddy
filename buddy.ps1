<#
.SYNOPSIS
    One-click control for Buddy: LM Studio's server, the backend, and the desktop app.

.DESCRIPTION
    start         Start whatever isn't running (LM Studio server if needed, backend,
                  desktop app). If Buddy is already running, it just shows it.
    stop          Quit the desktop app and stop the backend. LM Studio keeps running.
    stop-all      Same as stop, and also unload LM Studio's models and stop its
                  server, which frees several GB of memory.
    toggle        Show or hide Buddy (starts it if it isn't running).
    show, hide    Show or hide Buddy.
    restart       stop, then start. Use it after changing code or backend/.env.
    status        Print what is running.
    model [name]  List the language models, or switch to one ("model gemma"). Switches
                  the running Buddy right away and saves the choice in backend/.env.
    stop-backend  Stop only the backend this script started (Buddy's tray Quit uses this).
    restart-backend  Restart only the backend, e.g. after its PORT changed (the desktop
                  app's Connection card uses this, passing -OldPort).

    The backend runs in the background with no window. Its log is logs\backend.log.
    Add -Popup to show problems in a message box (the desktop shortcuts do this).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File buddy.ps1 start
    powershell -ExecutionPolicy Bypass -File buddy.ps1 status
    powershell -ExecutionPolicy Bypass -File buddy.ps1 model gemma
#>
param(
    # Explicit positions: once one parameter has one, the others are no longer positional.
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "stop-all", "toggle", "show", "hide", "restart", "status", "stop-backend", "restart-backend", "model")]
    [string]$Action = "start",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Name,
    [int]$OldPort = 0,
    [switch]$Popup
)

$ErrorActionPreference = "Stop"
$Root       = $PSScriptRoot
$Backend    = Join-Path $Root "backend"
$Desktop    = Join-Path $Root "desktop"
$Logs       = Join-Path $Root "logs"
$Python     = Join-Path $Root "venv\Scripts\python.exe"
$Electron   = Join-Path $Desktop "node_modules\electron\dist\electron.exe"
$PidFile    = Join-Path $Logs "backend.pid"
$BackendLog = Join-Path $Logs "backend.log"
$Lms        = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"
$LmsPort    = 1234

# ---------- helpers ----------

function Get-Setting([string]$Name, [string]$Default) {
    # Read a value from backend\.env (lines like NAME=value; comments ignored).
    $envFile = Join-Path $Backend ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match "^\s*$Name\s*=\s*(.*?)\s*$" -and $Matches[1]) { return $Matches[1].Trim('"', "'") }
        }
    }
    return $Default
}

$Port     = [int](Get-Setting "PORT" "8818")
$Provider = Get-Setting "LLM_PROVIDER" "lmstudio"

function Say([string]$Text) { Write-Host $Text }

function Warn([string]$Text) {
    Write-Host $Text -ForegroundColor Yellow
    if ($Popup) {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show($Text, "Buddy", "OK", "Warning")
    }
}

function Fail([string]$Text) {
    Warn $Text
    exit 1
}

# Any unexpected error: report it (shortcuts run hidden, so a message box is the only sign).
trap {
    Fail "Buddy's launcher hit an error:`n`n$($_.Exception.Message)`n`nLine $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim())"
}

function Invoke-Quiet([string]$Exe, [string[]]$Arguments, [int]$TimeoutSeconds = 60) {
    # Run a console program with no window, wait for it (not for programs it starts),
    # and return its exit code ($null on timeout). Not "& exe 2>&1": Windows PowerShell
    # turns a program's stderr text into errors, which would abort this script, and a
    # redirected pipe can make it wait for every process that inherited it.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = ($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join " "
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) { return $null }
    return $proc.ExitCode
}

function Test-Listening([int]$P) {
    [bool](Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue)
}

function Get-BackendProcess {
    # The backend this script started, if it is still running (checked by command line,
    # so a reused process id is never mistaken for it).
    if (-not (Test-Path $PidFile)) { return $null }
    $id = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not ($id -match '^\d+$')) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -like "*app.main*") { return $proc }
    return $null
}

function Get-DesktopProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='electron.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -ieq $Electron) })
}

function Test-DesktopRunning { (Get-DesktopProcesses).Count -gt 0 }

# ---------- parts ----------

function Start-LMStudio {
    if ($Provider -ne "lmstudio" -or (Test-Listening $LmsPort)) { return }
    if (-not (Test-Path $Lms)) {
        Say "LM Studio's server isn't running and the lms tool wasn't found. Start the server in LM Studio."
        return
    }
    Say "Starting LM Studio's server..."
    $code = Invoke-Quiet $Lms @("server", "start", "--port", "$LmsPort") 90
    if ($code -ne 0) { Say "lms server start returned $code; checking the port anyway." }
}

function Wait-LMStudio {
    # Only the server has to be up here. The backend loads the model itself (warm-up),
    # and the desktop app's loader shows the progress.
    if ($Provider -ne "lmstudio") { return }
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Listening $LmsPort) { Say "LM Studio server ready on port $LmsPort."; return }
        Start-Sleep -Milliseconds 500
    }
    Warn "Buddy started, but LM Studio's server isn't running on port $LmsPort, so Buddy can't reply yet.`n`nOpen LM Studio and start its server (Developer tab), then try again."
}

function Start-Backend {
    # Returns $true if a new backend was started.
    if (Test-Listening $Port) { Say "Backend already running on port $Port."; return $false }
    if (-not (Test-Path $Python)) { Fail "Python environment not found at $Python. Create it first (see CLAUDE.md, Running it)." }
    New-Item -ItemType Directory -Force -Path $Logs | Out-Null
    $env:PYTHONUNBUFFERED = "1"
    $proc = Start-Process -FilePath $Python -ArgumentList "-m", "app.main" -WorkingDirectory $Backend `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "backend.out.log") -RedirectStandardError $BackendLog
    Set-Content -Path $PidFile -Value $proc.Id
    Say "Backend starting (log: logs\backend.log)."
    return $true
}

function Wait-Backend {
    # Loading the speech model takes a few seconds. Report a crash instead of failing silently.
    for ($i = 0; $i -lt 180; $i++) {
        if (Test-Listening $Port) { Say "Backend ready on port $Port."; return }
        if (-not (Get-BackendProcess)) {
            $tail = ""
            if (Test-Path $BackendLog) { $tail = (Get-Content $BackendLog -Tail 12) -join "`n" }
            Fail "Buddy's backend stopped while starting.`n`n$tail`n`nFull log: $BackendLog"
        }
        Start-Sleep -Milliseconds 500
    }
    Fail "Buddy's backend didn't start within 90 seconds. See $BackendLog"
}

function Stop-Backend([switch]$AlsoManual) {
    $proc = Get-BackendProcess
    if ($proc) {
        [void](Invoke-Quiet "taskkill.exe" @("/PID", "$($proc.ProcessId)", "/T", "/F") 30)   # /T: also its child python process
        Say "Backend stopped."
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    if ($AlsoManual) {
        Stop-BackendOnPort $Port
        if ($OldPort -and $OldPort -ne $Port) { Stop-BackendOnPort $OldPort }
    }
}

function Stop-BackendOnPort([int]$P) {
    # A backend started by hand (python -m app.main) listening on port $P.
    foreach ($conn in @(Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue)) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
        if ($p -and $p.CommandLine -like "*app.main*") {
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            if ($parent -and $parent.CommandLine -like "*app.main*") { Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue }
            Say "Backend stopped."
        }
    }
}

function Send-Desktop([string]$Command) {
    # Starts the desktop app, or passes the command to the one already running.
    if (-not (Test-Path $Electron)) { Fail "Desktop app not installed. Run 'npm install' in the desktop folder." }
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $argList = @(".")
    if ($Command) { $argList += $Command }
    Start-Process -FilePath $Electron -ArgumentList $argList -WorkingDirectory $Desktop | Out-Null
}

function Stop-Desktop {
    if (-not (Test-DesktopRunning)) { return }
    Send-Desktop "quit"
    for ($i = 0; $i -lt 24 -and (Test-DesktopRunning); $i++) { Start-Sleep -Milliseconds 250 }
    Get-DesktopProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Say "Desktop app closed."
}

function Start-All {
    # Window first, so its startup loader appears right away; the slow parts follow.
    $newBackend = Start-Backend
    if (Test-DesktopRunning) { Send-Desktop "show" } else { Send-Desktop "" }
    Start-LMStudio
    if ($newBackend) { Wait-Backend }
    Wait-LMStudio
}

function Stop-All {
    Stop-Desktop
    Stop-Backend -AlsoManual
}

# ---------- actions ----------

switch ($Action) {
    "start"   { Start-All }
    "stop"    { Stop-All }
    "stop-all" {
        Stop-All
        if (Test-Path $Lms) {
            [void](Invoke-Quiet $Lms @("unload", "--all") 60)
            [void](Invoke-Quiet $Lms @("server", "stop") 60)
            Say "LM Studio models unloaded and server stopped."
        }
    }
    "toggle"  { if (Test-DesktopRunning) { Send-Desktop "toggle" } else { Start-All } }
    "show"    { if (Test-DesktopRunning) { Send-Desktop "show" } else { Start-All } }
    "hide"    { if (Test-DesktopRunning) { Send-Desktop "hide" } }
    "restart" { Stop-All; Start-Sleep -Seconds 1; Start-All }
    "stop-backend" { Stop-Backend }
    "restart-backend" {
        Stop-Backend -AlsoManual
        Start-Sleep -Seconds 1
        if (Start-Backend) { Wait-Backend }
    }
    "model" {
        # Lists or switches models (backend\scripts\model.py does the work).
        if (-not (Test-Path $Python)) { Fail "Python environment not found at $Python. Create it first (see README.md)." }
        $script = Join-Path $Backend "scripts\model.py"
        Push-Location $Backend
        try { & $Python $script @Name } finally { Pop-Location }
        exit $LASTEXITCODE
    }
    "status" {
        $lmsState = if (Test-Listening $LmsPort) { "running on port $LmsPort" } else { "not running" }
        $backendState = if (Test-Listening $Port) { "running on port $Port" } else { "not running" }
        if (Get-BackendProcess) { $backendState += " (started by this script)" }
        $desktopState = if (Test-DesktopRunning) { "running" } else { "not running" }
        Say "LM Studio server : $lmsState"
        Say "Backend          : $backendState"
        Say "Desktop app      : $desktopState"
    }
}
