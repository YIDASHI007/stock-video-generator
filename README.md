# 股票历史回测视频生成器

Stock Historical Simulation Video Generator 是一个可独立安装和运行的 Windows 本地应用。它搜索 A 股、港股和美股，抓取真实日线与公司行为，确定性地模拟一次性买入并持有，再由 Remotion 逐帧渲染 16:9 H.264 MP4。生产流程没有随机行情、硬编码价格或成功兜底；数据源不可用时会保留任务并显示真实错误。

> 历史数据模拟，仅供信息展示，不构成投资建议。

## 已实现

- React 19 + TypeScript + Vite 中文前端；
- FastAPI + Pydantic + SQLAlchemy + SQLite 后端；
- 统一 `MarketDataProvider`，A 股使用 AKShare；港股/美股以 yfinance 为主源，并以 AKShare/Sina 未复权日线和公司行为因子作为真实备用源；
- 未复权 OHLCV、分红、拆合股标准化与带 TTL 的文件缓存；
- 非交易日策略、碎股/整数股/市场交易单位、手续费、现金分红和红利复投；
- 每日股数、现金、资产、收益率、回撤及关键节点；
- `simulation.json`、`simulation.csv`、`market_data.json`、`visualization_spec.json`；
- SQLite 持久化状态机、优先级队列、并发限制、取消、超时、自动/手动重试和重启恢复；
- SSE 任务事件接口及前端 2 秒轮询；
- Remotion `useCurrentFrame` 驱动的折线、发光圆点、日期、金额和收益率；
- H.264、yuv420p、BT.709 输出及 Remotion 内置 FFprobe 媒体校验；
- 可选 TTS Provider 边界；未配置时明确生成无配音视频；
- 自动选题：股票池文件 + 确定性戏剧性评分（暴涨神话/暴跌教训/过山车/长跑赢家）；
- 全链路自动生产总控：选题 → 回测 → 脚本 → 配音 → 渲染无人值守流水线，含每日配额、同股 90 天冷却与搁浅机制。
- 独立抖音发布中心：持久化账号会话、内容清单、动态标题、简介、话题、合集、4:3 横封面、3:4 竖封面、审批闸门和执行证据；
- Patchright 确定性发布器 + Stagehand 局部自愈兜底；验证码、登录和最终发布授权不会交给 Agent。

## V1 固定成片结构

当前生产模板固定为 `v1`，Remotion 合成入口为
`StockHistoricalSimulationV1`。网页预览、命令行渲染、后台任务和自动生产
均使用同一个入口；历史入口会兼容映射到 V1。

- 1920 × 1080 横屏、30 FPS；
- 开场先用“投入本金持有某股票，最终赚了还是亏了”的问题式钩子明确视频主题；
- 顶部信息带依次展示买入日期、本金、持有股票、当前资产和当前累计收益，长名称会自适应分行；
- 主体为随时间滚动的资产走势图，盈利区间使用绿色实心填充，亏损区间使用红色实心填充；
- 当前光点同步展示日期和资产余额，时间轴只显示已经运行到的历史；
- 自动生成的 1440 × 1080 横封面和 1080 × 1440 竖封面沿用开场钩子风格，展示买入日期、代码、本金、股票名、持有年限及盈亏悬念；
- 每条视频最多展示 3–5 个历史事件，卡片锚定事件日期并随时间轴移动、淡出；
- 结尾不显示事件卡片或标志线，背景拉远为完整历史走势；
- 结尾中央放大展示本金、累计收益率和累计收益，底部仅保留股票名称与回测日期。

## 自动生产（选题 + 流水线总控）

打开设置页「自动生产策略」或驾驶舱「自动生产」开关后，后台循环会按策略无人值守出片：

1. **股票主库同步与选题补充**：每天从 AKShare A股名单、港交所证券名单及 NASDAQ Trader Symbol Directory 增量同步真实上市证券，持久化到 SQLite；自动排除 ST、退市整理、基金、权证、SPAC 和低流动性港股。补池时按市场均衡抽取有资格的候选，拉取近 10 年真实日线和拆合股事件（走磁盘缓存），使用拆股调整后的连续价格计算区间最大涨幅、最大回撤、年化波动率和全程年化收益，映射为四类戏剧性角度并确定性打分；同股 90 天冷却期内不会再次入队；
2. **生产**：取队首选题，依次委托现有任务机执行 SIMULATION（回测+脚本+配音）与 RENDER（渲染出片），断点产物复用由任务机保证；
3. **配额**：每日配额按「今天创建且未最终失败（搁浅/跳过）」的 run 计数；
4. **搁浅**：任一阶段失败自动重跑整条链，连续失败 3 次后进入 PARKED 搁浅，让出队列等待人工在任务中心「重试/跳过」。

流水线状态机：

```text
TOPIC_QUEUED → SIMULATING → SCRIPTING → VOICING → RENDERING → COMPLETED
     失败 → FAILED（retry_count < 3 自动重试）→ PARKED（搁浅，人工 retry/skip）
```

`data/universe.json` 现在是人工精选和中文名称覆盖名单，不再限制自动选题范围。首次启动会先将其作为保底种子导入数据库，随后由真实市场名单自动扩充；任何数据源失败都会如实记录，绝不会用模拟股票补位。格式：

```json
[
  {"symbol": "600519.SH", "name": "贵州茅台", "market": "CN"},
  {"symbol": "0700.HK", "name": "腾讯控股", "market": "HK"},
  {"symbol": "NVDA", "name": "英伟达", "market": "US", "angle_hint": "surge"}
]
```

- `market`：`CN` / `HK` / `US`；
- `angle_hint` 可选，取值 `surge`（暴涨神话）/ `crash`（暴跌教训）/ `rollercoaster`（过山车）/ `compound`（长跑赢家），用于强制选题角度；
- 买入日规则：所有题材统一从回测截止日往前 10 年的同一天买入；当天不是交易日则顺延到下一交易日，上市不足 10 年则从上市后的首个交易日开始。题材分类只影响叙事，不再改变投资起点；
- 本金币种随市场：CN→CNY、HK→HKD、US→USD（与回测的币种校验一致）。

策略持久化在 `data/pipeline_policy.json`（原子写入），字段：`enabled`、`daily_quota`、`amount`、`markets`、`angle_weights`、`voice`、`pool_target`。

## 项目结构

```text
apps/
  api/                 FastAPI、回测、Provider、任务系统
  web/                 React 中文界面
  renderer/            Remotion 渲染入口
packages/
  schemas/             TypeScript 类型及 JSON Schema
  video-template/      确定性股票曲线视频组件
data/
  universe.json        人工精选/中文名称覆盖种子（动态主库的保底与置顶来源）
  pipeline_policy.json 自动生产策略（由设置页写入）
  database/            SQLite
  market-cache/        标准化行情缓存和来源摘要
  simulations/         请求、行情、校验、回测和可视化产物
  renders/             渲染任务目录
  outputs/             MP4、4:3 横版封面、3:4 竖版封面和媒体验证报告
  publishes/           发布清单、浏览器截图、DOM 与动作日志
  publish-accounts/    每个抖音账号独立的 Chrome 持久化会话档案
examples/              示例请求
scripts/               启动、测试和 Schema 导出脚本
tests/                 离线单元测试和显式真实集成测试
logs/                  app、error 和逐任务 JSON 日志
```

## 环境

- Windows 10/11；
- Python 3.11+；
- Node.js 20+；
- pnpm 10+。

Remotion 会安装匹配平台的 FFmpeg/FFprobe 组件，不要求系统全局安装 FFmpeg。`/health` 会分别检查 Node、Remotion、FFmpeg、SQLite、磁盘和 TTS。

## 安装

```powershell
cd D:\codex-chat\股票回测视频生成器
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
pnpm install
Copy-Item .env.example .env
```

若 Node 不在 PATH，可在 `.env` 中把 `NODE_EXECUTABLE` 设置为 `node.exe` 的绝对路径。配置项包括端口、缓存 TTL、抓取/渲染超时、各类并发上限和最小剩余磁盘空间。

### 迁移到另一台 Windows 电脑

完整步骤见 [`TRANSFER_GUIDE.md`](TRANSFER_GUIDE.md)。迁移包内附自动安装与路径重写脚本：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-transferred-project.ps1
```

迁移包不会包含 `.venv`、`node_modules`、`.env` 或抖音 Chrome 登录档案；新电脑会保留历史数据库、成片和发布记录，但需要重新扫码登录。

## 启动

分别启动后端和前端：

```powershell
.\.venv\Scripts\stock-video-api.exe
pnpm dev:web
```

或运行：

```powershell
.\scripts\start.ps1
```

- 前端：http://127.0.0.1:5173
- API：http://127.0.0.1:8877
- OpenAPI：http://127.0.0.1:8877/docs

也可以直接使用 CLI：

```powershell
.\.venv\Scripts\stock-video-cli.exe .\examples\a-share-request.json --render
```

## 使用流程

1. 首页输入名称/代码并选择市场；
2. 必须从真实搜索结果中明确选择股票；
3. 设置买入日期、资金、分红、股数、手续费和视频规格；
4. 在任务页查看真实阶段、进度、重试和错误；
5. 回测页核对来源、抓取时间、校验、历史曲线和公司行为；
6. 预览页可编辑标题、时长和颜色，保存后发起逐帧渲染；
7. 新视频完成时会同步生成横竖封面、发布标题和互动副标题；副标题根据深坑翻红、暴涨、过山车、长期亏损、时间成本等真实行情特征选择并轮换模板，在驾驶舱点开成片即可一起预览。完整发布简介继续独立保存，文案持久化在 `data/outputs/<render_id>.copy.json`；既有成片不会被回填或重写。
8. 在「发布中心」填写本机账号标识和备注名称，点击“保存并打开扫码登录”；
9. 选择成片生成发布清单，核对该集预生成的标题/简介、动态候选标题、1–5 个话题和双封面；
10. 先执行预检；正式发布任务需开启账号权限并逐条点击“授权正式发布”。
11. 批量发布时，在首页成片库点击“批量选择”，按勾选顺序建立队列，设置账号、首条开始时间、成功后间隔和失败策略，再到发布中心对整个批次授权。

## 抖音发布工作流

发布器采用“确定性步骤优先，Agent 只做局部恢复”的结构：

```text
创建发布清单
→ 校验 MP4 / 1080×1440 竖封面 / 1440×1080 横封面
→ 检查持久化登录
→ 上传视频并等待平台转码
→ 填标题、简介、话题
→ 分别上传横封面和竖封面
→ 设置合集与自主声明
→ 截图 + DOM + 动作日志
→ READY_FOR_PUBLISH
→ 人工授权后点击发布
→ 到作品管理页核验结果
```

- 每条任务都会写入 `data/publishes/{publish_id}/publish_manifest.json`；
- 标题由回测事实和题材角度选择模板，最近使用的标题哈希与模板会进入历史记录，减少连续重复；
- 文案中的日期、年份、金额和百分比必须能与 `simulation.json` 对上，否则 API 拒绝保存；
- 旧版 1920×1080 横封面会在建立发布清单时自动居中裁切为 1440×1080，转换件只用于投稿，不覆盖原始封面；
- 默认模式是 `dry_run`，只填写并停在发布前；`immediate` 和 `scheduled` 都必须逐条人工授权；
- 账号级 `auto_publish_enabled` 只是允许“已人工授权”的任务点击发布，不会跳过任务审批；
- 遇到登录失效会打开可见浏览器扫码并保存会话；遇到短信、安全验证或页面不确定状态会交回人工；
- 确定性定位器失效时，Stagehand 只接管一个局部 UI 目标。它被禁止点击发布、提交验证码、绕过登录，且有调用次数与超时上限；
- 每次尝试会保存截图、DOM 和动作日志，可直接在发布中心查看。

批量发布队列遵循以下规则：

- 同一批次始终串行执行，下一条不会与当前上传并发；
- 间隔从上一条在作品管理页确认发布成功后开始计算，最短 5 分钟；
- 服务重启后会从 SQLite 恢复批次、顺序、等待时间和执行状态，不会补发形成突发流量；
- “暂停”会让当前正在上传的任务安全走完，再停止后续任务；
- 登录失效、短信验证或结果无法确认时，整个批次进入“需要人工处理”；
- 已发布视频默认禁止再次加入批次；未授权批次不会打开抖音或上传任何文件。

Stagehand 兜底需要模型密钥才会调用并消耗模型 Token；未配置密钥时确定性发布流程仍可运行，只是控件变化时直接进入可重试/人工状态。标题、金额和收益计算本身不调用 AI。

相关环境变量：

```text
PUBLISH_HEADLESS=false
PUBLISH_BROWSER_CHANNEL=chrome
PUBLISH_STEP_TIMEOUT_SECONDS=90
PUBLISH_UPLOAD_TIMEOUT_SECONDS=900
PUBLISH_MAX_AGENT_FALLBACKS=3
PUBLISH_AGENT_MODEL=openai/gpt-4.1-mini
OPENAI_API_KEY=...
```

`pnpm build` 会生成 `apps/publisher-agent/dist/index.js`，API 在没有显式配置
`PUBLISH_AGENT_COMMAND` 时会自动使用该入口。

状态流：

```text
CREATED → RESOLVING_SYMBOL → FETCHING_MARKET_DATA → VALIDATING_DATA
→ SIMULATING_PORTFOLIO → BUILDING_VIDEO_SPEC → RENDERING_VIDEO
→ VALIDATING_OUTPUT → COMPLETED
```

失败状态为 `FAILED_RETRYABLE`、`FAILED_FINAL`、`CANCELLED`。网络错误最多自动重试 3 次，渲染错误最多重试 2 次，退避为 5、20、60 秒；代码错误和数据校验失败不自动重试。

## 数据口径

- 使用未复权价格，再单独应用分红与拆合股，避免总回报数据重复计算公司行为；
- 请求日非交易日可顺延、回退或拒绝；
- `portfolio_value = shares × close + cash`；
- 收益率、运行高点和最大回撤均由 Decimal 驱动的确定性代码计算；
- 红利复投使用实际除息事件当日收盘价及相同股数/手续费规则；
- Provider 返回的币种、代码/交易所、日期顺序、重复、价格、间隔、异常跳变和公司行为均会校验；
- 外部源不可用或红利数据缺失时不会生成模拟行情。

缓存文件同时保存请求参数、获取时间、过期时间、缓存键、来源列表、响应行数摘要和标准化 payload。每次完成的回测还会把相关行情与公司行为复制到独立的 `market_data.json`。

## API

核心接口：

```text
GET  /health
GET  /api/providers/health
GET  /api/instruments/search?q=&market=
GET  /api/instruments/{symbol}
POST /api/simulations
GET  /api/simulations/{id}
GET  /api/simulations/{id}/series
GET  /api/simulations/{id}/download
GET/PUT /api/simulations/{id}/visualization-spec
POST /api/renders
GET  /api/renders/{id}
POST /api/renders/{id}/cancel
POST /api/renders/{id}/retry
GET  /api/jobs
GET  /api/jobs/{id}
GET  /api/jobs/{id}/events
POST /api/jobs/{id}/cancel
POST /api/jobs/{id}/retry
GET  /api/outputs
GET  /api/outputs/{id}
GET  /api/outputs/{id}/video
GET  /api/outputs/{id}/cover/landscape
GET  /api/outputs/{id}/cover/portrait
GET  /api/outputs/{id}/thumbnail
POST /api/outputs/{id}/open-folder

抖音发布：

GET/POST /api/publish/accounts
GET/POST /api/publish/accounts/{id}/login
GET/POST /api/publish/jobs
GET/PATCH /api/publish/jobs/{id}
POST /api/publish/jobs/{id}/run
POST /api/publish/jobs/{id}/approve
POST /api/publish/jobs/{id}/retry
POST /api/publish/jobs/{id}/cancel
GET /api/publish/attempts/{attempt_id}/evidence/{screenshot|dom|actions}
GET/POST /api/publish/batches
GET/PATCH /api/publish/batches/{id}
POST /api/publish/batches/{id}/approve-start
POST /api/publish/batches/{id}/pause
POST /api/publish/batches/{id}/resume
POST /api/publish/batches/{id}/cancel

自动生产总控：

GET  /api/pipeline/status            enabled、今日完成/配额、选题池水位、进行中与搁浅数
GET  /api/pipeline/runs?filter=      all|active|parked
GET  /api/pipeline/policy            读取自动生产策略
PUT  /api/pipeline/policy            保存自动生产策略（校验非法配比返回 422）
POST /api/pipeline/run-once          手动一键：取队首选题跑全流程（202 受理）
POST /api/pipeline/runs/{id}/retry   人工重跑搁浅/失败的 run
POST /api/pipeline/runs/{id}/skip    放弃并释放搁浅/失败的 run
GET  /api/pipeline/topics            选题池列表
POST /api/pipeline/topics/replenish  立即按策略补充选题（真实拉取行情）
```

## 测试

默认测试完全离线，不会把 fixture 用到生产：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
pnpm build
```

一次运行全部离线检查：

```powershell
.\scripts\test.ps1
```

真实 Provider 测试显式执行，覆盖 A 股、港股和美股日线及公司行为。yfinance 限流时健康检查会保留其真实失败原因，而全球适配器会尝试经过同样校验的 Sina 备用源：

```powershell
.\.venv\Scripts\python.exe -m pytest -m integration
```

短视频渲染管线测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "integration and render"
```

离线用例覆盖非交易日、三种股数模式、手续费、现金分红、红利复投、拆股、最大回撤、重复/空行情、上市日前请求、币种冲突、Schema 数值一致性和 SQLite 重启恢复。

## Schema 与产物

机器可读 Schema：

- `packages/schemas/simulation-request.schema.json`
- `packages/schemas/simulation.schema.json`
- `packages/schemas/visualization-spec.schema.json`

修改 Pydantic 模型后运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_schemas.py
```

真实任务产物位于 `data/simulations/{simulation_id}`，最终视频和验证报告位于 `data/outputs`。数据库与生成产物默认被 Git 忽略。

## 数据源与许可

AKShare 和 yfinance 只是数据接入软件，软件许可证不等于 Yahoo、Sina 或东方财富行情的再分发授权。Yahoo 数据尤其需要遵守其个人使用限制。商业部署或分发 Remotion/FFmpeg 二进制前，请阅读 [THIRD_PARTY.md](THIRD_PARTY.md) 并重新核对最新条款。

## 开源许可

本项目源码以 [MIT License](LICENSE) 发布。第三方依赖、行情数据源和外部平台的使用仍分别受其自身条款约束。
