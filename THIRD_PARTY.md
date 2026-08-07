# 第三方组件与数据源

以下版本来自当前锁文件或已安装环境。发布、商业部署或数据再分发前，必须重新核对最新许可证和上游数据条款。

| 项目 | 当前版本 | 链接 | 使用位置 | 许可证 | 商业/数据限制 |
|---|---:|---|---|---|---|
| AKShare | 1.18.78 | https://github.com/akfamily/akshare | A 股搜索/日线/公司行为；港股和美股 Sina 未复权日线及复权因子备用适配器 | MIT | 软件许可不授予新浪、东方财富等上游数据的商业使用或再分发权；部署者须遵守源站条款和频率限制。 |
| yfinance | 1.5.2 | https://github.com/ranaroussi/yfinance | 港股、美股搜索、日线与公司行为 | Apache-2.0 | 非 Yahoo 官方 SDK；项目明确提示 Yahoo 数据用于研究/教育并受个人使用条款约束。 |
| FastAPI | 0.140.0 | https://github.com/fastapi/fastapi | HTTP API 与 OpenAPI | MIT | 可商业使用，分发时保留许可声明。 |
| Pydantic | 2.13.4 | https://github.com/pydantic/pydantic | 数据模型与 JSON Schema | MIT | 可商业使用，分发时保留许可声明。 |
| SQLAlchemy | 2.0.51 | https://github.com/sqlalchemy/sqlalchemy | SQLite ORM | MIT | 可商业使用，分发时保留许可声明。 |
| pandas | 3.0.5 | https://github.com/pandas-dev/pandas | Provider 数据标准化 | BSD-3-Clause | 可商业使用，分发时保留许可声明。 |
| NumPy | 2.5.1 | https://github.com/numpy/numpy | 数值依赖 | BSD-3-Clause | 可商业使用，分发时保留许可声明。 |
| React / React DOM | 19.2.8 | https://github.com/facebook/react | 前端与视频组件 | MIT | 可商业使用，分发时保留许可声明。 |
| Remotion | 4.0.499 | https://github.com/remotion-dev/remotion | Player、逐帧渲染和媒体探测 | Remotion License | 个人及最多 3 人组织通常可免费使用；更大组织等情形需按当前 Remotion 许可购买。以官方最新许可为准。 |
| D3 Shape | 3.2.0 | https://github.com/d3/d3-shape | 资产曲线 SVG path | ISC | 可商业使用，分发时保留许可声明。 |
| Vite | 8.1.5 | https://github.com/vitejs/vite | 前端构建 | MIT | 可商业使用，分发时保留许可声明。 |
| TypeScript | 5.9.3 | https://github.com/microsoft/TypeScript | 类型检查 | Apache-2.0 | 可商业使用，分发时保留许可声明。 |
| Node.js | 24.x | https://nodejs.org/ | 安装版内置的 JavaScript 运行时 | MIT | 分发时保留 Node.js 及其随附第三方许可。 |
| Velopack | 1.2.0 | https://velopack.io/ | Windows 安装、快捷方式与增量自动更新 | MIT | 安装包保持其许可声明。 |
| Patchright Python | 1.61.2 | https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python | 抖音创作者中心持久化浏览器与确定性页面操作 | Apache-2.0 | 可商业使用，分发时保留许可与 NOTICE；平台自动化仍须遵守抖音规则。 |
| Stagehand | 3.7.1 | https://github.com/browserbase/stagehand | 发布页面控件变化时的受限局部 Agent 兜底 | MIT | 模型服务另有计费与使用条款；本项目禁止 Agent 点击最终发布或处理安全验证。 |
| social-auto-upload | 2026-07-27 查阅 | https://github.com/dreammis/social-auto-upload | 仅参考其抖音创作者中心页面路径和控件交互顺序，本项目重新实现状态机、双封面、简介回读、审批与证据机制 | MIT | 非官方接口；上游可随平台页面变化而失效，使用者负责遵守抖音平台规则。 |
| FFmpeg / FFprobe | Remotion 4.0.499 随附构建 | https://ffmpeg.org/ | H.264 编码及媒体探测 | 取决于实际构建的 LGPL/GPL 配置 | 分发前必须核对随附二进制的构建选项、链接方式和编码器；H.264 在部分地区可能涉及专利许可。 |
| SQLite | Python 标准库随附 | https://sqlite.org/ | 持久化任务和元数据 | Public Domain | SQLite 本身无常见商业许可限制。 |

## 已评估但未引入

- `mplfinance`：当前产品只需要资产价值折线，D3 Shape + SVG 已覆盖需求；
- `Manim`：没有同时维护第二套视频渲染技术栈；
- `vectorbt`：核心回测逻辑较小且需要完全可审计的确定性规则，因此未作为依赖。

## 重要说明

- 数据 Provider 可用不代表拥有缓存、展示、商业使用或再分发行情的权利；
- 生产部署者负责检查 Yahoo Finance、东方财富、新浪财经及交易所的最新条款；
- 本项目不包含数据源 API 密钥，也不会在日志中记录 Cookie、Authorization 头或密钥；
- 抖音网页自动化不是抖音官方开放平台发布 API；页面结构、风控和账号权限变化可能要求人工接管；
- 本文件不是法律意见。
