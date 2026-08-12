# Windows 安装与自动更新

正式版使用 PyInstaller `onedir` 冻结 Python 服务，使用 Velopack 生成用户级安装包、桌面快捷方式和增量更新包。前端由本机 FastAPI 服务直接托管，用户电脑不需要单独安装 Python、Node.js、pnpm 或 .NET。

## 本机发布

```powershell
.\scripts\build-windows-release.ps1 -Version 0.1.0 -UpdateRepoUrl https://github.com/YIDASHI007/stock-video-generator-releases
```

产物位于 `build/windows-<版本>/Releases`。`StockVideoGenerator-<版本>-Setup.exe` 是给其他电脑使用的安装器；其余 Velopack 文件必须一并上传到 GitHub Release，供增量更新使用。

## 用户数据

程序文件由 Velopack 管理，数据库、视频、BGM、发布账号和日志不会放进安装目录。默认位置为：

```text
%LOCALAPPDATA%\StockVideoGeneratorData\UserData
%LOCALAPPDATA%\StockVideoGeneratorData\Logs
```

可在 `%LOCALAPPDATA%\StockVideoGeneratorData\launcher.json` 指定其他磁盘：

```json
{
  "data_dir": "E:\\StockVideoGeneratorData",
  "log_dir": "E:\\StockVideoGeneratorData\\logs",
  "port": 8877,
  "use_system_proxy": true
}
```

安装版默认把 Windows 当前用户代理同步给 Python 行情库，并让本机、东方财富和新浪行情域名保持直连。需要手工指定代理时可加入 `"proxy_url": "http://127.0.0.1:端口"`；需要完全禁用自动代理发现时设置 `"use_system_proxy": false`。可通过 `"no_proxy"` 追加逗号分隔的直连主机。

升级只替换安装目录中的程序文件，不会覆盖上述用户数据目录。

## GitHub 自动发布

推送 `v0.1.9` 形式的标签后，`.github/workflows/release.yml` 会运行测试、构建安装包，并使用 `RELEASE_REPO_TOKEN` 在公开的 `YIDASHI007/stock-video-generator-releases` 仓库创建 GitHub Release。应用启动后会读取 `resources/update.json`，检查该仓库中的新版本；用户确认后显示下载进度，下载完成后关闭本机后台服务、安装并重启。

GitHub 仓库若为私有仓库，普通安装客户端无法匿名读取 Release。内部分发可以额外实现令牌认证；不希望在客户端保存令牌时，应把二进制 Release 放到单独的公开仓库，同时保持源代码仓库私有。

当前安装包未签名，Windows SmartScreen 可能显示未知发布者。正式对外分发前应购买或配置代码签名证书，并在发布流水线中设置 Velopack 的签名参数。
