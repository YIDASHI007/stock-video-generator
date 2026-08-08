# Desktop Launcher v0.1.3 QA

## Visual composition

- Native window bounds: 420 × 300, centered on the active display.
- The launcher uses an opaque near-black surface with a one-pixel neutral border.
- The header reads `股票视频工作台` and uses a restrained Fluent play mark.
- The active check is shown in one compact status card with text plus a spinner,
  check, warning, or error glyph.
- Five progress dots show the position in the startup sequence.
- Update buttons are hidden during normal startup and appear only when a newer
  release is available.
- Reference captures:
  - `build/launcher-splash-v2.png`
  - `build/launcher-update-prompt.png`

## Startup sequence

1. Ensure the local backend service is running; start it when the configured port
   is free.
2. Verify the configured port and its owning process.
3. Verify that at least one market-data provider is available.
4. Verify Node.js, Remotion, and FFmpeg.
5. Check the public GitHub release channel for updates.
6. For each check: show loading, show the result briefly, then advance.
7. When all critical checks pass, open the browser workbench and hide the launcher
   in the Windows notification area.
8. Keep a concise blocking error visible when startup cannot continue.

## Updates and tray lifecycle

- When a release is available, the launcher offers `更新到 vX.X.X` and `暂不更新`.
- The update action downloads the release, stops only this application's verified
  backend process, applies the update, and restarts the application.
- The tray menu contains `打开工作台`, `检查更新`, and `退出`.
- Closing the visible launcher hides it when the tray is active; `退出` releases the
  tray host and stops the verified local backend.
- A named Windows mutex limits each configured port to one launcher/tray host.
  Launching the shortcut again opens the existing workbench without creating a
  duplicate tray icon or a second lifecycle owner.

## Assets and packaging

- `launch-center-icon.png` is used by the window and notification-area icon.
- `launch-center.ico` is used by PyInstaller, Velopack, desktop shortcuts, and the
  Start menu shortcut.
- Launcher assets are included in both the Python package data and the frozen
  application bundle.

## Accessibility and motion

- Status is communicated by text and glyph, not color alone.
- Windows client-animation preferences are respected.
- Escape remains a reliable dismissal path before the tray is active.
- Loading indicators remain visible during long-running checks.

## Release validation

- Ruff: passed.
- Python: 146 passed, 7 explicitly deselected.
- Frontend, renderer, and publisher-agent builds: passed.
- TypeScript lint/type checks: passed.
- PowerShell release script parsing: passed.
- Local Windows v0.1.3 installer build: passed.

final result: passed
