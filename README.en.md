<div align="center">

# Ballie · An expressive AI ball

**A tiny expressive AI assistant for the web — cinematic onboarding · bring-your-own-key**

[![License](https://img.shields.io/badge/license-non--commercial-blue)](LICENSE)
[![Deploy](https://img.shields.io/badge/deploy-docker%20%2B%20caddy-2496ED)](DEPLOY.md)

[中文](README.md) | [English](README.en.md)

</div>

![Onboarding](docs/screenshots/welcome.png)

## What is this

Ballie is an AI assistant living inside an expressive ball on a web page, wrapped in a **cinematic onboarding experience**:

- **Opening show**: spring-driven camera moves, typewriter headline, pop-able feature icons, an auto-focused input card, 12 brand model bubbles orbiting the panel, close-up selection flying into the ball's brain, live connection test — then chat.
- **Chat**: streaming output, collapsible thinking cards, incremental Markdown, smart auto-follow scroll (yields instantly when you scroll up).
- **Voice**: text-to-speech replies (edge-tts + robot timbre), tap/hold-to-talk microphone input.
- **BYOK**: visitor API config lives only in their own browser; the server just proxies — nothing stored, nothing shared.
- **PWA / touch-ready**: add to home screen on mobile; hover interactions, shortcuts and a dev panel (F3) on desktop.

## Quick start (local)

```bash
pip install -r requirements.txt
python server.py        # opens http://127.0.0.1:8600
```

On Windows you can also double-click `start.bat`.

## Deploy to the public web

See **[DEPLOY.md](DEPLOY.md)**: one Docker command with automatic HTTPS via Caddy.
A free DuckDNS subdomain works fine if you don't own a domain.

```bash
cp .env.example .env    # set DOMAIN
docker compose up -d --build
```

## Credits & License

- The ball engine comes from the open-source **Emotion Ball** project by **sam70361**, under a *Learning & Exchange (non-commercial)* license:
  - keep [LICENSE](LICENSE) and attribution for non-commercial use;
  - **commercial use requires a written license**: 1251579308@qq.com (see [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)).
- Model brand icons: [lobe-icons](https://github.com/lobehub/lobe-icons) (MIT); trademarks belong to their owners.
- Motion language inspired by [newo-ether/Agora](https://github.com/newo-ether/Agora) (MIT).
