<div align="center">

# Qball

**一个会做表情的 AI 小助手 —— 开场演出式引导 · 访客自带 Key · 点开即用**

[![License](https://img.shields.io/badge/license-非商业授权-blue)](LICENSE)
[![Deploy](https://img.shields.io/badge/deploy-docker%20%2B%20caddy-2496ED)](DEPLOY.md)
[![PWA](https://img.shields.io/badge/PWA-ready-5A0FC8)](#)

[中文](README.md) | [English](README.en.md)

</div>

![开场引导](docs/screenshots/welcome.png)
![品牌模型轨道](docs/screenshots/orbit.png)

## 这是什么

Qball 是一个长在网页里的 AI 小助手,有一颗会做表情的球和一套**开场演出式**的使用体验:

- **开场演出**:镜头弹簧运镜 → 大字逐字浮现 → 点一下球 → 能力图标依次爆破 → 输入卡片弹出(自动聚焦+手形引导)→ 12 个品牌模型气泡绕场 → 选中特写「嗖」地飞进小球大脑 → 小球亲自测试连接 → 进入聊天
- **对话体验**:流式输出、思考分段卡片、增量 Markdown 渲染、自动跟随滚动(你往上拖,它绝不抢)
- **语音**:AI 回答自动朗读(edge-tts + 机器人音色);点击/长按麦克风即可说话
- **访客自带 Key(BYOK)**:访客的接口配置只存在自己的浏览器里,服务端只做转发,不落库、不共享;站主零成本
- **PWA / 触屏适配**:手机加到主屏就像原生 App;桌面端有悬停交互、快捷键和开发者面板(F3)

## 快速开始(本机)

```bash
pip install -r requirements.txt
python server.py        # 自动打开 http://127.0.0.1:8600
```

Windows 下也可以直接双击 `start.bat`(优先使用 `Qball.exe`,其次 `EmotionBall.exe`,都没有则用 Python 运行)。

> 本机打开时自动识别为管理员,无需访问码;首次进入跟随引导填写自己的接口即可。

## 部署到公网(别人点链接就能用)

完整步骤见 **[DEPLOY.md](DEPLOY.md)**:Docker 一条命令 + Caddy 自动 HTTPS。

```bash
cp .env.example .env    # 至少填写 DOMAIN(没有域名可用免费的 DuckDNS 子域名)
docker compose up -d --build
```

## 配置说明

| 角色 | 怎么配 |
|---|---|
| 访客 | 首次打开跟随引导,填写任意 OpenAI 兼容接口(地址 / Key / 模型),配置仅存于其浏览器 |
| 站主 | 环境变量(见 `.env.example`);**切勿提交 `config.json`**(已在 .gitignore,且服务端静态白名单拒绝访问) |

## 常见问题

- **语音不响?** 公网必须 HTTPS 才能使用麦克风;检查浏览器是否拦截自动播放。
- **拉取模型失败?** 部分接口不支持 `/models`,可直接手动输入模型名。
- **想重看开场引导?** 设置面板底部有「重播开场引导」。

## 声明

- **小球形象与表情引擎来源于开源项目 Emotion Ball**(作者 **sam70361**),遵循其「学习交流许可(禁止商业用途)」:
  - 非商业使用:请保留 [LICENSE](LICENSE) 与署名;
  - 商业使用:需事先取得书面授权 —— **1251579308@qq.com**(条款见 [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md));
  - 原项目在线预览:https://emotion-balls.vercel.app/
- 模型品牌图标:[lobe-icons](https://github.com/lobehub/lobe-icons)(MIT);商标归各自所有者,仅用于识别。
