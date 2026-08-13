param(
    [switch]$SkipDependencyRefresh,
    [switch]$Prompt
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "source-runtime.ps1")
Set-Location -LiteralPath $projectRoot

$git = Get-Command git.exe -ErrorAction Stop
$branch = (& $git.Source branch --show-current).Trim()
if (-not $branch) { throw "当前处于 detached HEAD，源码版不会自动更新。" }

$upstream = (& $git.Source rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or -not $upstream) {
    Write-Host "当前分支没有上游分支，跳过自动更新。" -ForegroundColor Yellow
    exit 0
}

$trackedChanges = & $git.Source status --porcelain
if ($trackedChanges) {
    Write-Host "检测到尚未提交的源码修改，已跳过自动拉取，避免覆盖本地工作。" -ForegroundColor Yellow
    exit 0
}

$before = (& $git.Source rev-parse HEAD).Trim()
Write-Host "检查源码增量更新：$upstream"
$remote = $upstream.Split("/", 2)[0]
& $git.Source fetch --quiet $remote $branch
if ($LASTEXITCODE -ne 0) {
    Write-Host "网络不可用或 GitHub 暂时无法连接，继续使用本地版本。" -ForegroundColor Yellow
    exit 0
}

$behind = [int](& $git.Source rev-list --count "HEAD..$upstream")
$ahead = [int](& $git.Source rev-list --count "$upstream..HEAD")
if ($ahead -gt 0 -and $behind -gt 0) {
    throw "本地分支和 $upstream 已分叉，请人工合并；自动更新没有修改任何文件。"
}
if ($behind -eq 0) {
    Write-Host "源码已经是最新版本。"
    exit 0
}

$versionFile = "apps/api/src/stock_video_generator/__init__.py"
$remoteVersionSource = & $git.Source show "$upstream`:$versionFile"
$latestVersion = "新版本"
if ($remoteVersionSource -match '__version__\s*=\s*["'']([^"'']+)["'']') {
    $latestVersion = "v$($Matches[1])"
}
$localVersionSource = Get-Content -LiteralPath (Join-Path $projectRoot $versionFile) -Raw
$currentVersion = "未知"
if ($localVersionSource -match '__version__\s*=\s*["'']([^"'']+)["'']') {
    $currentVersion = "v$($Matches[1])"
}
$releaseNotes = @(& $git.Source log --format=%s -8 "HEAD..$upstream")

if ($Prompt) {
    Add-Type -AssemblyName PresentationFramework
    $noteText = if ($releaseNotes.Count -gt 0) {
        "`n`n主要更新：`n- " + ($releaseNotes -join "`n- ")
    }
    else { "" }
    $message = @"
发现社媒工作台源码版 $latestVersion

当前版本：$currentVersion
更新大小：Git 增量源码（$behind 个提交）
更新方式：只拉取变化文件；依赖有变化时才同步依赖
$noteText

现在更新吗？选择“否”会继续启动当前版本。
"@
    $answer = [System.Windows.MessageBox]::Show(
        $message,
        "社媒工作台 · 发现新版本",
        [System.Windows.MessageBoxButton]::YesNo,
        [System.Windows.MessageBoxImage]::Information
    )
    if ($answer -ne [System.Windows.MessageBoxResult]::Yes) {
        Write-Host "用户选择暂不更新。"
        exit 2
    }
}

& $git.Source merge --ff-only $upstream
if ($LASTEXITCODE -ne 0) { throw "Git 增量更新失败。" }
$after = (& $git.Source rev-parse HEAD).Trim()

if (-not $SkipDependencyRefresh) {
    $changed = @(& $git.Source diff --name-only $before $after)
    if ($changed -contains "pyproject.toml") {
        $python = Get-SourcePython -ProjectRoot $projectRoot
        Write-Host "Python 依赖定义有变化，正在增量同步..."
        & $python -m pip install -e $projectRoot
        if ($LASTEXITCODE -ne 0) { throw "Python 依赖同步失败。" }
    }

    $nodeDependencyChanged = $false
    foreach ($path in $changed) {
        if (
            $path -eq "pnpm-lock.yaml" -or
            $path -eq "pnpm-workspace.yaml" -or
            $path -eq "package.json" -or
            $path -like "*/package.json"
        ) {
            $nodeDependencyChanged = $true
            break
        }
    }
    if ($nodeDependencyChanged) {
        $pnpm = Get-PnpmCommand
        Write-Host "Node.js 依赖定义有变化，正在增量同步..."
        & $pnpm --dir $projectRoot install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "Node.js 依赖同步失败。" }
    }
}

Write-Host "源码已增量更新：$($before.Substring(0, 7)) -> $($after.Substring(0, 7))" -ForegroundColor Green
Write-Host "未执行前端构建、PyInstaller 或安装包构建。"
exit 10
