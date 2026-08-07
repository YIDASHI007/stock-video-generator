# Design QA — Video V1

## Comparison target

- Main-screen source: `C:\Users\1\.codex\generated_images\019f9baf-e0eb-7921-9059-6c55030176c7\call_CYmP4lbSgS4mHWpAheD2dGnP.png`
- Ending-screen source: `C:\Users\1\.codex\generated_images\019f9baf-e0eb-7921-9059-6c55030176c7\call_UqueKwd9M3vlwbHoLiRggG5W.png`
- Main implementation frame: `D:\codex-chat\股票回测视频生成器\data\outputs\redesign-main-final.jpg`
- Event-state implementation frame: `D:\codex-chat\股票回测视频生成器\data\outputs\redesign-event.jpg`
- Ending implementation frame: `D:\codex-chat\股票回测视频生成器\data\outputs\redesign-finale.jpg`
- Main full-view comparison: `D:\codex-chat\股票回测视频生成器\data\outputs\design-qa-main-comparison.jpg`
- Main focused comparison: `D:\codex-chat\股票回测视频生成器\data\outputs\design-qa-main-focus.jpg`
- Ending full-view comparison: `D:\codex-chat\股票回测视频生成器\data\outputs\design-qa-finale-comparison.jpg`
- Ending focused comparison: `D:\codex-chat\股票回测视频生成器\data\outputs\design-qa-finale-focus.jpg`

## Normalization

- Source images: 1672 × 941, 16:9.
- Rendered implementation: 1920 × 1080, density 1.
- Video: 1920 × 1080, 30 fps, 60.05 seconds, H.264/AAC.
- Full-view comparisons normalize the source to the implementation's 16:9 viewport.
- Focused comparisons align the top data band and the ending result-metric region independently.
- States reviewed: normal rolling playback, active historical event, ending transition, and final ending frame.

## Findings

- No actionable P0, P1, or P2 differences remain.
- Typography: the compact top labels, high-contrast values, large green return value, and centered ending metrics reproduce the selected hierarchy. The implementation uses `10年持有结果` instead of the source mock's Chinese numeral; this preserves the same meaning while allowing the duration to remain data-driven.
- Spacing and layout: duplicated title, investment sentence, instrument row, and bottom summary card have been consolidated into one top information band. The chart receives substantially more vertical space and the ending metrics sit on a clear three-column grid.
- Colors: the near-black navy background, cool gray secondary text, restrained dividers, white principal value, and emerald-green gain values remain consistent with the selected visual direction.
- Image quality: all reviewed frames are direct 1920 × 1080 MP4 captures. Chart lines, type, dividers, glow, and event markers are rendered elements rather than enlarged screenshot assets.
- Copy and content: purchase date, principal, held stock name, current/final asset, return rate, cumulative profit, ticker, and holding period are all present in their intended states. Strategy wording has been removed.
- Historical events: event cards remain attached to their event time on the chart, move with the timeline, fade as they pass, and do not appear in the final ending screen.
- Intentional dynamic difference: the static main-screen mock shows final values and a full-history chart. During video playback the implementation correctly shows current values and a rolling time window; the full-history chart and final values appear in the ending state.

## Comparison history

- Previous layout repeated purchase information across the title area, ticker row, and bottom summary card.
- The main layout was rebuilt as a single top data band matching the selected third concept.
- The chart bounds were expanded after removing redundant regions.
- A dedicated ending overlay was added with enlarged principal, cumulative return, and cumulative profit values centered on screen.
- The former strategy field was replaced with `持有股票 / AMD`; the ending footer now contains only the stock name and date range.
- Historical-event layers continue during normal playback and are fully suppressed during the ending.
- Post-change TypeScript checks passed and the final MP4 validation reported no errors.

## Open questions

- None.

## Implementation checklist

- [x] Apply the selected third main-screen design.
- [x] Preserve moving and fading event annotations.
- [x] Remove event markers and cards from the ending.
- [x] Add the enlarged three-metric ending screen.
- [x] Replace the strategy field with the held stock name.
- [x] Remove strategy wording from the ending footer.
- [x] Keep values and holding duration data-driven.
- [x] Type-check the workspace.
- [x] Render and validate the revised MP4.
- [x] Compare full views and focused regions against both selected mocks.

final result: passed
