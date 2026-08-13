param(
    [switch]$SkipUpdate,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "source-runtime.ps1")
Set-Location -LiteralPath $projectRoot

$python = Get-SourcePython -ProjectRoot $projectRoot
$pnpm = Get-PnpmCommand
$runtime = Set-SourceRuntimeEnvironment -ProjectRoot $projectRoot
$apiUrl = "http://127.0.0.1:$($runtime.Port)/ready"
$frontendUrl = "http://127.0.0.1:5173"

function Stop-InstalledWorkbenchOnPort {
    param([int]$Port)

    $owner = Get-ListeningProcess -Port $Port
    if (-not $owner) { return }

    $installedRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $env:LOCALAPPDATA "StockVideoGenerator")
    )
    $executable = if ($owner.ExecutablePath) {
        [System.IO.Path]::GetFullPath([string]$owner.ExecutablePath)
    }
    else { "" }
    if ($executable.StartsWith($installedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-Host "正在停止旧安装版服务，切换为源码版..."
        Stop-Process -Id $owner.ProcessId -Force -ErrorAction Stop
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-ListeningProcess -Port $Port) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 250
        }
        return
    }

    if ($owner.CommandLine -like "*stock_video_generator.main:app*") {
        return
    }
    throw "端口 $Port 正被其他程序占用：PID $($owner.ProcessId) $($owner.Name)。"
}

function Stop-SourceRuntimeProcesses {
    foreach ($port in @($runtime.Port, 5173)) {
        $owner = Get-ListeningProcess -Port $port
        if (-not $owner) { continue }
        $command = [string]$owner.CommandLine
        if (
            $command -like "*$projectRoot*" -and
            ($command -like "*uvicorn*" -or $command -like "*vite*")
        ) {
            Stop-Process -Id $owner.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
}

function Stop-InstalledLauncherProcesses {
    $installedRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $env:LOCALAPPDATA "StockVideoGenerator")
    )
    $processes = Get-CimInstance Win32_Process `
        -Filter "Name = 'StockVideoGenerator.exe'" `
        -ErrorAction SilentlyContinue
    foreach ($process in $processes) {
        if (-not $process.ExecutablePath) { continue }
        $executable = [System.IO.Path]::GetFullPath([string]$process.ExecutablePath)
        if (-not $executable.StartsWith(
            $installedRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            continue
        }
        Write-Host "正在关闭旧安装版驻留进程 PID $($process.ProcessId)..."
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if (-not $SkipUpdate) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $PSScriptRoot "update-source.ps1") `
        -Prompt
    $updateExitCode = $LASTEXITCODE
    if ($updateExitCode -eq 10) {
        Write-Host "源码更新完成，正在重启工作台..."
        Stop-SourceRuntimeProcesses
    }
    elseif ($updateExitCode -notin @(0, 2)) {
        Write-Warning "自动更新未完成，将继续启动当前本地源码。"
    }
}

Stop-InstalledWorkbenchOnPort -Port $runtime.Port
Stop-InstalledLauncherProcesses

if (-not (Test-HttpReady -Url $apiUrl)) {
    $apiOut = Join-Path $runtime.LogDir "source-api.stdout.log"
    $apiErr = Join-Path $runtime.LogDir "source-api.stderr.log"
    Start-Process -FilePath $python `
        -ArgumentList @(
            "-m", "uvicorn", "stock_video_generator.main:app",
            "--host", "127.0.0.1",
            "--port", [string]$runtime.Port,
            "--reload",
            "--reload-dir", (Join-Path $projectRoot "apps\api\src")
        ) `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $apiOut `
        -RedirectStandardError $apiErr | Out-Null
}

if (-not (Test-HttpReady -Url $frontendUrl)) {
    $frontendOwner = Get-ListeningProcess -Port 5173
    if ($frontendOwner -and $frontendOwner.CommandLine -notlike "*vite*") {
        throw "端口 5173 正被其他程序占用：PID $($frontendOwner.ProcessId) $($frontendOwner.Name)。"
    }
    $webOut = Join-Path $runtime.LogDir "source-web.stdout.log"
    $webErr = Join-Path $runtime.LogDir "source-web.stderr.log"
    Start-Process -FilePath $pnpm `
        -ArgumentList @("--dir", $projectRoot, "dev:web") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $webOut `
        -RedirectStandardError $webErr | Out-Null
}

$deadline = (Get-Date).AddSeconds(90)
do {
    $apiReady = Test-HttpReady -Url $apiUrl
    $webReady = Test-HttpReady -Url $frontendUrl
    if ($apiReady -and $webReady) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

if (-not ($apiReady -and $webReady)) {
    throw "源码版启动超时。API=$apiReady，Web=$webReady；日志目录：$($runtime.LogDir)"
}

Write-Host "社媒工作台源码版已启动：$frontendUrl" -ForegroundColor Green
Write-Host "数据目录：$($runtime.DataDir)"
Write-Host "前端修改会热更新，Python 修改会自动重启后端。"
if (-not $NoBrowser) {
    Start-Process $frontendUrl
}
