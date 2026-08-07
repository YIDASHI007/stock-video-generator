# 股票视频工作流迁移指南

本迁移包用于把当前 Windows 本地工作流搬到另一台 Windows 10/11 电脑。

## 包内保留的内容

- 完整源代码、锁定文件和测试；
- SQLite 数据库；
- 已完成的回测、视频、横竖封面和验证报告；
- 发布清单及历史执行证据；
- 股票池、自动生产策略、背景音乐和行情缓存。

以下内容故意不打包：

- `.venv`、所有 `node_modules` 和构建缓存：跨电脑不可直接复用，安装脚本会重建；
- `.env`：其中包含当前电脑的 Node.js 绝对路径；
- `data/publish-accounts`：Chrome 登录档案包含敏感会话且受机器环境影响；
- `logs`、QA 临时产物和测试缓存。

因此，新电脑会保留历史成片和发布记录，但抖音账号必须重新扫码登录。

## 新电脑要求

- Windows 10/11；
- Python 3.11 或更高版本，并加入 `PATH`；
- Node.js 20 或更高版本，并加入 `PATH`；
- Google Chrome；
- 可访问 Python、npm、行情源、微软在线语音和抖音创作者中心的网络；
- 建议至少保留 15 GB 磁盘空间用于安装依赖、渲染缓存和后续成片。

## 恢复步骤

1. 把 ZIP 和对应的 `.sha256.txt` 复制到新电脑。
2. 校验压缩包：

   ```powershell
   Get-FileHash .\股票回测视频生成器-transfer-*.zip -Algorithm SHA256
   ```

   输出应与 `.sha256.txt` 中的值一致。

3. 解压到固定、不会被同步软件自动搬动的位置，例如：

   ```text
   D:\stock-video-generator
   ```

   ZIP 内含一层项目目录；可保留原名称，也可改名。

4. 在项目根目录打开 PowerShell：

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\scripts\setup-transferred-project.ps1
   ```

   安装脚本会：

   - 重建 `.venv`；
   - 自动重写数据库及 JSON 里的旧电脑绝对路径；
   - 安装 Python、pnpm 和 Node.js 依赖；
   - 生成新电脑专用 `.env`；
   - 构建前端、Remotion 渲染器和发布 Agent；
   - 运行离线测试。

5. 启动：

   ```powershell
   .\scripts\start.ps1
   ```

6. 打开：

   - 前端：<http://127.0.0.1:5173>
   - API 健康检查：<http://127.0.0.1:8877/health>

## 首次启动后的检查顺序

1. 设置页确认 SQLite、Node、Remotion、FFmpeg、磁盘和 TTS 均为正常。
2. 首页确认历史视频可以播放、横竖封面可以打开。
3. 先生成一条短测试视频，确认真实行情、渲染和封面输出正常。
4. 发布中心重新打开抖音账号扫码登录。
5. 先用一条视频执行“预检并填写”，不要立即正式发布。
6. 确认标题、话题、横封面和竖封面都正确后，再授权正式发布。
7. 最后再到设置页开启自动生产。

迁移后的自动生产默认保持关闭，避免新电脑刚启动就立即抓取、渲染或投稿。

## 两台电脑同时运行的边界

- 可以在旧电脑继续浏览文件，但不要让两台电脑同时使用同一个抖音账号自动发布；
- 不要同时开启两套自动生产，否则会产生重复选题和重复视频；
- 确认新电脑稳定后，再把它设为唯一生产与发布主机；
- 浏览器登录档案不会同步，两台电脑上的扫码登录彼此独立。

## 常见问题

### PowerShell 不允许运行脚本

只对当前窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### 找不到 Python 或 Node.js

安装后关闭全部 PowerShell 窗口，再重新打开。确认：

```powershell
python --version
node --version
```

### pnpm 安装失败

可手动执行：

```powershell
npm install --global pnpm@11.9.0
pnpm install --frozen-lockfile
```

### 历史视频路径仍指向旧电脑

在项目根目录重新运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\relocate_project.py
```

脚本会在修改数据库前生成 `stock_video.before-relocation.bak`。

### 端口被占用

确认没有其他程序占用 `5173` 和 `8877`，或结束旧的开发服务后重试。

