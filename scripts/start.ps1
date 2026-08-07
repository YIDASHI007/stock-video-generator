$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw ".venv was not found. Run scripts\\setup-transferred-project.ps1 first."
}

$pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpm) {
    $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
}
if (-not $pnpm) {
    throw "pnpm was not found. Run scripts\\setup-transferred-project.ps1 first."
}

$api = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "stock_video_generator.main:app", "--host", "127.0.0.1", "--port", "8877" `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru

try {
    & $pnpm.Source --dir $projectRoot dev:web
}
finally {
    if (-not $api.HasExited) {
        Stop-Process -Id $api.Id
    }
}
