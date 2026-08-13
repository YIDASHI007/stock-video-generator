$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "社媒工作台.lnk"
$backupPath = Join-Path $desktop "社媒工作台（安装版备份）.lnk"
$launcher = Join-Path $PSScriptRoot "start-source.vbs"
$icon = Join-Path $projectRoot "apps\api\src\stock_video_generator\assets\launch-center.ico"

if ((Test-Path -LiteralPath $shortcutPath) -and -not (Test-Path -LiteralPath $backupPath)) {
    Copy-Item -LiteralPath $shortcutPath -Destination $backupPath
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Join-Path $env:SystemRoot "System32\wscript.exe")
$shortcut.Arguments = "//nologo `"$launcher`""
$shortcut.WorkingDirectory = $projectRoot
if (Test-Path -LiteralPath $icon -PathType Leaf) {
    $shortcut.IconLocation = "$icon,0"
}
$shortcut.Description = "社媒工作台源码版（Git 增量更新）"
$shortcut.Save()

Write-Host "桌面入口已切换为源码版：$shortcutPath" -ForegroundColor Green
if (Test-Path -LiteralPath $backupPath) {
    Write-Host "原安装版入口已备份：$backupPath"
}
