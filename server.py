"""情绪小球 · 生产版服务端(Flask)

本地运行:  python server.py                  # 自动打开浏览器,默认 127.0.0.1:8600
公网部署:  waitress-serve --listen=0.0.0.0:8600 server:app   # 见 DEPLOY.md / docker-compose.yml

环境变量(公网部署推荐全部用环境变量,勿把密钥写进文件):
  B_AI_BASE / B_AI_KEY / B_AI_MODEL / TTS_VOICE   模型与语音配置
  ACCESS_CODE      访问码;不设置 = 不验码(仅适合本地)
  ADMIN_TOKEN      管理员令牌,用于 POST /api/config 等管理接口
  TTS_ENABLED      1/0 是否开放语音合成(公网可关)
  RATE_CHAT_PER_MIN / RATE_TTS_PER_MIN / RATE_API_PER_MIN
  DAILY_LLM_LIMIT  全站每日 LLM 请求上限(按自然日)
  HOST / PORT      监听地址与端口(本地 main() 使用)
"""
import asyncio
import hmac
import json
import logging
import mimetypes
import os
import queue
import re
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

try:
    import edge_tts
except ImportError:
    print("edge-tts is not installed. Run:  pip install -r requirements.txt")
    sys.exit(1)

from flask import Flask, Response, jsonify, request, send_file

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent

MAX_TTS_CHARS = 1500
FIRST_CHUNK_TIMEOUT = 30
NEXT_CHUNK_TIMEOUT = 90
HISTORY_CHARS = 6000
MAX_MESSAGE_CHARS = 2000
MAX_BODY_BYTES = 1_000_000

ALLOWED_EXT = {
    ".html", ".js", ".css", ".png", ".svg", ".ico", ".jpg", ".jpeg", ".webp",
    ".ttf", ".woff", ".woff2", ".webmanifest", ".txt",
}

DEFAULTS = {
    "api_base": "https://api.b.ai/v1",
    "api_key": "",
    "model": "qwen3.8-flash",
    "voice": "zh-CN-XiaoxiaoNeural",
    "robot": True,
    "temperature": 0.8,
    "timeout": 90,
}

EMOTIONS = [
    ("00", "Sleeping"), ("01", "Waking"), ("02", "Idle"), ("03", "Curious"),
    ("04", "Spacing Out"), ("05", "Booting"), ("06", "Dormant"), ("07", "Shake Awake"),
    ("10", "Happy"), ("11", "Puzzled"), ("12", "Down"), ("13", "Surprised"),
    ("14", "Shy"), ("15", "Tired"), ("16", "Focused"), ("17", "Panicked"),
    ("18", "Resigned"), ("19", "Satisfied"), ("20", "Confused"), ("21", "Angry"),
    ("30", "Thinking"), ("31", "Receiving"), ("32", "Busy"), ("33", "Done"),
    ("34", "Error"), ("35", "Listening"), ("36", "Loading"), ("37", "Recalling"),
    ("38", "Refusing"), ("39", "Replying"), ("40", "Searching"), ("41", "Powering Off"),
]
EMOTION_IDS = {e[0] for e in EMOTIONS}

SYSTEM_PROMPT = (
    "You are Ballie, a tiny expressive assistant living inside a cute emotion ball on screen. "
    "Your reply is displayed as a subtitle and read aloud by a text-to-speech voice. "
    "Always answer in Simplified Chinese, 2 short sentences at most, warm and playful, no emoji. "
    "You may use light Markdown (**bold**, `code`, - lists) when it helps. "
    "Pick exactly one emotionId whose expression best matches the feeling of your reply. "
    "Available emotionIds: " + "; ".join("%s %s" % (eid, name) for eid, name in EMOTIONS) + ". "
    "Use lifecycle/agent states when they fit (e.g. 30 Thinking, 34 Error, 38 Refusing, 33 Done). "
    'Respond with STRICT compact JSON only: {"emotionId":"<id>","reply":"<text>"}'
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("emotion-ball")


def _env_flag(name, default=True):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "off", "no")


ACCESS_CODE = (os.environ.get("ACCESS_CODE") or "").strip()
ADMIN_TOKEN = (os.environ.get("ADMIN_TOKEN") or "").strip()
TTS_ENABLED = _env_flag("TTS_ENABLED", True)
RATE_CHAT_PER_MIN = int(os.environ.get("RATE_CHAT_PER_MIN", "0"))
RATE_TTS_PER_MIN = int(os.environ.get("RATE_TTS_PER_MIN", "0"))
RATE_API_PER_MIN = int(os.environ.get("RATE_API_PER_MIN", "0"))
DAILY_LLM_LIMIT = int(os.environ.get("DAILY_LLM_LIMIT", "0"))

_voices_lock = threading.Lock()
_voices_cache = None


def load_config():
    cfg = dict(DEFAULTS)
    path = ROOT / "config.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            log.warning("config.json ignored: %s", exc)
    for env_key, cfg_key in (
        ("B_AI_BASE", "api_base"),
        ("B_AI_KEY", "api_key"),
        ("B_AI_MODEL", "model"),
        ("TTS_VOICE", "voice"),
    ):
        value = (os.environ.get(env_key) or "").strip()
        if value:
            cfg[cfg_key] = value
    return cfg


CONFIG = load_config()


def save_config():
    path = ROOT / "config.json"
    data = {key: CONFIG.get(key, DEFAULTS[key]) for key in DEFAULTS}
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        log.warning("config.json save failed: %s", exc)
        return False


def update_config(payload):
    api_base = payload.get("api_base")
    api_key = payload.get("api_key")
    model = payload.get("model")
    if api_base is not None:
        api_base = str(api_base).strip()
        if not re.match(r"^https?://", api_base):
            raise ValueError("API 地址需要以 http:// 或 https:// 开头")
        CONFIG["api_base"] = api_base.rstrip("/")
    if api_key is not None and str(api_key).strip():
        CONFIG["api_key"] = str(api_key).strip()
    if model is not None:
        model = str(model).strip()
        if not model:
            raise ValueError("模型名不能为空")
        if len(model) > 128:
            raise ValueError("模型名过长")
        CONFIG["model"] = model
    if not save_config():
        raise RuntimeError("写入 config.json 失败(容器部署请改用环境变量)")
    return CONFIG


def get_voices():
    global _voices_cache
    with _voices_lock:
        if _voices_cache is None:
            all_voices = asyncio.run(edge_tts.list_voices())
            english = [v for v in all_voices if str(v.get("Locale", "")).startswith("en-")]
            english.sort(key=lambda v: (v["Locale"], v["ShortName"]))
            _voices_cache = [
                {"name": v["ShortName"], "locale": v["Locale"], "gender": v.get("Gender", "")}
                for v in english
            ]
        return _voices_cache


def parse_reply(content):
    text = (content or "").strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if fence:
        text = fence.group(1).strip()
    obj = None
    try:
        obj = json.loads(text)
    except ValueError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                obj = json.loads(m.group(0))
            except ValueError:
                obj = None
    if isinstance(obj, dict):
        eid = str(obj.get("emotionId") or obj.get("emotion_id") or "").strip()
        reply = str(obj.get("reply") or obj.get("text") or "").strip()
    else:
        eid, reply = "", text
    if eid not in EMOTION_IDS:
        eid = "02"
    if not reply:
        reply = "嗯……我刚刚一时不知道说什么好。"
        eid = "20"
    return eid, reply


def build_messages(message, history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    items = []
    for item in (history or []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            items.append({"role": role, "content": content[:2000]})
    total = 0
    kept = []
    for item in reversed(items):
        length = len(item["content"])
        if kept and total + length > HISTORY_CHARS:
            break
        kept.append(item)
        total += length
    kept.reverse()
    messages.extend(kept)
    messages.append({"role": "user", "content": message})
    return messages


class ReplyExtractor:
    """Incrementally pulls the reply text out of a streaming {"emotionId":..,"reply":".."} JSON."""

    def __init__(self):
        self.buf = ""
        self.pos = None
        self.closed = False
        self.eid = None

    def feed(self, chunk):
        self.buf += chunk
        if self.eid is None:
            m = re.search(r'"emotion_?[Ii]d"\s*:\s*"?\s*(\d{1,3})', self.buf)
            if m:
                self.eid = m.group(1).zfill(2)
        if self.pos is None:
            m = re.search(r'"reply"\s*:\s*"', self.buf)
            if m:
                self.pos = m.end()
        if self.pos is None or self.closed:
            return ""
        pieces = []
        i = self.pos
        n = len(self.buf)
        while i < n:
            c = self.buf[i]
            if c == "\\":
                if i + 1 >= n:
                    break
                e = self.buf[i + 1]
                if e == "u":
                    if i + 6 > n:
                        break
                    try:
                        pieces.append(chr(int(self.buf[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                else:
                    pieces.append({"n": "\n", "t": " ", "r": "", '"': '"', "\\": "\\", "/": "/"}.get(e, e))
                    i += 2
            elif c == '"':
                self.closed = True
                i += 1
                break
            else:
                pieces.append(c)
                i += 1
        self.pos = i
        return "".join(pieces)


def open_chat_stream(message, history, api_base=None, api_key=None, api_model=None):
    key = (api_key or CONFIG["api_key"] or "").strip()
    base = (api_base or CONFIG["api_base"]).rstrip("/")
    model = (api_model or CONFIG["model"]).strip()
    if not key:
        raise RuntimeError("no API key: 请在配置页填写你的 API Key(或由站点管理员配置内置 Key)")

    body = json.dumps({
        "model": model,
        "messages": build_messages(message, history),
        "temperature": CONFIG["temperature"],
        "stream": True,
    }).encode("utf-8")

    req = Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
        method="POST",
    )
    try:
        return urlopen(req, timeout=CONFIG["timeout"])
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("LLM HTTP %s: %s" % (exc.code, detail))
    except URLError as exc:
        raise RuntimeError("LLM unreachable: %s" % exc.reason)


def chat_completion(message, history, api_base=None, api_key=None, api_model=None):
    key = (api_key or CONFIG["api_key"] or "").strip()
    base = (api_base or CONFIG["api_base"]).rstrip("/")
    model = (api_model or CONFIG["model"]).strip()
    if not key:
        raise RuntimeError("no API key: 请在配置页填写你的 API Key(或由站点管理员配置内置 Key)")
    body = json.dumps({
        "model": model,
        "messages": build_messages(message, history),
        "temperature": CONFIG["temperature"],
    }).encode("utf-8")
    req = Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=CONFIG["timeout"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("LLM HTTP %s: %s" % (exc.code, detail))
    except URLError as exc:
        raise RuntimeError("LLM unreachable: %s" % exc.reason)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("unexpected LLM response: %s" % json.dumps(data)[:300])
    return parse_reply(content)


def fetch_model_list(api_base, api_key):
    base = (api_base or CONFIG["api_base"]).strip().rstrip("/")
    if not re.match(r"^https?://", base):
        raise ValueError("API 地址需要以 http:// 或 https:// 开头")
    key = (api_key or "").strip() or CONFIG["api_key"]
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = Request(base + "/models", headers=headers, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError("模型列表 HTTP %s: %s" % (exc.code, detail))
    except URLError as exc:
        raise RuntimeError("无法连接: %s" % exc.reason)

    items = []

    def take(item):
        if isinstance(item, str) and item:
            items.append(item)
        elif isinstance(item, dict):
            mid = item.get("id") or item.get("name") or item.get("model")
            if mid:
                items.append(str(mid))

    seq = data
    if isinstance(data, dict):
        seq = data.get("data") or data.get("models") or []
    if isinstance(seq, list):
        for item in seq:
            take(item)
    seen = set()
    result = []
    for mid in items:
        if mid not in seen:
            seen.add(mid)
            result.append(mid)
    return result[:500]


# ---------------------------------------------------------------- 限流与鉴权

_rate_lock = threading.Lock()
_rate_hits = {}
_daily_lock = threading.Lock()
_daily = {"day": None, "count": 0}


def rate_ok(bucket, key, limit, window=60):
    if limit <= 0:
        return True
    now = time.time()
    with _rate_lock:
        dq = _rate_hits.setdefault((bucket, key), deque())
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def daily_llm_ok():
    if DAILY_LLM_LIMIT <= 0:
        return True
    with _daily_lock:
        today = date.today().isoformat()
        if _daily["day"] != today:
            _daily["day"] = today
            _daily["count"] = 0
        if _daily["count"] >= DAILY_LLM_LIMIT:
            return False
        _daily["count"] += 1
        return True


def client_ip():
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    remote = request.remote_addr or "?"
    if xff and (remote.startswith(("127.", "10.", "172.", "192.168.")) or remote in ("::1",)):
        return xff
    return remote


def request_api_creds():
    base = (request.headers.get("X-API-Base") or "").strip()
    key = (request.headers.get("X-API-Key") or "").strip()
    model = (request.headers.get("X-API-Model") or "").strip()
    if len(base) > 300:
        base = ""
    if len(key) > 300:
        key = ""
    if len(model) > 128:
        model = ""
    return base, key, model


def is_admin():
    if ADMIN_TOKEN and hmac.compare_digest(request.headers.get("X-Admin-Token", ""), ADMIN_TOKEN):
        return True
    if request.headers.get("X-Forwarded-For"):
        return False
    return client_ip() in ("127.0.0.1", "::1")


def code_ok():
    if not ACCESS_CODE:
        return True
    if is_admin():
        return True
    return hmac.compare_digest(request.headers.get("X-Access-Code", ""), ACCESS_CODE)


def api_error(status, message):
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


def guard(code=True, rate_bucket=None, rate_limit=None, daily=False):
    """Returns a (response, None) tuple when the request must stop, or (None, ip) to continue."""
    ip = client_ip()
    if rate_bucket and not rate_ok(rate_bucket, ip, rate_limit or RATE_API_PER_MIN):
        return api_error(429, "请求太频繁,请稍后再试"), ""
    if code and not code_ok():
        return api_error(401, "需要访问码"), ""
    if daily and not daily_llm_ok():
        return api_error(429, "今天的体验额度已用完,明天再来吧"), ""
    return None, ip


# ---------------------------------------------------------------- Flask 应用

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
app.json.ensure_ascii = False

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",
}


@app.after_request
def apply_headers(resp):
    for key, value in SECURITY_HEADERS.items():
        resp.headers.setdefault(key, value)
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.errorhandler(404)
def not_found(_):
    return api_error(404, "not found")


@app.errorhandler(413)
def too_large(_):
    return api_error(413, "请求体过大")


@app.errorhandler(500)
def server_error(exc):
    log.exception("internal error: %s", exc)
    return api_error(500, "internal error")


@app.get("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "model": CONFIG["model"],
        "voice": CONFIG["voice"],
        "robot": bool(CONFIG.get("robot", True)),
        "key": bool(CONFIG["api_key"]),
        "auth_required": bool(ACCESS_CODE),
        "tts": bool(TTS_ENABLED),
        "admin": is_admin(),
    })


@app.get("/api/auth")
def api_auth():
    denied, _ = guard(code=True, rate_bucket="api")
    if denied:
        return denied
    return jsonify({"ok": True, "admin": is_admin()})


@app.get("/api/config")
def api_config_get():
    if not is_admin():
        return api_error(403, "仅管理员可查看配置")
    return jsonify({
        "api_base": CONFIG["api_base"],
        "model": CONFIG["model"],
        "voice": CONFIG["voice"],
        "robot": bool(CONFIG.get("robot", True)),
        "key": bool(CONFIG["api_key"]),
    })


@app.post("/api/config")
def api_config_set():
    if not is_admin():
        return api_error(403, "仅管理员可修改配置")
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return api_error(400, "bad json")
    try:
        update_config(payload)
    except ValueError as exc:
        return api_error(400, str(exc))
    except RuntimeError as exc:
        return api_error(500, str(exc))
    return jsonify({
        "api_base": CONFIG["api_base"],
        "model": CONFIG["model"],
        "voice": CONFIG["voice"],
        "robot": bool(CONFIG.get("robot", True)),
        "key": bool(CONFIG["api_key"]),
    })


@app.post("/api/models")
def api_models():
    payload = request.get_json(silent=True) or {}
    header_base, header_key, _ = request_api_creds()
    base = str(payload.get("api_base") or header_base or "").strip()
    key = str(payload.get("api_key") or header_key or "").strip()
    admin = is_admin()
    if not admin and (not base or not key):
        return api_error(403, "拉取模型列表需要填写你自己的 API 地址和 Key")
    denied, _ = guard(code=not admin, rate_bucket="api", rate_limit=RATE_API_PER_MIN)
    if denied:
        return denied
    if base and not re.match(r"^https?://", base):
        return api_error(400, "API 地址需要以 http:// 或 https:// 开头")
    try:
        models = fetch_model_list(base, key)
    except ValueError as exc:
        return api_error(400, str(exc))
    except RuntimeError as exc:
        return api_error(502, str(exc))
    return jsonify({"models": models})


@app.post("/api/chat")
def api_chat():
    base, key, model = request_api_creds()
    if base and not re.match(r"^https?://", base):
        return api_error(400, "API 地址需要以 http:// 或 https:// 开头")
    denied, _ = guard(code=True, rate_bucket="chat", rate_limit=RATE_CHAT_PER_MIN,
                      daily=not key)
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return api_error(400, "message is empty")
    try:
        eid, reply = chat_completion(message[:MAX_MESSAGE_CHARS], payload.get("history"),
                                     base or None, key or None, model or None)
    except Exception as exc:
        log.warning("chat failed: %s", exc)
        return api_error(502, str(exc))
    return jsonify({"emotionId": eid, "reply": reply})


@app.post("/api/chat_stream")
def api_chat_stream():
    base, key, model = request_api_creds()
    if base and not re.match(r"^https?://", base):
        return api_error(400, "API 地址需要以 http:// 或 https:// 开头")
    denied, _ = guard(code=True, rate_bucket="chat", rate_limit=RATE_CHAT_PER_MIN,
                      daily=not key)
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return api_error(400, "message is empty")
    history = payload.get("history")

    def generate():
        try:
            upstream = open_chat_stream(message[:MAX_MESSAGE_CHARS], history,
                                        base or None, key or None, model or None)
        except Exception as exc:
            log.warning("stream open failed: %s", exc)
            yield "data: " + json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n\n"
            return

        extractor = ReplyExtractor()
        raw_all = []
        pending_text = []
        sent_emotion = False

        def sse(obj):
            return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

        def emit_emotion(eid):
            nonlocal sent_emotion
            if sent_emotion:
                return ""
            sent_emotion = True
            out = sse({"type": "emotion", "id": eid if eid in EMOTION_IDS else "02"})
            if pending_text:
                out += sse({"type": "text", "delta": "".join(pending_text)})
                pending_text.clear()
            return out

        try:
            for raw_line in upstream:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                think = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if think:
                    yield sse({"type": "thinking", "delta": think})
                text = delta.get("content") or ""
                if not text:
                    continue
                raw_all.append(text)
                piece = extractor.feed(text)
                if extractor.eid and not sent_emotion:
                    out = emit_emotion(extractor.eid)
                    if out:
                        yield out
                if piece:
                    if sent_emotion:
                        yield sse({"type": "text", "delta": piece})
                    else:
                        pending_text.append(piece)

            if extractor.pos is None:
                eid, reply = parse_reply("".join(raw_all))
                out = emit_emotion(eid)
                if out:
                    yield out
                if reply:
                    yield sse({"type": "text", "delta": reply})
            elif not sent_emotion:
                out = emit_emotion(extractor.eid or "02")
                if out:
                    yield out
            yield sse({"type": "done"})
        except (BrokenPipeError, ConnectionResetError, GeneratorExit):
            pass
        except Exception as exc:
            log.warning("stream error: %s", exc)
            try:
                yield sse({"type": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/tts")
def api_tts():
    if not TTS_ENABLED:
        return api_error(403, "语音功能未开放")
    denied, _ = guard(code=True, rate_bucket="tts", rate_limit=RATE_TTS_PER_MIN)
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    voice = str(payload.get("voice") or "").strip() or CONFIG["voice"]
    if not text:
        return api_error(400, "text is empty")
    if len(text) > MAX_TTS_CHARS:
        return api_error(400, "text too long (max %d chars)" % MAX_TTS_CHARS)
    if not re.fullmatch(r"[A-Za-z0-9\-]{1,64}", voice):
        return api_error(400, "bad voice")

    chunks = queue.Queue()

    def produce():
        async def run():
            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.put(("data", chunk["data"]))

        try:
            asyncio.run(run())
            chunks.put(("end", None))
        except Exception as exc:
            chunks.put(("error", str(exc)))

    threading.Thread(target=produce, daemon=True).start()
    try:
        kind, value = chunks.get(timeout=FIRST_CHUNK_TIMEOUT)
    except queue.Empty:
        return api_error(504, "edge-tts timed out")
    if kind == "error":
        log.warning("tts failed: %s", value)
        return api_error(502, str(value))
    if kind != "data":
        return api_error(502, "edge-tts returned no audio")
    first = value

    def generate():
        try:
            yield first
            while True:
                kind, value = chunks.get(timeout=NEXT_CHUNK_TIMEOUT)
                if kind != "data":
                    return
                yield value
        except queue.Empty:
            log.warning("tts stream timeout")
        except (BrokenPipeError, ConnectionResetError, GeneratorExit):
            pass

    return Response(
        generate(),
        mimetype="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/")
def index():
    return serve_static("agora-demo.html")


@app.get("/<path:path>")
def serve_static(path):
    safe = unquote(path)
    parts = Path(safe).parts
    if not parts or any(p.startswith(".") or p == ".." for p in parts):
        return api_error(404, "not found")
    if Path(safe).suffix.lower() not in ALLOWED_EXT:
        return api_error(404, "not found")
    target = (ROOT / safe).resolve()
    if ROOT not in target.parents or not target.is_file():
        return api_error(404, "not found")
    resp = send_file(target, conditional=True)
    ext = target.suffix.lower()
    if ext == ".webmanifest":
        ctype = "application/manifest+json"
    else:
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
    resp.headers["Content-Type"] = ctype
    resp.headers["Cache-Control"] = "no-store"
    return resp


def main():
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("PORT", "8600"))
    if not (ROOT / "agora-demo.html").exists():
        print("agora-demo.html is missing next to server.py")
        sys.exit(1)
    if not ACCESS_CODE:
        log.warning("ACCESS_CODE 未设置:任何人都可以直接调用接口(仅适合本地使用)")
    if not CONFIG["api_key"]:
        log.warning("未配置 API key:在 config.json 或环境变量 B_AI_KEY 中设置")

    from waitress import serve as waitress_serve

    url = "http://%s:%d/" % ("127.0.0.1" if host in ("0.0.0.0", "::") else host, port)
    log.info("emotion ball -> %s", url)
    log.info("model: %s | voice: %s | key: %s | access code: %s",
             CONFIG["model"], CONFIG["voice"],
             "loaded" if CONFIG["api_key"] else "MISSING",
             "on" if ACCESS_CODE else "off")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        waitress_serve(app, host=host, port=port, threads=8, clear_untrusted_proxy_headers=False)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
