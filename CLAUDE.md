# Rory Assistant

Desktop chat app (pywebview + HTML/JS) for developers, by Rory Systems. It runs **Claude Code** underneath via the **Claude Agent SDK** (Python), using the user's subscription (no API key). The Windows floating button (`%LOCALAPPDATA%\ClaudeAssistente\claude-tray.ps1`) opens this window.

## Docs (read on demand, not every message)

- `docs/arquitetura.md` — technical: file map, message flow, sessions, permissions, compaction, media, `!command`, file explorer, settings.
- `docs/funcionalidades.md` — what each feature does for the user.
- `docs/desenvolvimento.md` — run/test/build commands, gotchas (Smart App Control, WinForms threading, SDK buffer, ports…), status, next steps.
- `README.md` — public GitHub page (pt-BR), install commands.
- `install.ps1` / `install.sh` — one-command install from source (how they work: `docs/desenvolvimento.md`).

**Keep docs in sync**: when you change behavior, update the matching `docs/*.md` (pt-BR). Keep this file lean.

## Key files

`app/main.py` (window + `Api`, methods called by JS) · `app/agent.py` (`AgentHub`/`Session`, one `ClaudeSDKClient` per conversation) · `app/store.py` (`%APPDATA%\Rory Assistant\state.json`, `history/`) · `app/convert.py` · `app/media.py` · `app/usage.py` · `app/shellcomplete.py` · `ui/app.js` · `ui/index.html` · `ui/style.css` · `packaging/` · `.github/workflows/build.yml`.

## Rules

- **After touching `ui/*.js`, run `.venv\Scripts\python.exe scripts\check_js.py`** (no Node on this PC). A syntax error makes the app open "frozen".
- Also check `.venv\Scripts\python.exe -c "import app.main"` after Python changes.
- **Visual tests are done by the user.** Don't launch test instances or take screenshots unless asked.
- **Never close the user's running instance** (child of the floating button) and **never click "Sair da conta"**: it kills the session you are running in. Test in a separate instance with `RORY_DATA_DIR=<temp dir>`. Changes only apply after the user reopens the app.
- **Build installers only when asked** (`packaging\build-windows.ps1`; macOS/Linux only via GitHub Actions).
- All WinForms/WebView2 access goes inside `form.BeginInvoke(Action(...))` (deadlock otherwise).
- All markdown rendering goes through DOMPurify; links open in the browser.
- The app never writes into the project folder.
- UI text, docs and commit messages are in pt-BR.
- New Claude model → add its price to `PRICES` in `app/usage.py`.
- Git: repo on the **rorysystems** account; no Claude co-authorship.
