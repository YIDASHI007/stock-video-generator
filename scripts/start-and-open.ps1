$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendUrl = "http://127.0.0.1:5173"
$apiReadyUrl = "http://127.0.0.1:8877/openapi.json"
$logDir = Join-Path $projectRoot "logs"
$launcherLog = Join-Path $logDir "desktop-launcher.log"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Write-LauncherLog {
    param([string]$Message)

    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $launcherLog -Value $line -Encoding UTF8
}

function Test-ApiReady {
    try {
        $response = Invoke-WebRequest `
            -Uri $apiReadyUrl `
            -UseBasicParsing `
            -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-FrontendReady {
    try {
        $response = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-PortListening {
    param([int]$Port)

    return [bool](
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    )
}

function Get-PortOwner {
    param([int]$Port)

    $connection = Get-NetTCPConnection `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) {
        return $null
    }

    return Get-CimInstance Win32_Process `
        -Filter "ProcessId = $($connection.OwningProcess)" `
        -ErrorAction SilentlyContinue
}

function Stop-OwnedPortProcess {
    param(
        [int]$Port,
        [string]$ExpectedCommandPattern,
        [string]$ServiceName
    )

    $owner = Get-PortOwner -Port $Port
    if (-not $owner) {
        return
    }

    if ($owner.CommandLine -notlike "*$ExpectedCommandPattern*") {
        throw "$ServiceName cannot start because port $Port is used by PID $($owner.ProcessId) ($($owner.Name))."
    }

    Write-LauncherLog "$ServiceName is unresponsive. Restarting owned PID $($owner.ProcessId)."
    Stop-Process -Id $owner.ProcessId -Force -ErrorAction Stop

    $deadline = (Get-Date).AddSeconds(10)
    while ((Test-PortListening -Port $Port) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if (Test-PortListening -Port $Port) {
        throw "$ServiceName did not release port $Port after it was stopped."
    }
}

function Show-LauncherError {
    param([string]$Message)

    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $Message,
            "Stock Video Generator",
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Error
        ) | Out-Null
    }
    catch {
        # The launcher log remains available if Windows cannot show a dialog.
    }
}

function Start-Api {
    $python = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python virtual environment was not found. Run the installation script first."
    }

    Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "stock_video_generator.main:app", "--host", "127.0.0.1", "--port", "8877" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "desktop-api.stdout.log") `
        -RedirectStandardError (Join-Path $logDir "desktop-api.stderr.log") | Out-Null
}

function Start-Frontend {
    $pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if (-not $pnpm) {
        $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    }
    if (-not $pnpm) {
        throw "pnpm was not found. Run the installation script first."
    }

    Start-Process -FilePath $pnpm.Source `
        -ArgumentList "--dir", $projectRoot, "dev:web" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "desktop-web.stdout.log") `
        -RedirectStandardError (Join-Path $logDir "desktop-web.stderr.log") | Out-Null
}

try {
    Write-LauncherLog "Launcher started."

    $apiReady = Test-ApiReady
    $frontendReady = Test-FrontendReady
    $apiPortBusy = Test-PortListening -Port 8877
    $frontendPortBusy = Test-PortListening -Port 5173

    if (-not $apiReady) {
        if ($apiPortBusy) {
            Stop-OwnedPortProcess `
                -Port 8877 `
                -ExpectedCommandPattern "stock_video_generator.main:app" `
                -ServiceName "API"
        }
        Write-LauncherLog "Starting the API."
        Start-Api
    }
    if (-not $frontendReady) {
        if ($frontendPortBusy) {
            Stop-OwnedPortProcess `
                -Port 5173 `
                -ExpectedCommandPattern "stock-video-generator" `
                -ServiceName "Frontend"
        }
        Write-LauncherLog "Starting the frontend."
        Start-Frontend
    }

    $deadline = (Get-Date).AddSeconds(90)
    do {
        $apiReady = Test-ApiReady
        $frontendReady = Test-FrontendReady
        if ($apiReady -and $frontendReady) {
            Write-LauncherLog "API and frontend are ready. Opening the browser."
            Start-Process $frontendUrl
            exit 0
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    $state = "API ready: {0}; frontend ready: {1}" -f $apiReady, $frontendReady
    throw "Timed out while waiting for the application. $state. See $launcherLog"
}
catch {
    $message = $_.Exception.Message
    Write-LauncherLog "Launch failed: $message"
    Show-LauncherError -Message $message
    exit 1
}
