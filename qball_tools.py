"""Qball 工具集(v1):文件、命令、网页、笔记、时间。

约定:
- 文件类工具只能在工作区内操作(STATE/workspace),越界拒绝
- shell.run 默认需要用户批准(由 server 处理审批事件)
- 所有结果都是文本,超长截断
"""
import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

STATE_DIR = Path.home() / ".qball"


def configure(state_dir):
    global STATE_DIR
    STATE_DIR = Path(state_dir)


def workspace() -> Path:
    ws = STATE_DIR / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def notes_dir() -> Path:
    nd = STATE_DIR / "notes"
    nd.mkdir(parents=True, exist_ok=True)
    return nd


# ------------------------------------------------------------------ 工具实现

_MAX_READ = 200_000
_MAX_OUTPUT = 40_000


def _safe_path(rel: str) -> Path:
    ws = workspace().resolve()
    target = (ws / (rel or ".")).resolve()
    if target != ws and ws not in target.parents:
        raise ValueError("路径超出工作区范围: %s" % rel)
    return target


def t_now(_args):
    return time.strftime("%Y-%m-%d %H:%M:%S %A", time.localtime())


def t_fs_list(args):
    target = _safe_path(str(args.get("path") or "."))
    if not target.exists():
        return "路径不存在: %s" % str(args.get("path") or ".")
    if target.is_file():
        return "这是一个文件: %s" % target.name
    items = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.is_dir():
            items.append(child.name + "/")
        else:
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            items.append("%s  (%d 字节)" % (child.name, size))
    if not items:
        return "(空目录)"
    return "\n".join(items[:200])


def t_fs_read(args):
    target = _safe_path(str(args.get("path") or ""))
    if not target.is_file():
        return "文件不存在: %s" % str(args.get("path"))
    data = target.read_bytes()[:_MAX_READ]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace")


def t_fs_write(args):
    target = _safe_path(str(args.get("path") or ""))
    content = str(args.get("content") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return "已写入 %s(%d 字符)" % (target.relative_to(workspace()), len(content))


def t_fs_mkdir(args):
    target = _safe_path(str(args.get("path") or ""))
    target.mkdir(parents=True, exist_ok=True)
    return "已创建目录 %s" % target.relative_to(workspace())


def t_shell_run(args):
    cmd = str(args.get("command") or "").strip()
    if not cmd:
        return "命令为空"
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(workspace()),
            capture_output=True, timeout=120, **_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return "命令超时(120 秒)已终止: %s" % cmd
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    text = out + ("\n[stderr]\n" + err if err.strip() else "")
    text = text.strip() or "(无输出)"
    if len(text) > _MAX_OUTPUT:
        text = text[:_MAX_OUTPUT] + "\n…(截断)"
    return "退出码 %d\n%s" % (proc.returncode, text)


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Qball/0.2")


def _http_get(url: str, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def t_web_fetch(args):
    url = str(args.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        return "URL 需要以 http:// 或 https:// 开头"
    try:
        data, ctype = _http_get(url)
    except Exception as exc:
        # 直连失败时尝试 jina reader 兜底
        try:
            data, _ = _http_get("https://r.jina.ai/" + url)
            ctype = "text/plain"
        except Exception:
            return "抓取失败: %s" % exc
    text = data.decode("utf-8", "replace")
    if "html" in ctype.lower():
        text = _strip_html(text)
    if len(text) > _MAX_OUTPUT:
        text = text[:_MAX_OUTPUT] + "\n…(截断)"
    return text or "(页面无文本内容)"


def t_web_search(args):
    query = str(args.get("query") or "").strip()
    if not query:
        return "搜索词为空"
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query) + "&count=8"
    try:
        data, _ = _http_get(url)
    except Exception as exc:
        return "搜索失败(网络受限?): %s" % exc
    page = data.decode("utf-8", "replace")
    results = []
    # 逐条匹配结果块
    for m in re.finditer(r'<li class="b_algo".*?</li>', page, re.S):
        block = m.group(0)
        tm = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not tm:
            continue
        link = html.unescape(tm.group(1))
        title = _strip_html(tm.group(2))
        sm = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        snippet = _strip_html(sm.group(1)) if sm else ""
        results.append({"title": title, "url": link, "snippet": snippet[:300]})
        if len(results) >= 6:
            break
    if not results:
        text = _strip_html(page)
        return ("未解析到结构化结果,页面正文摘要:\n" + text[:1500]) if text else "没有搜索结果"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append("%d. %s\n   %s\n   %s" % (i, r["title"], r["url"], r["snippet"]))
    return "\n".join(lines)


def t_notes_add(args):
    text = str(args.get("text") or "").strip()
    if not text:
        return "内容为空"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = notes_dir() / ("%s.md" % stamp)
    path.write_text(text + "\n", encoding="utf-8")
    return "已记录: %s" % path.name


def t_notes_list(_args):
    files = sorted(notes_dir().glob("*.md"), reverse=True)[:50]
    if not files:
        return "(还没有笔记)"
    return "\n".join(f.name for f in files)


def t_notes_read(args):
    name = str(args.get("name") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z\-_.]+\.md", name):
        return "笔记名不合法"
    path = notes_dir() / name
    if not path.is_file():
        return "笔记不存在: %s" % name
    return path.read_text(encoding="utf-8")[:_MAX_READ]


def t_notes_search(args):
    query = str(args.get("query") or "").strip().lower()
    if not query:
        return "搜索词为空"
    hits = []
    for f in sorted(notes_dir().glob("*.md"), reverse=True):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if query in line.lower():
                hits.append("%s:%d  %s" % (f.name, i, line.strip()[:160]))
                break
        if len(hits) >= 20:
            break
    return "\n".join(hits) if hits else "没有匹配的笔记"


# ------------------------------------------------------------------ 注册表

def _spec(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


TOOLS = {
    "time.now": {
        "spec": _spec("time.now", "获取当前日期、时间与星期"),
        "fn": t_now, "approval": False,
    },
    "fs.list": {
        "spec": _spec("fs.list", "列出工作区内某个目录的内容(相对路径)",
                      {"path": {"type": "string", "description": "相对工作区的路径,默认 ."}}),
        "fn": t_fs_list, "approval": False,
    },
    "fs.read": {
        "spec": _spec("fs.read", "读取工作区内的文本文件",
                      {"path": {"type": "string", "description": "相对工作区的文件路径"}}, ["path"]),
        "fn": t_fs_read, "approval": False,
    },
    "fs.write": {
        "spec": _spec("fs.write", "把文本写入工作区内的文件(会覆盖)",
                      {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
        "fn": t_fs_write, "approval": False,
    },
    "fs.mkdir": {
        "spec": _spec("fs.mkdir", "在工作区内创建目录",
                      {"path": {"type": "string"}}, ["path"]),
        "fn": t_fs_mkdir, "approval": False,
    },
    "shell.run": {
        "spec": _spec("shell.run", "在工作区目录执行一条系统命令(需要用户批准)",
                      {"command": {"type": "string"}}, ["command"]),
        "fn": t_shell_run, "approval": True,
    },
    "web.fetch": {
        "spec": _spec("web.fetch", "抓取一个网页并提取正文文本",
                      {"url": {"type": "string"}}, ["url"]),
        "fn": t_web_fetch, "approval": False,
    },
    "web.search": {
        "spec": _spec("web.search", "用搜索引擎查资料,返回标题/链接/摘要",
                      {"query": {"type": "string"}}, ["query"]),
        "fn": t_web_search, "approval": False,
    },
    "notes.add": {
        "spec": _spec("notes.add", "把一条信息记进长期笔记(自动带时间戳)",
                      {"text": {"type": "string"}}, ["text"]),
        "fn": t_notes_add, "approval": False,
    },
    "notes.list": {
        "spec": _spec("notes.list", "列出最近的笔记文件"),
        "fn": t_notes_list, "approval": False,
    },
    "notes.read": {
        "spec": _spec("notes.read", "读取某个笔记的内容",
                      {"name": {"type": "string"}}, ["name"]),
        "fn": t_notes_read, "approval": False,
    },
    "notes.search": {
        "spec": _spec("notes.search", "在笔记里搜索关键词",
                      {"query": {"type": "string"}}, ["query"]),
        "fn": t_notes_search, "approval": False,
    },
}


def all_specs():
    return [entry["spec"] for entry in TOOLS.values()]


def needs_approval(name):
    entry = TOOLS.get(name)
    return bool(entry and entry["approval"])


def run(name, args):
    """执行工具,返回 (ok, text)。异常归一为 (False, 错误信息)。"""
    entry = TOOLS.get(name)
    if not entry:
        return False, "未知工具: %s" % name
    if not isinstance(args, dict):
        args = {}
    try:
        text = entry["fn"](args)
        return True, str(text)
    except Exception as exc:
        return False, "工具执行出错: %s" % exc


def prompt_section():
    """给文本协议模型用的工具说明。"""
    lines = ["你可以使用以下工具来完成任务:"]
    for entry in TOOLS.values():
        spec = entry["spec"]
        params = ",".join(spec["parameters"]["properties"].keys()) or "-"
        lines.append("- %s(%s): %s%s" % (spec["name"], params, spec["description"],
                                         "(需用户批准)" if entry["approval"] else ""))
    lines.append('需要调用工具时,在回复 JSON 里增加 action 字段: '
                 '{"emotionId":"30","reply":"我来看看…","action":{"tool":"工具名","args":{...}}}; '
                 '收到「工具结果」后继续推理,最后再给出正常回答(reply 里写真正要说的话)。')
    return "\n".join(lines)
