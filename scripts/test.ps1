$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

& (Join-Path $projectRoot ".venv\Scripts\python.exe") -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& pnpm --dir $projectRoot test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& pnpm --dir $projectRoot build
exit $LASTEXITCODE
