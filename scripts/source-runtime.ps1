$ErrorActionPreference = "Stop"

function Get-SourceProjectRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

function Get-SourcePython {
    param([string]$ProjectRoot)

    $python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "尚未初始化源码运行环境。请先运行 scripts\setup-source.ps1。"
    }
    return $python
}

function Get-PnpmCommand {
    $pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if (-not $pnpm) {
        $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    }
    if (-not $pnpm) {
        throw "未找到 pnpm。请先运行 scripts\setup-source.ps1。"
    }
    return $pnpm.Source
}

function Get-NodeCommand {
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($node) {
        return $node.Source
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "StockVideoGenerator\current\runtime\node\node.exe"),
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "未找到 Node.js 20+。请先安装 Node.js，或保留现有工作台的内置 Node。"
}

function Read-LauncherSettings {
    $configRoot = Join-Path $env:LOCALAPPDATA "StockVideoGeneratorData"
    $configPath = Join-Path $configRoot "launcher.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return @{}
    }

    try {
        $raw = [System.IO.File]::ReadAllText(
            $configPath,
            [System.Text.UTF8Encoding]::new($false)
        )
        $value = $raw | ConvertFrom-Json
        $result = @{}
        if ($value.data_dir) { $result.data_dir = [string]$value.data_dir }
        if ($value.log_dir) { $result.log_dir = [string]$value.log_dir }
        if ($value.port) { $result.port = [int]$value.port }
        return $result
    }
    catch {
        throw "无法读取现有用户配置：$configPath。$($_.Exception.Message)"
    }
}

function Set-SourceRuntimeEnvironment {
    param([string]$ProjectRoot)

    $launcher = Read-LauncherSettings
    $configRoot = Join-Path $env:LOCALAPPDATA "StockVideoGeneratorData"
    $dataDir = if ($env:APP_DATA_DIR) {
        $env:APP_DATA_DIR
    }
    elseif ($launcher.data_dir) {
        $launcher.data_dir
    }
    else {
        Join-Path $configRoot "UserData"
    }
    $logDir = if ($env:APP_LOG_DIR) {
        $env:APP_LOG_DIR
    }
    elseif ($launcher.log_dir) {
        $launcher.log_dir
    }
    else {
        Join-Path $configRoot "Logs"
    }
    $port = if ($env:APP_PORT) {
        [int]$env:APP_PORT
    }
    elseif ($launcher.port) {
        [int]$launcher.port
    }
    else {
        8877
    }

    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    $node = Get-NodeCommand
    $nodeDirectory = Split-Path -Parent $node
    if (($env:Path -split ";") -notcontains $nodeDirectory) {
        $env:Path = "$nodeDirectory;$env:Path"
    }
    $env:APP_ENV = "development"
    $env:APP_HOST = "127.0.0.1"
    $env:APP_PORT = [string]$port
    $env:APP_RUNTIME_DIR = $ProjectRoot
    $env:APP_DATA_DIR = [System.IO.Path]::GetFullPath($dataDir)
    $env:APP_LOG_DIR = [System.IO.Path]::GetFullPath($logDir)
    $env:APP_CORS_ORIGINS = "http://127.0.0.1:5173,http://localhost:5173"
    $env:NODE_EXECUTABLE = $node
    $env:VITE_API_BASE_URL = "http://127.0.0.1:$port"

    return [pscustomobject]@{
        Port = $port
        DataDir = $env:APP_DATA_DIR
        LogDir = $env:APP_LOG_DIR
    }
}

function Get-ListeningProcess {
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

function Test-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 2
    )

    try {
        $response = Invoke-WebRequest `
            -Uri $Url `
            -UseBasicParsing `
            -TimeoutSec $TimeoutSeconds
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}
