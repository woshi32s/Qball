"""Qball · 生产版服务端(Flask)

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
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

import qball_tools
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

try:
    import edge_tts
except ImportError:
    print("edge-tts is not installed. Run:  pip install -r requirements.txt")
    sys.exit(1)

from flask import Flask, Response, jsonify, request, send_file

VERSION = "0.3.0"
APP_ID = "qball"

if getattr(sys, "frozen", False):
    ASSETS = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    APP_HOME = Path(sys.executable).resolve().parent
else:
    ASSETS = Path(__file__).resolve().parent
    APP_HOME = Path(__file__).resolve().parent

STATE = Path(os.environ.get("QBALL_HOME") or (Path.home() / ".qball"))
LOG_DIR = STATE / "logs"
START_TIME = time.time()


def config_path():
    """打包运行时使用 ~/.qball/config.json;源码模式优先复用状态目录配置(与安装版一致),
    否则退回项目内 config.json(纯开发/容器)。"""
    state_cfg = STATE / "config.json"
    if getattr(sys, "frozen", False):
        return state_cfg
    if state_cfg.exists():
        return state_cfg
    return APP_HOME / "config.json"

MAX_TTS_CHARS = 1500
FIRST_CHUNK_TIMEOUT = 30
NEXT_CHUNK_TIMEOUT = 90
HISTORY_CHARS = 6000
MAX_MESSAGE_CHARS = 2000
MAX_MESSAGE_CHARS_LOCAL = 24000
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
    "tools_enabled": True,
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
    "You are Qball, a tiny expressive assistant living inside a cute expressive ball on screen. "
    "Your reply is displayed as a subtitle and read aloud by a text-to-speech voice. "
    "Chit-chat stays short (1-2 sentences); but when the user gives you a task, or you used tools, "
    "deliver a clear result in as few words as needed: what you did, and the outcome. "
    "Files you produce are shown to the user as clickable cards below your reply automatically — "
    "just mention the file name briefly, never paste long contents. "
    "Honesty rule: never claim something is created/saved/finished unless a tool actually did it; "
    "if it failed, say exactly what failed. "
    "Always answer in Simplified Chinese, warm and playful, no emoji. "
    "You may use light Markdown (**bold**, `code`, - lists) when it helps. "
    "Pick exactly one emotionId whose expression best matches the feeling of your reply. "
    "Available emotionIds: " + "; ".join("%s %s" % (eid, name) for eid, name in EMOTIONS) + ". "
    "Use lifecycle/agent states when they fit (e.g. 30 Thinking, 34 Error, 38 Refusing, 33 Done). "
    'Respond with STRICT compact JSON only: {"emotionId":"<id>","reply":"<text>"}'
)

def setup_logging():
    handlers = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            LOG_DIR / "qball.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers or [logging.NullHandler()],
    )


setup_logging()
log = logging.getLogger("qball")


def ensure_state():
    """准备状态目录;打包运行时把旧版放在 exe 旁的 config.json 迁移进来。"""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "version.txt").write_text(VERSION, encoding="utf-8")
    except OSError as exc:
        log.warning("state dir unavailable: %s", exc)
        return
    if getattr(sys, "frozen", False):
        target = STATE / "config.json"
        legacy = APP_HOME / "config.json"
        if not target.exists() and legacy.exists():
            try:
                shutil.copyfile(legacy, target)
                log.info("migrated legacy config -> %s", target)
            except OSError as exc:
                log.warning("config migration failed: %s", exc)


ensure_state()
qball_tools.configure(STATE)

try:
    import evolution as evolution_engine
except Exception:  # noqa: BLE001
    evolution_engine = None

USER_AGENT = "Qball/0.2 (+https://github.com/woshi32s/Qball)"
CREATE_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
IS_WINDOWS = sys.platform == "win32"

MAX_TOOL_ROUNDS = 8
_PENDING_APPROVALS = {}
_APPROVAL_LOCK = threading.Lock()


def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _wait_approval(approval_id, timeout=180):
    entry = {"event": threading.Event(), "allow": False}
    with _APPROVAL_LOCK:
        _PENDING_APPROVALS[approval_id] = entry
    try:
        entry["event"].wait(timeout)
        return bool(entry["allow"])
    finally:
        with _APPROVAL_LOCK:
            _PENDING_APPROVALS.pop(approval_id, None)


def _tools_unsupported(message):
    low = (message or "").lower()
    return ("tool" in low or "function" in low) and any(
        code in low for code in ("400", "404", "422", "invalid", "unsupported", "unknown"))


SESSION_DIR = STATE / "data" / "sessions"


def _session_file(session_id):
    safe = re.sub(r"[^0-9A-Za-z._-]", "", session_id or "")[:64]
    if not safe:
        return None
    return SESSION_DIR / (safe + ".jsonl")


def record_session(session_id, records):
    path = _session_file(session_id)
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for rec in records:
                item = dict(rec)
                item.setdefault("t", time.time())
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("session write failed: %s", exc)


# ------------------------------------------------ 会话级防呆 / 待办 / 检查点 / 规则

MODIFY_TOOLS = ("fs.write", "fs.edit", "fs.mkdir", "shell.run")
PLAN_TOOLS = {"time.now", "fs.list", "fs.read", "web.fetch", "web.search",
              "notes.add", "notes.list", "notes.read", "notes.search", "todo.write"}
COMPACT_KEEP = 12
_SESS_LOCK = threading.Lock()
_SESS_FILES = {}   # sid -> {rel: {"mtime": float, "size": int}}


def _ws_rel(rel):
    rel = str(rel or "").strip().replace("\\", "/").lstrip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or rel == "." or ".." in rel:
        return ""
    return rel


def _file_state(rel):
    try:
        st = (Path(qball_tools.workspace()) / rel).stat()
        return {"mtime": st.st_mtime, "size": st.st_size}
    except OSError:
        return None


def session_write_guard(session_id, name, args):
    """写/编辑前的防呆:本会话读过但磁盘已变 → 拒绝;fs.edit 需本会话读过。"""
    if not session_id:
        return ""
    rel = _ws_rel((args or {}).get("path"))
    if not rel:
        return ""
    with _SESS_LOCK:
        known = (_SESS_FILES.get(session_id) or {}).get(rel)
    disk = _file_state(rel)
    if name == "fs.edit":
        if known is None and disk is not None:
            return "fs.edit 前请先用 fs.read 读一下这个文件(本会话未读过,防误改)"
        if known and disk and (disk["mtime"] != known["mtime"] or disk["size"] != known["size"]):
            return "文件在本次会话读取后已被其他改动修改,请先重新 fs.read 再编辑(防覆盖)"
    if name == "fs.write" and known and disk and (
            disk["mtime"] != known["mtime"] or disk["size"] != known["size"]):
        return "文件在本次会话读取后已被其他改动修改,已阻止覆盖;请先重新 fs.read 确认内容"
    return ""


def session_note_tool(session_id, name, args, ok):
    if not session_id or not ok:
        return
    rel = _ws_rel((args or {}).get("path"))
    if not rel or name not in ("fs.read", "fs.write", "fs.edit"):
        return
    disk = _file_state(rel)
    if disk is None:
        return
    with _SESS_LOCK:
        _SESS_FILES.setdefault(session_id, {})[rel] = disk


def session_todos_text(session_id):
    if not session_id or qball_tools is None:
        return ""
    items = qball_tools.session_todos(session_id)
    if not items:
        return ""
    mark = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    lines = ["【当前待办(用 todo.write 维护)】"]
    for t in items[:12]:
        lines.append("- %s %s" % (mark.get(t.get("status") or "pending", "[ ]"),
                                  str(t.get("content") or "")[:80]))
    return "\n".join(lines)


def session_msg_count(session_id):
    path = _session_file(session_id)
    if not path or not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                   if line.strip())
    except OSError:
        return 0


def load_project_rules():
    """分层加载项目规则:全局 ~/.qball/QBALL.md + 工作区 QBALL.md / AGENTS.md。"""
    parts = []
    cands = [(STATE / "QBALL.md", "全局"),
             (Path(qball_tools.workspace()) / "QBALL.md", "工作区"),
             (Path(qball_tools.workspace()) / "AGENTS.md", "工作区 AGENTS")]
    for path, label in cands:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                continue
            parts.append("【项目规则 · %s】\n%s" % (label, text[:2500]))
        except OSError:
            continue
    return "\n\n".join(parts)


CKPT_DIR = STATE / "checkpoints.git"


def _ckpt_git(args, timeout=120):
    return subprocess.run(
        ["git", "--git-dir", str(CKPT_DIR), "--work-tree", str(Path(qball_tools.workspace()))] + args,
        capture_output=True, timeout=timeout, **CREATE_NO_WINDOW)


def ckpt_ensure():
    if CKPT_DIR.exists():
        return True
    try:
        _ckpt_git(["init", "-q"])
        _ckpt_git(["config", "user.name", "Qball Checkpoints"])
        _ckpt_git(["config", "user.email", "ckpt@qball.local"])
        _ckpt_git(["config", "core.autocrlf", "false"])
        return True
    except Exception:  # noqa: BLE001
        return False


def ckpt_snapshot(label):
    """工作区快照(影子 git,独立于任何用户仓库);无变化时返回当前 HEAD。"""
    try:
        if not ckpt_ensure():
            return ""
        _ckpt_git(["add", "-A"])
        diff = _ckpt_git(["diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            head = _ckpt_git(["rev-parse", "HEAD"])
            return head.stdout.decode("utf-8", "replace").strip()
        if _ckpt_git(["commit", "-q", "-m", str(label)[:80]]).returncode != 0:
            return ""
        return _ckpt_git(["rev-parse", "HEAD"]).stdout.decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return ""


def ckpt_restore(commit):
    r = _ckpt_git(["reset", "--hard", commit])
    if r.returncode != 0:
        raise ValueError("还原失败: %s" % r.stderr.decode("utf-8", "replace")[:200])
    _ckpt_git(["clean", "-fd"])
    return True


def quick_completion(messages, base, key, model, timeout=60):
    """一次性的非交互补全(用于历史摘要);失败返回 ''。"""
    parts = []
    try:
        upstream = open_chat_stream(messages, base or None, key or None, model or None)
        for raw in upstream:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
            piece = delta.get("content") or ""
            if piece:
                parts.append(piece)
                if sum(len(p) for p in parts) > 4000:
                    break
        return "".join(parts).strip()
    except Exception:  # noqa: BLE001
        return ""


def _session_summary(session_id):
    path = _session_file(session_id)
    if not path or not path.exists():
        return None
    last = None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("role") == "summary":
                last = rec
    except OSError:
        return None
    return last


def prepare_summary(history, session_id, base, key, model):
    """历史超长时,用模型把最早部分压成摘要(持久化到会话,跨轮复用)。"""
    items = []
    for item in (history or []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            items.append({"role": role, "content": content[:2000]})
    total = sum(len(it["content"]) for it in items)
    if total <= HISTORY_CHARS or len(items) <= COMPACT_KEEP + 2:
        return ""
    cut = len(items) - COMPACT_KEEP
    cached = _session_summary(session_id) if session_id else None
    prev_upto = int((cached or {}).get("upto") or 0)
    prev_text = str((cached or {}).get("summary") or "")
    if cut <= prev_upto:
        return prev_text
    new_part = items[prev_upto:cut]
    if not new_part:
        return prev_text
    body = []
    if prev_text:
        body.append("已有摘要:\n" + prev_text)
    for it in new_part:
        body.append(("用户: " if it["role"] == "user" else "助手: ") + it["content"])
    prompt = ("请把下面的对话压缩成中文要点摘要(不超过 300 字),必须保留:1) 用户目标与关键要求 "
              "2) 已完成的事与结论 3) 未完成的下一步 4) 提到的文件路径。只输出摘要本身。\n\n"
              + "\n\n".join(body))
    summary = quick_completion([{"role": "user", "content": prompt[:12000]}], base, key, model)
    if not summary:
        return prev_text
    if session_id:
        try:
            path = _session_file(session_id)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"role": "summary", "content": summary[:2000],
                                         "upto": cut, "t": time.time()},
                                        ensure_ascii=False) + "\n")
        except OSError:
            pass
    return summary


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
    path = config_path()
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
    path = config_path()
    data = {key: CONFIG.get(key, DEFAULTS[key]) for key in DEFAULTS}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
    if payload.get("tools_enabled") is not None:
        CONFIG["tools_enabled"] = bool(payload.get("tools_enabled"))
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
        action = obj.get("action")
        if not isinstance(action, dict) or not action.get("tool"):
            action = None
    else:
        eid, reply = "", text
        action = None
    if eid not in EMOTION_IDS:
        eid = "02"
    if not reply:
        reply = "嗯……我刚刚一时不知道说什么好。"
        eid = "20"
    return eid, reply, action


def build_messages(message, history, tools_hint=None, system=None,
                   session_id=None, prefix_summary=None):
    if system:
        base_system = system
    else:
        base_system = SYSTEM_PROMPT
        try:
            rules = load_project_rules()
        except Exception:  # noqa: BLE001
            rules = ""
        if rules:
            base_system = base_system + "\n\n" + rules
        if evolution_engine is not None:
            try:
                hint = evolution_engine.prompt_addendum(STATE)
            except Exception:  # noqa: BLE001
                hint = ""
            if hint:
                base_system = base_system + "\n\n" + hint
        try:
            todos = session_todos_text(session_id)
        except Exception:  # noqa: BLE001
            todos = ""
        if todos:
            base_system = base_system + "\n\n" + todos
        if tools_hint:
            base_system = base_system + "\n\n" + tools_hint
    messages = [{"role": "system", "content": base_system}]
    if prefix_summary:
        messages.append({"role": "system",
                         "content": "【更早对话的摘要(系统自动压缩,内容仍然有效)】\n" + str(prefix_summary)[:3000]})
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


def open_chat_stream(messages, api_base=None, api_key=None, api_model=None, tools=None, session_id=None):
    key = (api_key or CONFIG["api_key"] or "").strip()
    base = (api_base or CONFIG["api_base"]).rstrip("/")
    model = (api_model or CONFIG["model"]).strip()
    if not key:
        raise RuntimeError("no API key: 请在配置页填写你的 API Key(或由站点管理员配置内置 Key)")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": CONFIG["temperature"],
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    body = json.dumps(payload).encode("utf-8")

    req = Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "x-opencode-session": session_id or uuid.uuid4().hex,
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


def chat_completion(message, history, api_base=None, api_key=None, api_model=None, session_id=None, system=None):
    key = (api_key or CONFIG["api_key"] or "").strip()
    base = (api_base or CONFIG["api_base"]).rstrip("/")
    model = (api_model or CONFIG["model"]).strip()
    if not key:
        raise RuntimeError("no API key: 请在配置页填写你的 API Key(或由站点管理员配置内置 Key)")
    body = json.dumps({
        "model": model,
        "messages": build_messages(message, history, system=system),
        "temperature": CONFIG["temperature"],
    }).encode("utf-8")
    req = Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "x-opencode-session": session_id or uuid.uuid4().hex,
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
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("unexpected LLM response: %s" % json.dumps(data)[:300])
    if system:
        return "02", str(content).strip()
    eid, reply, _action = parse_reply(content)
    return eid, reply


def fetch_model_list(api_base, api_key):
    base = (api_base or CONFIG["api_base"]).strip().rstrip("/")
    if not re.match(r"^https?://", base):
        raise ValueError("API 地址需要以 http:// 或 https:// 开头")
    key = (api_key or "").strip() or CONFIG["api_key"]
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
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
        "app": APP_ID,
        "version": VERSION,
        "mode": "exe" if getattr(sys, "frozen", False) else "source",
        "uptime": int(time.time() - START_TIME),
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


@app.post("/api/shutdown")
def api_shutdown():
    if not is_admin():
        return api_error(403, "仅本机可退出")
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


TASK_NAME = "Qball"
STARTUP_LNK = "Qball.lnk"


def _autostart_supported():
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def _shortcut_path():
    import ctypes

    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 7, None, 0, buf)  # CSIDL_STARTUP
    return Path(buf.value) / STARTUP_LNK


def _task_exists():
    try:
        res = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                             capture_output=True, timeout=15, **CREATE_NO_WINDOW)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _autostart_enabled():
    if _task_exists():
        return True
    try:
        return _shortcut_path().exists()
    except OSError:
        return False


def set_autostart(enabled):
    """开机自启:优先计划任务,失败回退到启动文件夹快捷方式。"""
    exe = str(Path(sys.executable).resolve())
    if enabled:
        try:
            res = subprocess.run(
                ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "ONLOGON",
                 "/TR", '"%s" --no-browser' % exe],
                capture_output=True, timeout=30, **CREATE_NO_WINDOW)
            if res.returncode == 0:
                return "task"
        except (OSError, subprocess.SubprocessError):
            pass
        script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            "$sc = $ws.CreateShortcut('%s'); "
            "$sc.TargetPath = '%s'; $sc.Arguments = '--no-browser'; "
            "$sc.WorkingDirectory = '%s'; $sc.Save()"
        ) % (str(_shortcut_path()), exe, str(Path(exe).parent))
        res = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, timeout=30, **CREATE_NO_WINDOW)
        if res.returncode != 0:
            raise RuntimeError("无法创建开机自启(计划任务与启动文件夹均失败)")
        return "startup"
    else:
        try:
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                           capture_output=True, timeout=15, **CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
        shortcut = _shortcut_path()
        if shortcut.exists():
            try:
                shortcut.unlink()
            except OSError:
                pass
        return "off"


@app.get("/api/autostart")
def api_autostart_get():
    if not is_admin():
        return api_error(403, "仅本机可查看")
    supported = _autostart_supported()
    return jsonify({"supported": supported, "enabled": _autostart_enabled() if supported else False})


@app.post("/api/autostart")
def api_autostart_set():
    if not is_admin():
        return api_error(403, "仅本机可修改")
    if not _autostart_supported():
        return api_error(400, "开机自启仅支持打包后的 Windows 版本")
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled"))
    try:
        set_autostart(enabled)
    except RuntimeError as exc:
        return api_error(500, str(exc))
    return jsonify({"supported": True, "enabled": _autostart_enabled() if enabled else False})


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
        "tools_enabled": bool(CONFIG.get("tools_enabled", True)),
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
        "tools_enabled": bool(CONFIG.get("tools_enabled", True)),
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
    limit = MAX_MESSAGE_CHARS_LOCAL if is_admin() else MAX_MESSAGE_CHARS
    message = message[:limit]
    system_override = ""
    if is_admin():
        system_override = str(payload.get("system") or "").strip()[:4000]
    try:
        session_id = (request.headers.get("X-Qball-Session") or "").strip()[:64] or None
        eid, reply = chat_completion(message, payload.get("history"),
                                     base or None, key or None, model or None, session_id,
                                     system=system_override or None)
    except Exception as exc:
        log.warning("chat failed: %s", exc)
        return api_error(502, str(exc))
    if session_id:
        record_session(session_id, [
            {"role": "user", "content": message},
            {"role": "assistant", "content": reply, "emotionId": eid},
        ])
    return jsonify({"emotionId": eid, "reply": reply})


@app.get("/api/sessions")
def api_sessions():
    if not is_admin():
        return api_error(403, "仅本机可查看")
    out = []
    if SESSION_DIR.exists():
        try:
            files = sorted(SESSION_DIR.glob("*.jsonl"),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:50]
        except OSError:
            files = []
        for path in files:
            try:
                count = 0
                first_user = ""
                last = ""
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    count += 1
                    last = str(rec.get("content") or "")[:60]
                    if not first_user and rec.get("role") == "user":
                        first_user = str(rec.get("content") or "")[:60]
                out.append({
                    "id": path.stem,
                    "updated": int(path.stat().st_mtime),
                    "messages": count,
                    "preview": first_user or last,
                })
            except OSError:
                continue
    return jsonify({"sessions": out})


@app.get("/api/sessions/<sid>")
def api_session_detail(sid):
    if not is_admin():
        return api_error(403, "仅本机可查看")
    path = _session_file(sid)
    if not path or not path.exists():
        return api_error(404, "会话不存在")
    msgs = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            role = rec.get("role")
            content = str(rec.get("content") or "")
            if role in ("user", "assistant") and content:
                item = {"role": role, "content": content}
                if rec.get("emotionId"):
                    item["emotionId"] = rec.get("emotionId")
                if rec.get("tools"):
                    item["tools"] = rec.get("tools")
                if rec.get("deliverables"):
                    item["deliverables"] = rec.get("deliverables")
                if rec.get("checkpoint"):
                    item["checkpoint"] = rec.get("checkpoint")
                msgs.append(item)
    except OSError:
        return api_error(500, "读取失败")
    return jsonify({"id": sid, "messages": msgs})


# ---------------------------------------------------------------- 自我进化

def spawn_evolution_daemon():
    """启动进化守护进程(源码模式专属),返回 (pid, error)。"""
    if evolution_engine is None:
        return None, "evolution 模块不可用"
    pid = evolution_engine.daemon_pid(STATE)
    if pid:
        return pid, ""
    if getattr(sys, "frozen", False):
        return None, "自我进化需要源码模式:先运行 qball dev-setup"
    script = Path(APP_HOME) / "evolution.py"
    if not script.exists():
        return None, "源码目录缺少 evolution.py"
    env = dict(os.environ)
    env["QBALL_HOME"] = str(STATE)
    kwargs = {"cwd": str(APP_HOME), "env": env,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([sys.executable, str(script), "daemon"], **kwargs)
    return proc.pid, ""


@app.get("/api/evolution/status")
def api_evolution_status():
    if not is_admin():
        return api_error(403, "仅本机可查看")
    if evolution_engine is None:
        return jsonify({"available": False})
    try:
        cfg = evolution_engine.load_config(STATE)
        st = evolution_engine.load_state(STATE)
        pid = evolution_engine.daemon_pid(STATE)
        scores = evolution_engine.read_jsonl(evolution_engine.paths(STATE)["scores"])[-10:]
        usage = evolution_engine.read_jsonl(evolution_engine.paths(STATE)["usage"])
        today = time.strftime("%Y-%m-%d")
        calls_today = sum(1 for u in usage if str(u.get("when", "")).startswith(today))
        return jsonify({
            "available": True,
            "enabled": bool(cfg.get("enabled")),
            "running": pid is not None,
            "paused": evolution_engine.control_flag(STATE, ".pause").exists(),
            "generation": st.get("generation", 0),
            "last_score": st.get("last_score"),
            "last_error": st.get("last_error", ""),
            "shadow_generations": cfg.get("shadow_generations"),
            "shadow_remaining": max(0, int(cfg.get("shadow_generations", 0)) - int(st.get("generation", 0))),
            "executor_model": cfg.get("executor_model"),
            "judge_model": cfg.get("judge_model"),
            "source_mode": not getattr(sys, "frozen", False),
            "app_dir": str(cfg.get("app_dir") or (STATE / "app")),
            "calls_today": calls_today,
            "recent": scores,
            "adopted": st.get("adopted", [])[-5:],
            "last_restart": st.get("last_restart", ""),
            "notify_desktop": bool(cfg.get("notify_desktop", True)),
        })
    except Exception as exc:  # noqa: BLE001
        return api_error(500, str(exc))


@app.get("/api/evolution/library")
def api_evolution_library():
    if not is_admin():
        return api_error(403, "仅本机可查看")
    if evolution_engine is None:
        return jsonify({"available": False})

    def _text(path, cap):
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:cap]
        except OSError:
            return ""

    try:
        p = evolution_engine.paths(STATE)
        skills = []
        for f in sorted((p["agent"] / "skills").glob("*.md")):
            text = _text(f, 4000)
            first = ""
            for line in text.splitlines():
                line = line.strip().lstrip("#").strip()
                if line:
                    first = line[:80]
                    break
            try:
                mtime = time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            except OSError:
                mtime = ""
            skills.append({"name": f.stem, "chars": len(text), "first": first, "mtime": mtime})
        st = evolution_engine.load_state(STATE)
        return jsonify({
            "available": True,
            "generation": st.get("generation", 0),
            "adoptions": len(st.get("adopted") or []),
            "skills": skills,
            "addendum": _text(p["agent"] / "system_prompt_addendum.md", 2400),
            "journal": _text(p["agent"] / "memory" / "journal.md", 3000),
            "ideas": _text(p["root"] / "reports" / "ux-ideas.md", 16000),
        })
    except Exception as exc:  # noqa: BLE001
        return api_error(500, str(exc))


@app.post("/api/evolution/control")
def api_evolution_control():
    if not is_admin():
        return api_error(403, "仅本机可操作")
    if evolution_engine is None:
        return api_error(503, "evolution 模块不可用")
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip()
    cfg = evolution_engine.load_config(STATE)
    try:
        if action == "enable":
            cfg["enabled"] = True
            evolution_engine.save_config(STATE, cfg)
            evolution_engine.control_flag(STATE, ".stop").unlink(missing_ok=True)
            pid, err = spawn_evolution_daemon()
            if err:
                return api_error(400, err)
            return jsonify({"ok": True, "pid": pid})
        if action == "disable":
            cfg["enabled"] = False
            evolution_engine.save_config(STATE, cfg)
            evolution_engine.control_flag(STATE, ".stop").touch()
            return jsonify({"ok": True})
        if action == "pause":
            evolution_engine.control_flag(STATE, ".pause").touch()
            return jsonify({"ok": True})
        if action == "resume":
            evolution_engine.control_flag(STATE, ".pause").unlink(missing_ok=True)
            return jsonify({"ok": True})
        if action == "set_notify":
            cfg["notify_desktop"] = bool(payload.get("value", True))
            evolution_engine.save_config(STATE, cfg)
            return jsonify({"ok": True, "notify_desktop": cfg["notify_desktop"]})
        if action == "run_once":
            evolution_engine.control_flag(STATE, ".run_once").touch()
            if not evolution_engine.daemon_pid(STATE):
                pid, err = spawn_evolution_daemon()
                if err:
                    return api_error(400, err)
            return jsonify({"ok": True})
        if action == "run_once_direct":
            result = evolution_engine.run_generation(STATE)
            return jsonify({"ok": True, "result": {
                k: result.get(k) for k in ("gen", "score", "shadow", "proposal_summary", "reflection", "consolidate", "error")}})
        if action == "set_models":
            for key in ("executor_model", "judge_model"):
                value = str(payload.get(key) or "").strip()
                if value:
                    cfg[key] = value[:128]
            evolution_engine.save_config(STATE, cfg)
            return jsonify({"ok": True})
        if action == "set_shadow":
            cfg["shadow_generations"] = max(0, int(payload.get("value") or 0))
            evolution_engine.save_config(STATE, cfg)
            return jsonify({"ok": True})
        if action == "revert":
            ok, msg = evolution_engine.revert_last(STATE)
            if not ok:
                return api_error(400, msg)
            return jsonify({"ok": True, "message": msg})
    except Exception as exc:  # noqa: BLE001
        return api_error(500, str(exc))
    return api_error(400, "未知动作")


@app.get("/api/tools")
def api_tools():
    if not is_admin():
        return api_error(403, "仅本机可查看")
    return jsonify({
        "enabled": bool(CONFIG.get("tools_enabled", True)),
        "workspace": str(qball_tools.workspace()),
        "tools": qball_tools.all_specs(),
    })


@app.post("/api/tools/run")
def api_tools_run():
    if not is_admin():
        return api_error(403, "仅本机可运行")
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    if not name:
        return api_error(400, "name is empty")
    if qball_tools.needs_approval(name):
        return api_error(403, "该工具需要用户批准,请在对话中触发")
    ok, text = qball_tools.run(name, args)
    return jsonify({"ok": ok, "text": text[:4000]})


@app.post("/api/approve")
def api_approve():
    if not is_admin():
        return api_error(403, "仅本机可操作")
    payload = request.get_json(silent=True) or {}
    approval_id = str(payload.get("id") or "")
    with _APPROVAL_LOCK:
        entry = _PENDING_APPROVALS.get(approval_id)
    if not entry:
        return api_error(404, "审批请求不存在或已过期")
    entry["allow"] = bool(payload.get("allow"))
    entry["event"].set()
    return jsonify({"ok": True})


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
    use_tools = bool(CONFIG.get("tools_enabled", True)) and not payload.get("no_tools")
    session_id = (request.headers.get("X-Qball-Session") or "").strip()[:64] or None
    auto_approve = request.headers.get("X-Qball-Auto-Approve") == "1" and is_admin()
    limit = MAX_MESSAGE_CHARS_LOCAL if is_admin() else MAX_MESSAGE_CHARS
    msg_text = message[:limit]
    plan_mode = str(payload.get("mode") or "").strip().lower() == "plan"

    def generate():
        native = use_tools
        text_hint = qball_tools.prompt_section() if use_tools else None
        try:
            summary_text = prepare_summary(history, session_id, base, key, model)
        except Exception:  # noqa: BLE001
            summary_text = ""
        messages = build_messages(msg_text, history,
                                  tools_hint=None if native else text_hint,
                                  session_id=session_id, prefix_summary=summary_text)
        if plan_mode:
            messages[0]["content"] += (
                "\n\n【计划模式】当前是只读计划档:只允许查看与调研,禁止修改文件或执行命令。"
                "请先给出清晰的分步计划(编号列表,每步一句话,涉及文件写出路径),"
                "如有必要用 todo.write 建待办;结尾询问用户是否开始执行。")
        state = {"eid": "02", "text": [], "tools": [], "deliverables": []}
        turn_ckpt = {"commit": "", "msg_index": session_msg_count(session_id)}
        rounds = 0

        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            tool_defs = qball_tools.all_specs() if (use_tools and native) else None
            if tool_defs and plan_mode:
                tool_defs = [s for s in tool_defs if s.get("name") in PLAN_TOOLS]
            try:
                upstream = open_chat_stream(messages, base or None, key or None, model or None,
                                            tools=tool_defs, session_id=session_id)
            except Exception as exc:
                err = str(exc)
                if tool_defs and _tools_unsupported(err):
                    native = False
                    messages = build_messages(msg_text, history, tools_hint=text_hint,
                                              session_id=session_id, prefix_summary=summary_text)
                    continue
                log.warning("stream open failed: %s", exc)
                yield _sse({"type": "error", "message": err})
                return

            extractor = ReplyExtractor()
            raw_all = []
            pending_text = []
            sent_emotion = False
            tool_calls = {}

            def emit_emotion(eid):
                nonlocal sent_emotion
                if sent_emotion:
                    return ""
                sent_emotion = True
                state["eid"] = eid if eid in EMOTION_IDS else "02"
                out = _sse({"type": "emotion", "id": state["eid"]})
                if pending_text:
                    text = "".join(pending_text)
                    state["text"].append(text)
                    out += _sse({"type": "text", "delta": text})
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
                        yield _sse({"type": "thinking", "delta": think})
                    for tc in delta.get("tool_calls") or []:
                        if not isinstance(tc, dict):
                            continue
                        index = tc.get("index", 0)
                        entry = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            entry["id"] = str(tc["id"])
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            entry["name"] = str(fn["name"])
                        if fn.get("arguments"):
                            entry["arguments"] += str(fn["arguments"])
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
                            state["text"].append(piece)
                            yield _sse({"type": "text", "delta": piece})
                        else:
                            pending_text.append(piece)
            except (BrokenPipeError, ConnectionResetError, GeneratorExit):
                return
            except Exception as exc:
                log.warning("stream error: %s", exc)
                yield _sse({"type": "error", "message": str(exc)})
                return
            finally:
                try:
                    upstream.close()
                except Exception:
                    pass

            raw_text = "".join(raw_all)
            eid, reply, action = "", "", None
            if raw_text.strip():
                eid, reply, action = parse_reply(raw_text)
            if extractor.pos is None:
                if eid or reply:
                    out = emit_emotion(eid or "30")
                    if out:
                        yield out
                    if reply:
                        state["text"].append(reply)
                        yield _sse({"type": "text", "delta": reply})
            elif not sent_emotion:
                out = emit_emotion(extractor.eid or "02")
                if out:
                    yield out

            calls = []
            if use_tools:
                if tool_calls:
                    for index in sorted(tool_calls):
                        entry = tool_calls[index]
                        if not entry["name"]:
                            continue
                        try:
                            args = json.loads(entry["arguments"]) if entry["arguments"].strip() else {}
                        except ValueError:
                            args = {}
                        if not isinstance(args, dict):
                            args = {}
                        calls.append({"id": entry["id"] or ("call_%d" % index),
                                      "name": entry["name"], "args": args})
                elif action:
                    args = action.get("args") if isinstance(action.get("args"), dict) else {}
                    calls.append({"id": "text_%d" % rounds, "name": str(action.get("tool")), "args": args})

            if not calls:
                if session_id:
                    assistant = {"role": "assistant", "content": "".join(state["text"])[:8000],
                                 "emotionId": state["eid"]}
                    if state["tools"]:
                        assistant["tools"] = state["tools"]
                    if state["deliverables"]:
                        assistant["deliverables"] = state["deliverables"]
                    if turn_ckpt["commit"]:
                        assistant["checkpoint"] = {"commit": turn_ckpt["commit"],
                                                   "msg_index": turn_ckpt["msg_index"]}
                    record_session(session_id, [
                        {"role": "user", "content": msg_text},
                        assistant,
                    ])
                yield _sse({"type": "done"})
                return

            results = []
            for call in calls:
                state["tools"].append(call["name"])
                yield _sse({"type": "tool_call", "id": call["id"], "name": call["name"], "args": call["args"]})
                if plan_mode and call["name"] not in PLAN_TOOLS:
                    ok, text = False, "计划模式:该工具在只读档被禁用;请在计划里说明需要它做什么"
                    yield _sse({"type": "tool_result", "id": call["id"], "name": call["name"],
                                "ok": ok, "text": text})
                    results.append((call, text))
                    continue
                if session_id and call["name"] in MODIFY_TOOLS and not turn_ckpt["commit"]:
                    ck = ckpt_snapshot("turn %s · %s" % (session_id[:24], call["name"]))
                    if ck:
                        turn_ckpt["commit"] = ck
                        yield _sse({"type": "checkpoint", "id": ck})
                qball_tools.set_session(session_id)
                guard = ""
                if call["name"] in ("fs.write", "fs.edit"):
                    guard = session_write_guard(session_id, call["name"], call["args"])
                if guard:
                    ok, text = False, guard
                elif qball_tools.needs_approval(call["name"]) and not auto_approve:
                    approval_id = uuid.uuid4().hex[:12]
                    yield _sse({"type": "approval_required", "id": approval_id,
                                "name": call["name"], "args": call["args"]})
                    allowed = _wait_approval(approval_id)
                    if allowed:
                        ok, text = qball_tools.run(call["name"], call["args"])
                    else:
                        ok, text = False, "用户拒绝了这次操作"
                else:
                    ok, text = qball_tools.run(call["name"], call["args"])
                session_note_tool(session_id, call["name"], call["args"], ok)
                if ok and call["name"] == "todo.write" and session_id:
                    yield _sse({"type": "todos", "items": qball_tools.session_todos(session_id)})
                yield _sse({"type": "tool_result", "id": call["id"], "name": call["name"],
                            "ok": ok, "text": text[:4000]})
                results.append((call, text))
                # 产物收集:成功写入的文件自动成为"交付物卡片"(不依赖模型自觉)
                if ok and call["name"] == "fs.write":
                    rel = str((call["args"] or {}).get("path") or "").strip().replace("\\", "/")
                    rel = rel.lstrip("/").lstrip("./")
                    if (rel and ".." not in rel and len(state["deliverables"]) < 12
                            and all(d["path"] != rel for d in state["deliverables"])):
                        item = {"path": rel,
                                "chars": len(str((call["args"] or {}).get("content") or ""))}
                        state["deliverables"].append(item)
                        yield _sse({"type": "deliverable", "path": rel, "chars": item["chars"]})

            if native:
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": call["id"], "type": "function",
                        "function": {"name": call["name"],
                                     "arguments": json.dumps(call["args"], ensure_ascii=False)},
                    } for call, _text in results],
                })
                for call, text in results:
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": text[:8000]})
            else:
                messages.append({"role": "assistant", "content": raw_text[:4000]})
                for call, text in results:
                    messages.append({"role": "user",
                                     "content": "[工具结果 %s]\n%s" % (call["name"], text[:8000])})

        if session_id:
            assistant = {"role": "assistant", "content": "".join(state["text"])[:8000],
                         "emotionId": state["eid"]}
            if state["tools"]:
                assistant["tools"] = state["tools"]
            if state["deliverables"]:
                assistant["deliverables"] = state["deliverables"]
            if turn_ckpt["commit"]:
                assistant["checkpoint"] = {"commit": turn_ckpt["commit"],
                                           "msg_index": turn_ckpt["msg_index"]}
            record_session(session_id, [
                {"role": "user", "content": msg_text},
                assistant,
            ])
        yield _sse({"type": "error", "message": "工具调用次数到达上限,先停一下"})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/open")
def api_open():
    """打开/定位工作区内的产物(仅本机)。action: open | reveal | path"""
    if not is_admin():
        return api_error(403, "仅本机可用")
    payload = request.get_json(silent=True) or {}
    rel = str(payload.get("path") or "").strip().replace("\\", "/")
    action = str(payload.get("action") or "open").strip().lower()
    ws = Path(qball_tools.workspace()).resolve()
    target = ws if rel in ("", ".") else (ws / rel).resolve()
    try:
        target.relative_to(ws)
    except ValueError:
        return api_error(400, "路径必须在工作区内")
    if not target.exists():
        return api_error(404, "文件不存在")
    if action == "path":
        return jsonify({"ok": True, "path": str(target), "name": target.name})
    try:
        if IS_WINDOWS:
            if action == "reveal":
                subprocess.Popen(["explorer.exe", "/select," + str(target)], **CREATE_NO_WINDOW)
            else:
                os.startfile(str(target))  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(target if action == "open" else target.parent)])
        return jsonify({"ok": True, "path": str(target)})
    except Exception as exc:  # noqa: BLE001
        return api_error(500, str(exc))


@app.get("/api/checkpoints")
def api_checkpoints():
    """最近的工作区快照(影子 git),仅本机。"""
    if not is_admin():
        return api_error(403, "仅本机可查看")
    items = []
    try:
        if CKPT_DIR.exists():
            r = _ckpt_git(["log", "--pretty=format:%H|%ad|%s", "--date=format:%m-%d %H:%M", "-30"])
            for line in r.stdout.decode("utf-8", "replace").splitlines():
                cols = line.split("|", 2)
                if len(cols) == 3:
                    items.append({"commit": cols[0], "time": cols[1], "label": cols[2]})
    except Exception as exc:  # noqa: BLE001
        return api_error(500, str(exc))
    return jsonify({"checkpoints": items})


@app.post("/api/checkpoints/restore")
def api_checkpoints_restore():
    """把工作区还原到某个快照;scope=both 时同时裁掉该轮之后的对话。"""
    if not is_admin():
        return api_error(403, "仅本机可操作")
    payload = request.get_json(silent=True) or {}
    commit = str(payload.get("commit") or "").strip()
    scope = "both" if str(payload.get("scope") or "") == "both" else "files"
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        return api_error(400, "commit 不合法")
    try:
        if not CKPT_DIR.exists():
            return api_error(400, "还没有任何检查点")
        ver = _ckpt_git(["rev-parse", "--verify", commit + "^{commit}"])
        if ver.returncode != 0:
            return api_error(400, "找不到该检查点")
        ckpt_restore(commit)
        if scope == "both":
            sid = str(payload.get("session") or "").strip()[:64]
            try:
                keep = int(payload.get("msg_index"))
            except (TypeError, ValueError):
                keep = -1
            path = _session_file(sid) if sid else None
            if path and path.exists() and keep >= 0:
                lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
                         if l.strip()]
                path.write_text("\n".join(lines[:keep]) + "\n", encoding="utf-8")
        return jsonify({"ok": True, "scope": scope})
    except Exception as exc:  # noqa: BLE001
        return api_error(500, str(exc))


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
    return serve_static("qball.html")


@app.get("/<path:path>")
def serve_static(path):
    safe = unquote(path)
    parts = Path(safe).parts
    if not parts or any(p.startswith(".") or p == ".." for p in parts):
        return api_error(404, "not found")
    if Path(safe).suffix.lower() not in ALLOWED_EXT:
        return api_error(404, "not found")
    target = (ASSETS / safe).resolve()
    if ASSETS not in target.parents or not target.is_file():
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


def read_saved_port():
    try:
        value = int((STATE / "port.txt").read_text(encoding="utf-8").strip())
        return value if 1 <= value <= 65535 else None
    except (OSError, ValueError):
        return None


def save_port(port):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "port.txt").write_text(str(port), encoding="utf-8")
    except OSError:
        pass


def running_port():
    """检测本机是否已有 Qball 实例在跑,返回其端口。"""
    ports = []
    saved = read_saved_port()
    if saved:
        ports.append(saved)
    ports.extend(p for p in range(8600, 8611) if p not in ports)
    for port in ports:
        try:
            with urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("app") == APP_ID:
                return port
        except Exception:
            continue
    return None


def pick_port():
    """优先复用上次端口,被占用则向后顺延(8600-8610)。"""
    saved = read_saved_port()
    order = ([saved] if saved else []) + [p for p in range(8600, 8611) if p != saved]
    for port in order:
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    return 8600


def main():
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    explicit_port = os.environ.get("PORT")
    if not (ASSETS / "qball.html").exists():
        print("qball.html is missing next to the executable")
        sys.exit(1)

    if explicit_port:
        port = int(explicit_port)
    else:
        already = running_port()
        if already:
            url = "http://127.0.0.1:%d/" % already
            log.info("Qball is already running -> %s", url)
            if "--no-browser" not in sys.argv:
                threading.Timer(0.3, lambda: webbrowser.open(url)).start()
            return
        port = pick_port()
        save_port(port)

    if not ACCESS_CODE:
        log.warning("ACCESS_CODE 未设置:任何人都可以直接调用接口(仅适合本机使用)")
    if not CONFIG["api_key"]:
        log.warning("未配置 API key:在 %s 或环境变量 B_AI_KEY 中设置", config_path())
    if evolution_engine is not None:
        try:
            _evo_cfg = evolution_engine.load_config(STATE)
            if _evo_cfg.get("enabled") and not evolution_engine.daemon_pid(STATE):
                _pid, _err = spawn_evolution_daemon()
                if _err:
                    log.warning("evolution daemon: %s", _err)
                else:
                    log.info("evolution daemon started (pid %s)", _pid)
        except Exception as exc:  # noqa: BLE001
            log.warning("evolution autostart failed: %s", exc)

    from waitress import serve as waitress_serve

    url = "http://%s:%d/" % ("127.0.0.1" if host in ("0.0.0.0", "::") else host, port)
    log.info("Qball v%s -> %s", VERSION, url)
    log.info("state dir: %s", STATE)
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
