# 社媒工作台源码版

源码版直接运行 Python、React/Vite、Remotion 和发布辅助器源码，不把每次功能修改重新封装成 EXE 或 Velopack 安装包。

## 运行方式

首次在一台 Windows 电脑上部署：

```powershell
git clone https://github.com/YIDASHI007/stock-video-generator.git
cd stock-video-generator
powershell -ExecutionPolicy Bypass -File .\scripts\setup-source.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install-source-shortcut.ps1
```

以后双击桌面的“社媒工作台”即可。启动脚本先检查 GitHub 上游分支的版本；发现新版时会显示当前版本、目标版本、更新提交摘要并询问是否更新。用户确认后才执行 Git 快进更新并重启：

- FastAPI 源码服务：`http://127.0.0.1:8877`
- Vite 前端源码服务：`http://127.0.0.1:5173`
- Remotion：生成视频时直接读取 TypeScript 源码
- 发布辅助器：通过本地 `tsx` 直接读取 TypeScript 源码

Python 文件修改后，Uvicorn 会自动重启；React/TypeScript 页面修改后，Vite 会热更新。

## 更新规则

`scripts/update-source.ps1` 只进行 Git 增量更新：

1. 本地存在尚未提交的源码改动时，自动跳过，绝不覆盖本地工作。
2. 正常更新使用 `git fetch` + `git merge --ff-only`。
3. 仅 `pyproject.toml` 变化时重新同步 Python 依赖。
4. 仅 pnpm 锁文件或 `package.json` 变化时重新同步 Node.js 依赖。
5. 不运行全量前端构建、PyInstaller、Velopack，也不上传或下载完整安装包。

工作台“系统 → 备份与更新”也会显示源码运行模式、当前版本、远程版本和更新状态。页面中的“联网检查更新”只读取版本状态，不会擅自修改源码；实际更新仍在下一次桌面启动时由用户确认。

## 版本发布

每个对外源码版本仍使用 `vX.Y.Z` 标签，例如 `v0.1.13`。标签推送后，GitHub Actions 只执行源码测试并发布版本说明，不再自动执行 PyInstaller、Velopack 或上传完整 Windows 安装包。其他电脑比较本机代码与 `origin/main`，并从源码中的 `__version__` 读取目标版本号。

如以后确实需要给没有 Python/Node 环境的用户提供传统安装程序，可以在 GitHub Actions 中手动运行“Build optional Windows installer”；这个兼容流程不会被普通源码版本标签自动触发。

## 数据安全

源码版优先读取 `%LOCALAPPDATA%\StockVideoGeneratorData\launcher.json` 中已有的数据和日志目录，因此切换运行方式不会迁移、清空或覆盖 SQLite、视频、账号会话和分析结果。旧安装版可以保留为恢复入口；确认源码版长期稳定后再单独卸载。

## 必须保留的首次依赖安装

源码运行不等于零依赖。每台新电脑首次部署仍需要：

- Git
- Python 3.11+
- Node.js 20+
- pnpm 11.9.0（初始化脚本可自动安装）

这是一次性的运行环境准备，不是每个版本重新编译。依赖定义没有变化时，后续更新只传输 Git 中变化的源码。

## “源码可运行”与“开源许可证”

本方案解决的是源码交付和直接运行。若要将项目在法律意义上作为开源软件对外发布，还需要项目所有者另外选择 MIT、Apache-2.0、GPL 等许可证；脚本不会擅自改变当前许可证。
