param(
    [switch]$SkipNodeDependencies
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "source-runtime.ps1")
Set-Location -LiteralPath $projectRoot

Write-Host "== 社媒工作台：初始化源码版 ==" -ForegroundColor Cyan

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "未找到 Python。请安装 Python 3.11 或更高版本。"
}
& $pythonCommand.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "需要 Python 3.11 或更高版本。"
}

$nodeCommand = Get-NodeCommand
$nodeDirectory = Split-Path -Parent $nodeCommand
if (($env:Path -split ";") -notcontains $nodeDirectory) {
    $env:Path = "$nodeDirectory;$env:Path"
}
& $nodeCommand -e "process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)"
if ($LASTEXITCODE -ne 0) {
    throw "需要 Node.js 20 或更高版本。"
}

$pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpmCommand) {
    $npmCommand = Get-Command npm.cmd -ErrorAction Stop
    Write-Host "首次安装 pnpm 11.9.0..."
    & $npmCommand.Source install --global pnpm@11.9.0
    if ($LASTEXITCODE -ne 0) { throw "pnpm 安装失败。" }
    $pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
}
if (-not $pnpmCommand) {
    throw "pnpm 已安装但当前终端尚未识别，请重新打开 PowerShell 后再运行。"
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host "创建 Python 虚拟环境..."
    & $pythonCommand.Source -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Python 虚拟环境创建失败。" }
}

Write-Host "安装 Python 运行依赖（可编辑源码模式）..."
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "Python 依赖安装失败。" }

if (-not $SkipNodeDependencies) {
    Write-Host "安装前端、渲染器与发布器依赖..."
    & $pnpmCommand.Source --dir $projectRoot install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "Node.js 依赖安装失败。" }
}

Write-Host ""
Write-Host "源码版初始化完成；没有生成 EXE，也没有构建安装包。" -ForegroundColor Green
Write-Host "启动命令：.\scripts\start-source.ps1"
Write-Host "安装桌面入口：.\scripts\install-source-shortcut.ps1"
