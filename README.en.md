<div align="center">

# Qball

**A tiny emotive AI assistant that lives on your PC — one command to install, keys and data stay local**

[![Release](https://img.shields.io/github/v/release/woshi32s/Qball)](https://github.com/woshi32s/Qball/releases)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D4)](#install-windows)
[![License](https://img.shields.io/badge/license-non--commercial-blue)](LICENSE)

[中文](README.md) | [English](README.en.md)

</div>

![Onboarding](docs/screenshots/welcome.png)
![Model orbit](docs/screenshots/orbit.png)

## What is it

Qball is a personal AI assistant running on **your own machine** — no server to rent, no sign-up, your model key never leaves your computer (`~/.qball`).

- **One-command install**: downloads, installs to `%LOCALAPPDATA%\Qball`, sets up auto-start and a Start Menu entry. Uninstall with `qball uninstall`.
- **Open and go**: a browser UI with an expressive ball character, a cinematic onboarding, streaming chat and text-to-speech (edge-tts).
- **Any OpenAI-compatible API**: bring your own base URL / key / model. BYOK, no lock-in.
- **Local-first**: chats and settings stay on your machine; public hosting is optional.
- **PWA**: add to home screen on desktop or mobile.
- **Self-evolving** (advanced): it practices on real tasks in the background and improves its own agent — see below.

## Self-evolution: improving the agent, not training the model

Qball can practice on real tasks in the background and make **itself** better: it reflects on failures,
distills lessons into behavior rules and skills, and can even patch its own code. No GPU needed —
and **the model weights are never touched**.

> The model stays exactly the cloud API you configured — not a single byte changes.
> What improves is the **agent** (rules, skill library, workflow, code).

Every proposed change must pass three gates: **score gate / path allowlist / full test suite** —
only green changes are auto-adopted (git-versioned, one-click rollback). See **[docs/EVOLUTION.md](docs/EVOLUTION.md)** (Chinese).

## Install (Windows)

### Option 1 — one command (recommended)

Paste into PowerShell:

```powershell
iwr -useb https://cdn.jsdelivr.net/gh/woshi32s/Qball@main/install.ps1 | iex
```

It fetches the latest release, verifies the SHA-256, installs, enables auto-start and opens the UI.

Without auto-start:

```powershell
& ([scriptblock]::Create((iwr -useb https://cdn.jsdelivr.net/gh/woshi32s/Qball@main/install.ps1))) -NoStartup
```

### Option 2 — portable EXE

Download `Qball.exe` from [Releases](https://github.com/woshi32s/Qball/releases) and double-click it — a single self-contained file, no Python needed. Run the installer later if you want PATH commands and auto-start.

> Windows SmartScreen may warn about an unsigned binary: choose "More info → Run anyway".

## qball commands

| Command | Description |
|---|---|
| `qball start` / `qball stop` | Start / stop |
| `qball status` / `qball doctor` | Status / diagnostics |
| `qball logs` | Recent logs |
| `qball update` | Update to latest |
| `qball autostart on` / `off` | Toggle auto-start |
| `qball uninstall` / `-Purge` | Uninstall (with data if `-Purge`) |

## Where data lives

| What | Where |
|---|---|
| Config / logs / port | `%USERPROFILE%\.qball` |
| Chat history | browser localStorage (local only) |
| App | `%LOCALAPPDATA%\Qball` |

## FAQ

- **Download slow or failing?** The installer tries mirrors automatically; you can also grab `Qball.exe` from Releases manually.
- **Port in use?** Qball picks a free port in 8600–8610 and remembers it.
- **Double-click again?** It reopens the running instance instead of starting a second one.
- **Remove everything?** `qball uninstall -Purge`, then delete `%LOCALAPPDATA%\Qball`.

## Run from source (dev)

```bash
pip install -r requirements.txt
python server.py          # http://127.0.0.1:8600
```

Build the single-file EXE:

```powershell
pip install pyinstaller
python -m PyInstaller Qball.spec --noconfirm    # dist\Qball.exe
```

Releases: bump `VERSION` in `server.py`, push a tag (`v0.2.0`), and GitHub Actions builds, smoke-tests and publishes the release (updating `version.json`).

## Deploy to the web (optional)

Want a link others can open? See [DEPLOY.md](DEPLOY.md) (Docker + Caddy HTTPS). Not required for local use.

## Credits

- Ball character and emotion engine come from the open-source **Emotion Ball** project by **sam70361** (learning/exchange license, commercial use prohibited without written permission — 1251579308@qq.com). Online preview: https://emotion-balls.vercel.app/
- Model brand icons: [lobe-icons](https://github.com/lobehub/lobe-icons) (MIT). Trademarks belong to their owners; icons are used for identification only.
