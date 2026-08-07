param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectParent = Split-Path -Parent $projectRoot
$projectName = Split-Path -Leaf $projectRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

if (-not $OutputPath) {
    $OutputPath = Join-Path $projectParent "$projectName-transfer-$timestamp.zip"
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if ($OutputPath.StartsWith(
    [System.IO.Path]::GetFullPath($projectRoot) + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "The transfer archive cannot be placed inside the project directory."
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "The output file already exists: $OutputPath"
}

$tar = Get-Command tar.exe -ErrorAction Stop
$exclude = @(
    "--exclude=$projectName/.git",
    "--exclude=$projectName/.venv",
    "--exclude=$projectName/node_modules",
    "--exclude=$projectName/*/node_modules",
    "--exclude=$projectName/*/*/node_modules",
    "--exclude=$projectName/.pytest_cache",
    "--exclude=$projectName/.ruff_cache",
    "--exclude=$projectName/logs",
    "--exclude=$projectName/.env",
    "--exclude=$projectName/data/publish-accounts",
    "--exclude=$projectName/data/qa",
    "--exclude=$projectName/data/acceptance",
    "--exclude=$projectName/**/__pycache__",
    "--exclude=$projectName/**/*.pyc",
    "--exclude=$projectName/**/dist",
    "--exclude=$projectName/**/*.tsbuildinfo",
    "--exclude=$projectName/data/database/*.db-*",
    "--exclude=$projectName/data/database/*.bak"
)

Write-Host "Creating transfer archive: $OutputPath" -ForegroundColor Cyan
Push-Location -LiteralPath $projectParent
try {
    & $tar.Source -a -c -f $OutputPath @exclude $projectName
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$hash = Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
$hashPath = "$OutputPath.sha256.txt"
$hashLine = "$($hash.Hash)  $(Split-Path -Leaf $OutputPath)"
[System.IO.File]::WriteAllText(
    $hashPath,
    $hashLine + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

$sizeGb = [math]::Round((Get-Item -LiteralPath $OutputPath).Length / 1GB, 3)
Write-Host "Transfer archive completed." -ForegroundColor Green
Write-Host "File: $OutputPath"
Write-Host "Size: $sizeGb GB"
Write-Host "SHA256: $($hash.Hash)"
Write-Host "Checksum file: $hashPath"
