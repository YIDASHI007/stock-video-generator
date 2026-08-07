# Design QA — 正式视频与钩子式封面工作流

## 视频模板

- 正式视频 composition：`StockHistoricalSimulationV1`
- 正式视频实现：`ScrollingFilledAreaStockVideo`
- 输出保持 1920 × 1080、30 fps、横屏。
- 保留开场提问、随时间推进的 K 线/面积走势、资产与日期光点、结尾拉远。
- 股票名称直接读取 `spec.instrument.name`，中英文长名称会自适应字号和行数。

## 正式封面切换

- `StockCoverLandscape` 已切换为钩子式横版封面，尺寸 1440 × 1080。
- `StockCoverPortrait` 已切换为钩子式竖版封面，尺寸 1080 × 1440。
- 封面会读取实际买入日期、股票代码、初始资金、公司名称和持有年限。
- 长公司名称会自动缩小并换行，避免挤占问题文案和核心视觉区域。
- 原版封面保留为 `StockCoverLandscapeLegacy` 与 `StockCoverPortraitLegacy`，方便回退和对比。

## WUXI 验收证据

- 正式工作流验证视频：`E:\stock-video-generator\data\design-previews\wuxi-formal-hook-cover-workflow-demo.mp4`
- 正式横版封面：`E:\stock-video-generator\data\design-previews\wuxi-formal-hook-cover-workflow-demo.cover-landscape.png`
- 正式竖版封面：`E:\stock-video-generator\data\design-previews\wuxi-formal-hook-cover-workflow-demo.cover-portrait.png`
- 验证报告：`E:\stock-video-generator\data\design-previews\wuxi-formal-hook-cover-workflow-demo.mp4.validation.json`
- WUXI 实际输出横版封面：`E:\stock-video-generator\data\outputs\286d72e1-41df-4b43-8583-fb2d92ec92f5.cover-landscape.png`
- WUXI 实际输出竖版封面：`E:\stock-video-generator\data\outputs\286d72e1-41df-4b43-8583-fb2d92ec92f5.cover-portrait.png`
- 横版 SHA256：`FD70F24DC4FDDB5A319DD59BF6E9F23D6A9BE55BD60FC87FEFA8DB4ED3F63AC4`
- 竖版 SHA256：`EA771F44DC89A27115E9AC8F289B0B0D8B539634271F9432D19E0F74CA1D9F59`
- 实际输出封面与正式工作流验证封面的哈希一致。

## 保留的工作流

- BGM 来源、音量和淡出逻辑未修改。
- 视频主体、模拟计算、队列、发布、清理和选题去重逻辑未修改。
- 本次没有修改自动生产策略的开关或配额。
- WUXI 原封面已备份到：
  - `E:\stock-video-generator\data\design-previews\wuxi-original-cover-landscape-before-hook.png`
  - `E:\stock-video-generator\data\design-previews\wuxi-original-cover-portrait-before-hook.png`

## 验证结果

- Renderer validation：通过，无错误。
- 视频：H.264、1920 × 1080、30 fps、60.053333 秒、yuv420p、BT.709。
- 音频：AAC。
- 横版封面：1440 × 1080，907,241 bytes。
- 竖版封面：1080 × 1440，725,222 bytes。
- Python：123 passed，7 explicitly deselected。
- TypeScript workspace tests：通过。
- Frontend、renderer、publisher-agent builds：通过。

final result: passed

---

# Production Result Cover Workflow QA

- Source visual truth: `C:\Users\simon\AppData\Local\Temp\codex-clipboard-4505bdfb-496a-4e9c-bbb8-74f2883463fe.png`
- Production landscape composition: `StockCoverLandscape` → `StockResultShockLandscapeCover`
- Production portrait composition: `StockCoverPortrait` → `StockResultShockPortraitCover`
- Main implementation screenshot: `E:\stock-video-generator\data\design-previews\production-result-cover-validation\amd-production-landscape.png`
- Full-view comparison: `E:\stock-video-generator\data\design-previews\production-result-cover-validation\reference-vs-production-amd.png`
- Source pixels: 1448 × 1086, normalized to 1440 × 1080.
- Implementation pixels / CSS viewport: landscape 1440 × 1080; portrait 1080 × 1440; 1× density.
- State: frame 0 static covers rendered through the production composition IDs.

## Fidelity surfaces

- Typography: centered three-level hierarchy, heavyweight display type, single-line landscape result, and near-full-width percentage match the approved reference.
- Spacing: title, result, divider, and percentage remain in the approved vertical bands in both output ratios.
- Colors: ivory/gold gain palette matches the reference; negative return keeps the layout and switches only the percentage to red.
- Data fidelity: every background curve is read from the current simulation series; no fixed screenshot or fabricated curve is used.
- Copy: title uses the buy year, formatted initial capital, and the shortest readable company identity; result and return use the current simulation summary.

## Responsive edge cases

- Standard gain: AMD, `8,993%`, landscape and portrait passed.
- Long English company: Taiwan Semiconductor Manufacturing Company Limited initially clipped in landscape (P2). Fixed by stripping legal suffixes and falling back to `TSM` when the remaining title is still too wide. Post-fix landscape and portrait passed.
- Long legal name plus loss: Scienjoy Holding Corporation is shortened to `Scienjoy`; `-89.5%` uses the loss-red treatment without overflow.
- Extreme values: synthetic layout-only stress case `382.6亿` and `3,826,000%` dynamically reduces numeric type size and passes in both ratios.
- Monetary units: values are compacted through `万`, `亿`, `万亿`, `京`, and `垓` before type fitting.

No focused crop was required because title boundaries, result width, and percentage boundaries are clearly readable in the full-resolution outputs. Workspace TypeScript tests passed for renderer, web, and publisher-agent.

final result: passed

---

# Result Shock Cover V3 — Reference Fidelity QA

- Source visual truth: `C:\Users\simon\AppData\Local\Temp\codex-clipboard-4505bdfb-496a-4e9c-bbb8-74f2883463fe.png`
- Implementation: `E:\stock-video-generator\data\design-previews\result-shock-amd-v3\amd-result-shock-v3-landscape.png`
- Portrait adaptation: `E:\stock-video-generator\data\design-previews\result-shock-amd-v3\amd-result-shock-v3-portrait.png`
- Full-view comparison: `E:\stock-video-generator\data\design-previews\result-shock-amd-v3\reference-vs-amd-v3.png`
- Source pixels: 1448 × 1086; normalized to 1440 × 1080 for comparison.
- Implementation pixels / CSS viewport: 1440 × 1080 at 1× density.
- State: static cover at frame 0, AMD real simulation data.

## Required fidelity surfaces

- Typography: three-level heavy Chinese display hierarchy matches the reference; title, result line, and percentage remain single-line in landscape.
- Spacing and layout: title, result line, gold divider, and percentage use the same center alignment and approximate vertical bands as the reference. The shorter AMD percentage is widened to preserve the reference's near-full-width impact.
- Colors: dark teal-to-burgundy background, ivory result text, and high-luminance gold percentage match the reference's foreground balance.
- Image/data fidelity: decorative crypto emblem is intentionally replaced by a low-opacity AMD watermark; the background line is the actual AMD portfolio series rather than a fabricated asset.
- Copy: only the instrument, initial capital, final value, and return percentage change; the three-line sentence structure is preserved.

## Comparison history

- Earlier P1: V2 used left alignment, a fluorescent green label, and a slanted percentage panel, materially changing the source composition.
- Fix: rebuilt the cover around the reference's centered three-band layout and white/gold palette.
- Earlier P2: the shorter `8,993%` result occupied less width than the reference percentage.
- Fix: increased and horizontally scaled the percentage while preserving its height and center line.
- Post-fix evidence: `reference-vs-amd-v3.png` shows matching title position, result-line scale, divider position, and near-full-width percentage treatment.

## Residual P3

- The reference uses richer raster grain and embossed metallic texture. V3 keeps deterministic CSS text treatment so the production workflow can reproduce it consistently for every instrument.

Focused-region comparison was not required because all important text and spacing differences are legible in the normalized full-view comparison.

final result: passed
