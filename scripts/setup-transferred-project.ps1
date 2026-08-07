param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

Write-Host "== Stock Video Workflow: transferred setup ==" -ForegroundColor Cyan

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python was not found. Install Python 3.11+ and add it to PATH."
}
& $pythonCommand.Source -c "import sys; assert sys.version_info >= (3, 11), sys.version"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required."
}

$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw "Node.js was not found. Install Node.js 20+ and add it to PATH."
}
& $nodeCommand.Source -e "const m=Number(process.versions.node.split('.')[0]); if(m<20) process.exit(1)"
if ($LASTEXITCODE -ne 0) {
    throw "Node.js 20 or newer is required."
}

$pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpmCommand) {
    Write-Host "pnpm was not found. Installing pnpm 11.9.0 with npm..."
    $npmCommand = Get-Command npm.cmd -ErrorAction Stop
    & $npmCommand.Source install --global pnpm@11.9.0
    if ($LASTEXITCODE -ne 0) {
        throw "pnpm installation failed."
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$userPath;$machinePath"
    $pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
}
if (-not $pnpmCommand) {
    throw "pnpm was installed but is not visible in this terminal. Reopen PowerShell and rerun this script."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating the Python virtual environment..."
    & $pythonCommand.Source -m venv (Join-Path $projectRoot ".venv")
}

Write-Host "Relocating absolute paths in SQLite and JSON artifacts..."
& $venvPython (Join-Path $PSScriptRoot "relocate_project.py")
if ($LASTEXITCODE -ne 0) {
    throw "Project path relocation failed."
}

Write-Host "Installing Python dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed."
}

Write-Host "Installing Node.js dependencies..."
& $pnpmCommand.Source --dir $projectRoot install --frozen-lockfile
if ($LASTEXITCODE -ne 0) {
    throw "Node.js dependency installation failed."
}

$envPath = Join-Path $projectRoot ".env"
$envTemplate = Join-Path $projectRoot ".env.example"
$nodePath = $nodeCommand.Source.Replace("\", "/")
$envText = Get-Content -LiteralPath $envTemplate -Raw -Encoding utf8
$envText = [regex]::Replace(
    $envText,
    "(?m)^NODE_EXECUTABLE=.*$",
    "NODE_EXECUTABLE=$nodePath"
)
[System.IO.File]::WriteAllText(
    $envPath,
    $envText,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Building the web app, renderer and publisher agent..."
& $pnpmCommand.Source --dir $projectRoot build
if ($LASTEXITCODE -ne 0) {
    throw "Project build failed."
}

if (-not $SkipTests) {
    Write-Host "Running offline tests..."
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "Python tests failed."
    }
}

Write-Host ""
Write-Host "Setup completed." -ForegroundColor Green
Write-Host "Next command:"
Write-Host "  .\scripts\start.ps1"
Write-Host "Then open http://127.0.0.1:5173"
Write-Host "Douyin browser sessions are intentionally excluded. Sign in again in Publish Center."
Write-Host "Do not enable automatic production or formal publishing before health checks pass."
