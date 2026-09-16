"""Qball MCP(Model Context Protocol)客户端 —— 零依赖、stdio 传输。

复用生态:任何 MCP 服务器(文件系统/浏览器/数据库/搜索…)都能变成 Qball 工具。
配置文件 ~/.qball/mcp.json:
{
  "servers": {
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\data"],
      "env": {},
      "enabled": true,
      "auto_approve": ["read_file", "list_directory"]
    }
  }
}

工具以 mcp.<server>.<tool> 暴露;默认需要用户批准(可用 auto_approve 放行单个工具,
或 <server> 级 auto_approve: true 放行全部)。
协议:JSON-RPC 2.0 over stdio(按行分隔);握手 initialize → notifications/initialized;
tools/list 分页拉取;tools/call 执行。仅 stdio(HTTP/SSE 传输暂不支持)。
"""
import atexit
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = {"creationflags": 0x08000000} if IS_WINDOWS else {}
PROTOCOL_VERSION = "2025-06-18"   # 广泛兼容的握手版本
INIT_TIMEOUT = 30
CALL_TIMEOUT = 90
SPECS_TTL = 30

_lock = threading.RLock()
_servers = {}                     # name -> _Server
_specs_cache = {"tools": [], "when": 0.0, "errors": {}}
_atexit_done = False


def config_path(state_dir=None):
    return Path(state_dir or (Path.home() / ".qball")) / "mcp.json"


def load_config(state_dir=None):
    path = config_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    servers = data.get("servers")
    if not isinstance(servers, dict):
        return {}
    out = {}
    for name, conf in servers.items():
        safe = re.sub(r"[^0-9A-Za-z_-]", "", str(name))[:24]
        if safe and isinstance(conf, dict) and conf.get("enabled", True):
            out[safe] = conf
    return out


def _tool_short_name(tool_name):
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(tool_name))[:48]


class _Server:
    def __init__(self, name, conf):
        self.name = name
        self.conf = conf
        self.proc = None
        self.queue = None
        self.tools = []
        self.error = ""
        self.started_at = 0.0
        self.rpc_id = 0
        self.lock = threading.Lock()

    # -------------------------------------------------- 进程与协议
    def _cmd(self):
        cmd = str(self.conf.get("command") or "").strip()
        args = [str(a) for a in (self.conf.get("args") or [])]
        if not cmd:
            raise ValueError("缺少 command")
        if IS_WINDOWS and (re.search(r"\.(cmd|bat)$", cmd, re.I)
                           or cmd.lower() in ("npx", "npm", "pnpm", "yarn", "uvx")):
            return ["cmd", "/c", cmd] + args
        return [cmd] + args

    def start(self):
        env = dict(os.environ)
        for k, v in (self.conf.get("env") or {}).items():
            env[str(k)] = str(v)
        cwd = self.conf.get("cwd")
        self.proc = subprocess.Popen(
            self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, cwd=(str(cwd) if cwd else None),
            **CREATE_NO_WINDOW)
        self.queue = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "qball", "version": "0.3"},
        }, timeout=INIT_TIMEOUT)
        self._notify("notifications/initialized", {})
        self.tools = self._list_tools()
        self.started_at = time.time()
        self.error = ""

    def _reader(self):
        try:
            for raw in self.proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    self.queue.put(json.loads(line))
                except ValueError:
                    continue
        except Exception:  # noqa: BLE001
            pass
        self.queue.put({"__eof__": True})

    def _write(self, obj):
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _notify(self, method, params):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _rpc(self, method, params, timeout=CALL_TIMEOUT):
        self.rpc_id += 1
        rid = self.rpc_id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError("MCP %s %s 超时(%ds)" % (self.name, method, timeout))
            try:
                msg = self.queue.get(timeout=remain)
            except queue.Empty:
                raise TimeoutError("MCP %s %s 超时(%ds)" % (self.name, method, timeout))
            if msg.get("__eof__"):
                raise RuntimeError("MCP %s 进程已退出" % self.name)
            if msg.get("id") != rid:
                continue  # 通知或其他响应,忽略
            if "error" in msg:
                err = msg["error"]
                raise RuntimeError("MCP %s 错误: %s" % (self.name, err.get("message") or err))
            return msg.get("result") or {}

    def _list_tools(self):
        tools = []
        cursor = None
        for _ in range(5):
            params = {"cursor": cursor} if cursor else {}
            result = self._rpc("tools/list", params, timeout=INIT_TIMEOUT)
            for t in (result.get("tools") or []):
                if isinstance(t, dict) and t.get("name"):
                    tools.append(t)
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    # -------------------------------------------------- 对外
    def call(self, tool_name, args):
        result = self._rpc("tools/call", {"name": tool_name, "arguments": args or {}})
        parts = []
        for item in (result.get("content") or []):
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "resource":
                    parts.append(str((item.get("resource") or {}).get("text") or ""))
        text = "\n".join(p for p in parts if p).strip() or "(无文本输出)"
        if result.get("isError"):
            raise RuntimeError(text)
        return text

    def spec_list(self):
        out = []
        for t in self.tools:
            short = _tool_short_name(t.get("name"))
            schema = t.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            out.append({
                "name": "mcp.%s.%s" % (self.name, short),
                "description": "[MCP:%s] %s" % (self.name, str(t.get("description") or "")[:220]),
                "parameters": schema,
                "_mcp": {"server": self.name, "tool": t.get("name"), "short": short},
            })
        return out


def _ensure(state_dir):
    """按配置确保服务器进程可用(懒启动);返回 {name: _Server}。"""
    global _atexit_done
    confs = load_config(state_dir)
    with _lock:
        for name in list(_servers):
            if name not in confs:
                _kill(_servers.pop(name))
        for name, conf in confs.items():
            srv = _servers.get(name)
            if srv and srv.conf != conf:
                _kill(srv)
                srv = None
                _servers.pop(name, None)
            if srv is None:
                srv = _Server(name, conf)
                srv.error = ""
                try:
                    srv.start()
                except Exception as exc:  # noqa: BLE001
                    srv.error = str(exc)[:200]
                _servers[name] = srv
        if not _atexit_done:
            atexit.register(shutdown)
            _atexit_done = True
        return dict(_servers)


def _kill(srv):
    try:
        if srv.proc and srv.proc.poll() is None:
            srv.proc.kill()
    except Exception:  # noqa: BLE001
        pass


def shutdown():
    with _lock:
        for srv in _servers.values():
            _kill(srv)
        _servers.clear()


def reload_all(state_dir):
    with _lock:
        for srv in _servers.values():
            _kill(srv)
        _servers.clear()
        _specs_cache["when"] = 0.0
    return status(state_dir)


def specs(state_dir=None, force=False):
    """全部 MCP 工具定义(带缓存);失败的服务器记录在 errors 里。"""
    now = time.time()
    if not force and _specs_cache["tools"] and now - _specs_cache["when"] < SPECS_TTL:
        return list(_specs_cache["tools"])
    servers = _ensure(state_dir)
    tools, errors = [], {}
    for name, srv in servers.items():
        if srv.error:
            errors[name] = srv.error
            continue
        try:
            tools.extend(srv.spec_list())
        except Exception as exc:  # noqa: BLE001
            errors[name] = str(exc)[:200]
    _specs_cache.update({"tools": tools, "when": now, "errors": errors})
    return list(tools)


def resolve(state_dir, full_name):
    """mcp.<server>.<short> -> (server, tool, short) 或 None。"""
    m = re.match(r"^mcp\.([0-9A-Za-z_-]+)\.(.+)$", str(full_name or ""))
    if not m:
        return None
    server, short = m.group(1), m.group(2)
    for spec in specs(state_dir):
        info = spec.get("_mcp") or {}
        if info.get("server") == server and info.get("short") == short:
            return info
    return None


def needs_approval(state_dir, full_name):
    info = resolve(state_dir, full_name)
    if not info:
        return True
    conf = load_config(state_dir).get(info["server"]) or {}
    auto = conf.get("auto_approve")
    if auto is True:
        return False
    if isinstance(auto, list):
        names = {str(x) for x in auto}
        if info["tool"] in names or info["short"] in names:
            return False
    return True


def run(state_dir, full_name, args):
    """执行 MCP 工具;进程挂了会自动重启一次。返回 (ok, text)。"""
    info = resolve(state_dir, full_name)
    if not info:
        return False, "MCP 工具不存在(可能服务器离线): %s" % full_name
    servers = _ensure(state_dir)
    srv = servers.get(info["server"])
    if srv is None:
        return False, "MCP 服务器未运行: %s" % info["server"]
    last = ""
    for attempt in (1, 2):
        try:
            if srv.proc is None or srv.proc.poll() is not None:
                raise RuntimeError("进程不在运行")
            return True, srv.call(info["tool"], args)
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:200]
            if attempt == 1:
                try:
                    _kill(srv)
                    srv.start()
                    _specs_cache["when"] = 0.0
                    continue
                except Exception as exc2:  # noqa: BLE001
                    last = "%s;重启失败: %s" % (last, str(exc2)[:120])
                    break
    return False, "MCP 调用失败(%s): %s" % (info["server"], last)


def status(state_dir=None):
    servers = _ensure(state_dir)
    out = []
    for name, srv in servers.items():
        out.append({
            "name": name,
            "alive": bool(srv.proc and srv.proc.poll() is None),
            "tools": len(srv.tools),
            "error": srv.error,
            "auto_approve": (srv.conf.get("auto_approve") is True and "all")
            or (isinstance(srv.conf.get("auto_approve"), list)
                and len(srv.conf.get("auto_approve"))),
        })
    return {"servers": out, "config": str(config_path(state_dir))}
