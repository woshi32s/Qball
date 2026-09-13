# 部署指南(公网 · Docker)

> 上线前请先确认你已获得 Emotion Ball 核心引擎的**商业授权**(如需商用):
> 联系版权方 sam70361 · 1251579308@qq.com,详见 `LICENSE-COMMERCIAL.md`。

## 一、准备

| 需要 | 说明 |
|---|---|
| 云服务器 | 1 核 1G 起即可(海外免备案;国内服务器需完成 ICP 备案后才能解析域名访问) |
| 域名 | 有自有域名最好;没有就用免费的 DuckDNS 子域名(见「一点五」) |
| 服务器软件 | 安装 Docker 与 Docker Compose(官方脚本一键安装即可) |
| 模型 Key | 无需准备 —— 访客自带 Key(BYOK) |

## 一点五、没有域名?用 DuckDNS 免费子域名(推荐)

1. 打开 https://www.duckdns.org ,用 GitHub / Google 账号登录
2. 创建一个子域名,例如 `ballie` → 得到 `ballie.duckdns.org`
3. 在页面上把 **current ip** 填成你的服务器公网 IP(或按页面提示在服务器上配置自动更新)
4. 部署时 `.env` 里填 `DOMAIN=ballie.duckdns.org`,其余步骤不变(Caddy 会自动为该域名签发 HTTPS 证书)

> 注意:国内大陆服务器解析未备案域名时,80 端口访问可能被运营商拦截;DuckDNS 方案在**海外服务器**上最稳定。
> 需要零配置的即时链接(临时,重启会变):在服务器上运行 `cloudflared tunnel --url http://localhost:8600`,会直接给出一个 `https://xxx.trycloudflare.com` 链接。

## 二、部署

```bash
# 1. 把项目上传到服务器(或 git clone),进入项目目录
cd emotion-ball

# 2. 准备环境变量
cp .env.example .env
vim .env        # 至少填写 DOMAIN;ACCESS_CODE / ADMIN_TOKEN 可选

# 3. 构建并启动(app + Caddy 自动申请 HTTPS 证书)
docker compose up -d --build

# 4. 查看状态与日志
docker compose ps
docker compose logs -f app
```

完成后访问 `https://你的域名`。首次部署 Caddy 申请证书约需 10~60 秒。

## 三、环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `DOMAIN` | ✅ | 你的域名(不含 http://) |
| `ACCESS_CODE` | 否 | 访客访问码;不填 = 不验码 |
| `B_AI_KEY` | 否 | 站点内置模型 Key(备用,不建议在 BYOK 模式配置) |
| `ADMIN_TOKEN` | 否 | 管理接口令牌(改服务端配置/拉取模型列表时需要) |
| `B_AI_BASE` | 否 | 内置接口地址,默认 `https://api.b.ai/v1` |
| `B_AI_MODEL` | 否 | 内置模型名,默认 `qwen3.8-flash` |
| `TTS_VOICE` | 否 | edge-tts 音色,默认 `zh-CN-XiaoxiaoNeural` |
| `TTS_ENABLED` | 否 | `1`/`0`,公网可关掉语音省带宽 |
| `DAILY_LLM_LIMIT` | 否 | 每日请求上限,**默认 0 = 关闭**(仅对内置 Key 生效) |
| `RATE_CHAT_PER_MIN` | 否 | 单 IP 每分钟聊天次数,**默认 0 = 关闭** |
| `RATE_TTS_PER_MIN` | 否 | 单 IP 每分钟语音次数,**默认 0 = 关闭** |

修改 `.env` 后执行 `docker compose up -d` 生效(不必重新 build)。

## 三点五、访客自带 Key(BYOK)+ 首次引导

默认就是 BYOK 模式(不配置 `B_AI_KEY`):

- 访客第一次打开会进入**沉浸式引导**:小球自我介绍 → 功能介绍 → 填写 API 地址/Key → 选择模型 → 小球亲自测试连接 → 进入聊天
- 配置**只存在访客自己的浏览器**(localStorage),请求经过你的服务器**纯转发**,不落库、不共享
- 访客用量完全走他自己的 Key,你不承担费用;限流默认关闭,无需配置
- 服务器即使配置了 `B_AI_KEY` 也只是备用(脚本调用等场景),页面上不会给访客使用
- 安全检查:静态白名单确保 `.env`/`config.json` 不可下载;管理接口仅限 `ADMIN_TOKEN`

## 四、更新版本

```bash
docker compose up -d --build
```

前端页面由 Service Worker 做 stale-while-revalidate 缓存,访客刷新两次即可看到新版。

## 五、安全设计(已内置)

- API Key 仅保存在服务器环境变量,浏览器永远拿不到
- 静态文件白名单:`.env`、`config.json`、`server.py` 等文件**不可被下载**
- 访问码校验(常量时间比较)、单 IP 限流、全站每日额度
- 管理接口仅限 `ADMIN_TOKEN`(或服务器本机)
- Caddy 自动 HTTPS;只暴露 80/443,应用端口不出容器网络

## 六、注意事项

1. **备份**:`caddy_data` 卷保存证书,重建容器不受影响;聊天记录在访客浏览器本地,服务器不存。
2. **语音合成**:当前使用 `edge-tts`(微软 Edge 朗读的非官方接口),免费但无 SLA;正式商业化建议更换为官方语音服务(Azure Speech 等),改动只需替换 `server.py` 的 `api_tts`。
3. **合规**:面向公众提供生成式 AI 服务,请遵守所在地法规(如中国的《生成式人工智能服务管理暂行办法》,需要内容标识/安全评估/备案时请自行落实);本站对话数据不落库。
4. **多实例**:限流与每日额度是进程内内存实现,多副本部署需要改用 Redis;单机部署无需处理。
5. **不要把 8600 端口直接暴露公网**:只让 Caddy 转发(compose 已如此配置),否则 `X-Forwarded-For` 限流可被伪造。

## 七、本地开发

```bash
pip install -r requirements.txt
python server.py          # 127.0.0.1:8600,自动打开浏览器(本机访问自动视为管理员)
```
