param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [string]$UpdateRepoUrl = "",

    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$env:CI = "true"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $projectRoot "build\windows-$Version"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "build"))
if (-not $OutputRoot.StartsWith($buildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputRoot must be inside $buildRoot"
}

$stageDir = Join-Path $OutputRoot "stage"
$pyDistDir = Join-Path $OutputRoot "pyinstaller-dist"
$pyWorkDir = Join-Path $OutputRoot "pyinstaller-work"
$releaseDir = Join-Path $OutputRoot "Releases"
$specDir = Join-Path $OutputRoot "spec"
$appIcon = Join-Path $projectRoot "apps\api\src\stock_video_generator\assets\launch-center.ico"

function Remove-DirectoryTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
        return
    }
    $longPath = if ($fullPath.StartsWith("\\?\")) {
        $fullPath
    } else {
        "\\?\$fullPath"
    }
    [System.IO.Directory]::Delete($longPath, $true)
}

function Convert-PnpmDeployToPortableNodeModules {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageRoot,

        [Parameter(Mandatory = $true)]
        [string]$SelfPackageName
    )

    $packageRootPath = [System.IO.Path]::GetFullPath($PackageRoot)
    $modulesPath = Join-Path $packageRootPath "node_modules"
    $indexPath = Join-Path $modulesPath ".pnpm\node_modules"
    $portablePath = Join-Path $packageRootPath "node_modules-portable"
    $legacyRoot = Join-Path $OutputRoot "pnpm-deploy-cache"
    $legacyName = $SelfPackageName.Replace("@", "").Replace("/", "-")
    $legacyPath = Join-Path $legacyRoot $legacyName
    if (-not (Test-Path -LiteralPath $indexPath -PathType Container)) {
        throw "pnpm deploy index was not found: $indexPath"
    }
    foreach ($candidate in @($modulesPath, $portablePath)) {
        $candidatePath = [System.IO.Path]::GetFullPath($candidate)
        if (-not $candidatePath.StartsWith(
            $packageRootPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Portable dependency path escaped package root: $candidatePath"
        }
    }
    $legacyPath = [System.IO.Path]::GetFullPath($legacyPath)
    if (-not $legacyPath.StartsWith(
        $OutputRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Deploy cache path escaped output root: $legacyPath"
    }

    New-Item -ItemType Directory -Path $legacyRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $portablePath -Force | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $indexPath -Force) {
        if ($entry.Name.StartsWith("@")) {
            $scopeTarget = Join-Path $portablePath $entry.Name
            New-Item -ItemType Directory -Path $scopeTarget -Force | Out-Null
            foreach ($package in Get-ChildItem -LiteralPath $entry.FullName -Force) {
                $qualifiedName = "$($entry.Name)/$($package.Name)"
                if ($qualifiedName -eq $SelfPackageName) {
                    continue
                }
                Copy-Item -LiteralPath $package.FullName `
                    -Destination $scopeTarget -Recurse -Force
            }
            continue
        }
        Copy-Item -LiteralPath $entry.FullName `
            -Destination $portablePath -Recurse -Force
    }

    Move-Item -LiteralPath $modulesPath -Destination $legacyPath
    Move-Item -LiteralPath $portablePath -Destination $modulesPath
}

if (Test-Path -LiteralPath $OutputRoot) {
    Remove-DirectoryTree -Path $OutputRoot
}
New-Item -ItemType Directory -Path $stageDir, $releaseDir, $specDir -Force | Out-Null

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment was not found: $python"
}
$vpk = Join-Path $env:LOCALAPPDATA "Programs\vpk\vpk.exe"
$localDotnet = Join-Path $env:LOCALAPPDATA "Programs\dotnet8"
if (Test-Path -LiteralPath (Join-Path $localDotnet "dotnet.exe") -PathType Leaf) {
    $env:DOTNET_ROOT = $localDotnet
    $env:PATH = "$localDotnet;$env:PATH"
}
if (-not (Test-Path -LiteralPath $vpk -PathType Leaf)) {
    $vpkCommand = Get-Command vpk.exe -ErrorAction SilentlyContinue
    if (-not $vpkCommand) {
        throw "Velopack vpk was not found. Install it with: dotnet tool install vpk"
    }
    $vpk = $vpkCommand.Source
}

Write-Host "[1/6] Building web, renderer and publisher sources..."
& pnpm --dir $projectRoot install --frozen-lockfile --prod=false
if ($LASTEXITCODE -ne 0) { throw "pnpm install failed." }
& pnpm --dir $projectRoot run build
if ($LASTEXITCODE -ne 0) { throw "pnpm build failed." }

Write-Host "[2/6] Freezing the Python desktop application..."
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name StockVideoGenerator `
    --icon $appIcon `
    --paths (Join-Path $projectRoot "apps\api\src") `
    --collect-all akshare `
    --collect-all patchright `
    --collect-all py_mini_racer `
    --collect-all velopack `
    --add-data "$(Join-Path $projectRoot 'apps\api\src\stock_video_generator\assets');stock_video_generator/assets" `
    --distpath $pyDistDir `
    --workpath $pyWorkDir `
    --specpath $specDir `
    (Join-Path $projectRoot "apps\api\src\stock_video_generator\desktop.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

Copy-Item -Path (Join-Path $pyDistDir "StockVideoGenerator\*") `
    -Destination $stageDir -Recurse -Force

Write-Host "[3/6] Creating portable renderer and publisher runtimes..."
$rendererTarget = Join-Path $stageDir "apps\renderer"
$publisherTarget = Join-Path $stageDir "apps\publisher-agent"
& pnpm --dir $projectRoot --filter "@stock-video/renderer" deploy --prod --legacy $rendererTarget
if ($LASTEXITCODE -ne 0) { throw "Renderer deployment failed." }
& pnpm --dir $projectRoot --filter "@stock-video/publisher-agent" deploy --prod --legacy $publisherTarget
if ($LASTEXITCODE -ne 0) { throw "Publisher deployment failed." }
Convert-PnpmDeployToPortableNodeModules `
    -PackageRoot $rendererTarget `
    -SelfPackageName "@stock-video/renderer"
Convert-PnpmDeployToPortableNodeModules `
    -PackageRoot $publisherTarget `
    -SelfPackageName "@stock-video/publisher-agent"

Write-Host "[4/6] Staging the production frontend and Node.js..."
$webTarget = Join-Path $stageDir "apps\web\dist"
New-Item -ItemType Directory -Path $webTarget -Force | Out-Null
Copy-Item -Path (Join-Path $projectRoot "apps\web\dist\*") `
    -Destination $webTarget -Recurse -Force
$nodeTarget = Join-Path $stageDir "runtime\node"
New-Item -ItemType Directory -Path $nodeTarget -Force | Out-Null
$node = (Get-Command node.exe -ErrorAction Stop).Source
Copy-Item -LiteralPath $node -Destination (Join-Path $nodeTarget "node.exe") -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "THIRD_PARTY.md") -Destination $stageDir -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination $stageDir -Force

$resourceDir = Join-Path $stageDir "resources"
New-Item -ItemType Directory -Path $resourceDir -Force | Out-Null
@{
    github_repo_url = $UpdateRepoUrl
    channel = "win"
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $resourceDir "update.json") -Encoding UTF8

Write-Host "[5/6] Verifying staged runtime..."
$required = @(
    (Join-Path $stageDir "StockVideoGenerator.exe"),
    (Join-Path $stageDir "apps\web\dist\index.html"),
    (Join-Path $stageDir "apps\renderer\scripts\render.mjs"),
    (Join-Path $stageDir "apps\publisher-agent\dist\index.js"),
    (Join-Path $stageDir "runtime\node\node.exe"),
    (Join-Path $stageDir "apps\renderer\node_modules\@remotion\compositor-win32-x64-msvc\ffmpeg.exe"),
    (Join-Path $stageDir "apps\renderer\node_modules\@remotion\compositor-win32-x64-msvc\ffprobe.exe"),
    (Join-Path $stageDir "_internal\py_mini_racer\mini_racer.dll")
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Staged runtime is missing: $path"
    }
}
$stagedNode = Join-Path $stageDir "runtime\node\node.exe"
Push-Location $rendererTarget
try {
    & $stagedNode -e `
        "import('@remotion/renderer').then(()=>console.log('Remotion runtime import OK')).catch((error)=>{console.error(error);process.exit(1)})"
    if ($LASTEXITCODE -ne 0) {
        throw "Staged Remotion runtime cannot resolve its production dependencies."
    }
} finally {
    Pop-Location
}

Write-Host "[6/6] Building the Velopack installer and delta package..."
& $vpk pack `
    --packId StockVideoGenerator `
    --packVersion $Version `
    --packDir $stageDir `
    --mainExe StockVideoGenerator.exe `
    --packTitle "Stock Video Generator" `
    --packAuthors "Stock Video Generator" `
    --icon $appIcon `
    --shortcuts "Desktop,StartMenuRoot" `
    --channel win `
    --outputDir $releaseDir `
    --yes
if ($LASTEXITCODE -ne 0) { throw "Velopack packaging failed." }

Write-Host "Windows release created at: $releaseDir"
Get-ChildItem -LiteralPath $releaseDir | Select-Object Name, Length, LastWriteTime
