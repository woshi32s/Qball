"""Qball 自我进化引擎(M1)

设计:
- 独立进程(daemon)运行,状态与产物全部放在 ~/.qball/evolution/
- 执行任务 / 评审 / 反思 / 提案 都通过本机 Qball 服务端(/api/chat_stream、/api/chat)调用,
  以便复用工具链、模型配置与审批通道(自动批准,头 X-Qball-Auto-Approve)
- 一代流程:派活 → 执行(沙盒=工作区) → 评审 → 反思 → 提案 → 静态检查
            → 应用(影子模式则只记录) → 测试门(仅代码改动) → 采纳(本地 commit)/ 回滚
- 引擎自身与 tests/ 属于保护路径,agent 不可修改

用法:
  python evolution.py daemon      # 常驻循环
  python evolution.py once        # 跑一代后退出
  python evolution.py status      # 打印状态
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------- 常量与配置

CONFIG_DEFAULTS = {
    "enabled": False,
    "executor_model": "deepseek-v4.1-flash",
    "judge_model": "deepseek-v4.1-flash",
    "shadow_generations": 20,          # 前 N 代只提案不落地(0 = 直接全自动)
    "cooldown_seconds": 60,
    "allow_code_changes": True,        # 允许修改应用代码(测试门保护)
    "max_find_replace": 200,           # 单次代码改动行数上限
    "max_file_bytes": 12000,           # 提案可读/可写的单文件上限(受消息长度限制)
    "gate_command": "powershell -NoProfile -ExecutionPolicy Bypass -File tests/run_all.ps1 -AppRoot .",
    "gate_timeout_seconds": 1800,
    "server_url": "http://127.0.0.1:8600",
    "allowed_paths": [
        "server.py", "qball_tools.py", "qball.html", "README.md", "README.en.md",
        "requirements.txt", "js/", "css/", "icons/", "fonts/", "assets/",
    ],
    "protected_paths": [
        "tests/", "evolution.py", ".github/", "install.ps1", "qball.ps1", "qball.cmd",
        "Qball.spec", "version.txt", "version.json", "Dockerfile", "docker-compose.yml",
        "Caddyfile", "LICENSE", "LICENSE-COMMERCIAL.md", "scripts/", "DEPLOY.md",
        ".git/", ".venv/", ".gitignore", "start.bat",
    ],
    "scaffold_paths": ["agent/"],
    "app_dir": "",
}

SEED_TASKS = [
    {
        "id": "seed-hello-file",
        "title": "写一个问候文件",
        "prompt": "在工作区根目录创建一个文件 evo-hello.txt,内容包含一行 hi from qball。完成后简短说明。",
        "kind": "verify",
        "verify": {"type": "file_contains", "path": "evo-hello.txt", "text": "hi from qball"},
        "priority": 10,
    },
    {
        "id": "seed-fib-script",
        "title": "斐波那契小脚本",
        "prompt": "在工作区写一个 Python 脚本 fib.py:打印前 10 个斐波那契数(每个一行)。然后用命令运行它确认输出里包含 55。",
        "kind": "verify",
        "verify": {"type": "shell", "command": "python fib.py", "expect_contains": "55"},
        "priority": 20,
    },
    {
        "id": "seed-inventory",
        "title": "文件清单报告",
        "prompt": "把工作区 tests-work 目录下的文件名列表写进工作区的 report.md(一行一个文件)。",
        "kind": "verify",
        "verify": {"type": "file_contains", "path": "report.md", "text": "hello.txt"},
        "priority": 15,
    },
    {
        "id": "seed-self-intro",
        "title": "自我介绍(软任务)",
        "prompt": "用三句话介绍你自己:亲切、简短、让人想继续聊下去。",
        "kind": "soft",
        "rubric": "评分点:亲切自然(35%)、简短不啰嗦(35%)、体现小球的人格(30%)。",
        "priority": 30,
    },
]


# ---------------------------------------------------------------- 基础工具

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = "[%s] %s" % (now(), msg)
    print(line, flush=True)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default if default is not None else {}


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def paths(state_dir):
    root = Path(state_dir) / "evolution"
    return {
        "root": root,
        "config": root / "config.json",
        "state": root / "state.json",
        "tasks": root / "bench" / "tasks",
        "outcomes": root / "outcomes",
        "scores": root / "scores.jsonl",
        "usage": root / "usage.jsonl",
        "log": root / "daemon.log",
        "control": root / "control",
        "agent": root / "agent",
        "pid": root / "daemon.pid",
    }


def load_config(state_dir):
    p = paths(state_dir)
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(_read_json(p["config"], {}))
    return cfg


def save_config(state_dir, cfg):
    _write_json(paths(state_dir)["config"], cfg)


def load_state(state_dir):
    p = paths(state_dir)
    st = _read_json(p["state"], {})
    st.setdefault("generation", 0)
    st.setdefault("adopted", [])
    st.setdefault("last_error", "")
    return st


def save_state(state_dir, st):
    _write_json(paths(state_dir)["state"], st)


def control_flag(state_dir, name):
    return paths(state_dir)["control"] / name


def prompt_addendum(state_dir):
    """把进化产物(附加指令 + 技能库索引)注入系统提示词。"""
    p = paths(state_dir)
    parts = []
    addendum = p["agent"] / "system_prompt_addendum.md"
    if addendum.exists():
        try:
            text = addendum.read_text(encoding="utf-8").strip()[:2000]
        except OSError:
            text = ""
        if text:
            parts.append("【进化附加指令】\n" + text)
    skills = sorted((p["agent"] / "skills").glob("*.md"))[:10]
    lines = []
    for f in skills:
        try:
            first = ""
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip().lstrip("#").strip()
                if line:
                    first = line[:60]
                    break
            if first:
                lines.append("- %s: %s" % (f.stem, first))
        except OSError:
            continue
    if lines:
        parts.append("【技能库】\n" + "\n".join(lines)[:800])
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 初始化

def init_workspace(state_dir):
    p = paths(state_dir)
    for key in ("root", "tasks", "outcomes", "control", "agent"):
        p[key].mkdir(parents=True, exist_ok=True)
    (p["agent"] / "skills").mkdir(parents=True, exist_ok=True)
    (p["agent"] / "memory").mkdir(parents=True, exist_ok=True)

    addendum = p["agent"] / "system_prompt_addendum.md"
    if not addendum.exists():
        addendum.write_text("", encoding="utf-8")
    journal = p["agent"] / "memory" / "journal.md"
    if not journal.exists():
        journal.write_text("# 进化反思日志\n\n", encoding="utf-8")

    if not _read_json(p["config"], None):
        save_config(state_dir, CONFIG_DEFAULTS)

    tasks = bench_tasks(state_dir)
    if not tasks:
        for task in SEED_TASKS:
            save_task(state_dir, task)

    # agent 产物自身的版本库
    if not (p["root"] / ".git").exists():
        try:
            _run(["git", "init", "-q"], cwd=p["root"])
            _run(["git", "-C", str(p["root"]), "config", "user.name", "Qball Evolution"])
            _run(["git", "-C", str(p["root"]), "config", "user.email", "evo@qball.local"])
            git_commit(p["root"], "init evolution workspace")
        except Exception as exc:  # noqa: BLE001
            log("git init failed: %s" % exc)
    return p


def _run(args, cwd=None, timeout=120):
    proc = subprocess.run(args, cwd=str(cwd) if cwd else None,
                          capture_output=True, timeout=timeout)
    out = (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")
    return proc.returncode, out.strip()


def git_commit(repo, message):
    _run(["git", "-C", str(repo), "add", "-A"])
    code, out = _run(["git", "-C", str(repo), "commit", "-q", "-m", message])
    if code != 0 and "nothing to commit" not in out:
        raise RuntimeError("git commit failed: %s" % out[:300])
    code, sha = _run(["git", "-C", str(repo), "rev-parse", "HEAD"])
    return sha.strip()


def git_restore(repo, files, remove_untracked=True):
    for f in files:
        try:
            _run(["git", "-C", str(repo), "checkout", "--", f])
        except Exception:  # noqa: BLE001
            pass
    if remove_untracked:
        for f in files:
            full = Path(repo) / f
            try:
                tracked = _run(["git", "-C", str(repo), "ls-files", "--error-unmatch", f])[0] == 0
                if not tracked and full.exists():
                    full.unlink()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 任务库

def bench_tasks(state_dir):
    p = paths(state_dir)
    tasks = []
    for f in sorted(p["tasks"].glob("*.json")):
        t = _read_json(f, None)
        if isinstance(t, dict) and t.get("id"):
            tasks.append(t)
    return tasks


def save_task(state_dir, task):
    p = paths(state_dir)
    _write_json(p["tasks"] / ("%s.json" % task["id"]), task)


def pick_task(state_dir):
    tasks = bench_tasks(state_dir)
    if not tasks:
        return None
    st = load_state(state_dir)
    last_scores = {}
    for line in read_jsonl(paths(state_dir)["scores"])[-200:]:
        last_scores[line.get("task")] = line.get("score", 0)
    pending = [t for t in tasks if last_scores.get(t["id"], -1) < 1.0]
    if pending:
        pending.sort(key=lambda t: (last_scores.get(t["id"], -1), t.get("priority", 50)))
        return pending[0]
    tasks.sort(key=lambda t: last_scores.get(t["id"], 0))
    return tasks[0]


def read_jsonl(path):
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def append_jsonl(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 服务端调用

class Server:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def chat(self, message, model, timeout=180):
        body = json.dumps({"message": message, "history": []}).encode("utf-8")
        req = urllib.request.Request(self.base + "/api/chat", data=body, method="POST",
                                     headers={
                                         "Content-Type": "application/json",
                                         "X-Qball-Auto-Approve": "1",
                                         "X-API-Model": model,
                                     })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def chat_stream(self, message, model, session_id, timeout=600):
        """执行一次完整 agent 会话,返回 {text, tools:[{name, ok, text}], error}"""
        body = json.dumps({"message": message, "history": []}).encode("utf-8")
        req = urllib.request.Request(self.base + "/api/chat_stream", data=body, method="POST",
                                     headers={
                                         "Content-Type": "application/json",
                                         "X-Qball-Auto-Approve": "1",
                                         "X-API-Model": model,
                                         "X-Qball-Session": session_id,
                                     })
        text_parts, tools, error = [], [], ""
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if ev.get("type") == "text":
                    text_parts.append(ev.get("delta") or "")
                elif ev.get("type") == "tool_call":
                    tools.append({"name": ev.get("name"), "args": ev.get("args"), "ok": None, "text": ""})
                elif ev.get("type") == "tool_result":
                    for t in reversed(tools):
                        if t["name"] == ev.get("name") and t["ok"] is None:
                            t["ok"] = ev.get("ok")
                            t["text"] = (ev.get("text") or "")[:500]
                            break
                elif ev.get("type") == "error":
                    error = ev.get("message") or "error"
        return {"text": "".join(text_parts), "tools": tools, "error": error}


# ---------------------------------------------------------------- 评审

def verify_task(state_dir, task):
    """确定性验收:shell / file_contains。返回 (ok, detail)"""
    v = task.get("verify") or {}
    ws = Path(state_dir) / "workspace"
    kind = v.get("type")
    if kind == "file_contains":
        target = ws / str(v.get("path") or "")
        if not target.is_file():
            return False, "文件不存在: %s" % v.get("path")
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return False, "读取失败: %s" % exc
        return (str(v.get("text")) in content), "文件包含目标内容" if str(v.get("text")) in content else "缺少目标内容"
    if kind == "shell":
        cmd = str(v.get("command") or "")
        try:
            code, out = _run(cmd, cwd=ws, timeout=120)
        except Exception as exc:  # noqa: BLE001
            return False, "命令执行异常: %s" % exc
        expect = v.get("expect_contains")
        ok = (code == 0) if not expect else (expect in out)
        return ok, "输出: " + out[-200:].replace("\n", " ")
    return False, "未知验收类型"


def judge_task(state_dir, cfg, server, task, transcript):
    rubric = task.get("rubric") or "评分点:任务完成度、表达质量、简洁程度。"
    prompt = (
        "【评审】你是严格的评分员。请按评分点给下面这次任务执行打分(0 到 1 之间的小数)。\n"
        "任务: %s\n%s\n执行过程摘要: %s\n最终输出: %s\n"
        '只输出 JSON: {"score": <0-1>, "reason": "<一句话>"}'
        % (task.get("title"), rubric,
           "; ".join("%s(%s)" % (t["name"], "ok" if t["ok"] else "fail") for t in transcript.get("tools", []))[:400],
           (transcript.get("text") or "")[:800])
    )
    try:
        data = server.chat(prompt, cfg["judge_model"])
        reply = str(data.get("reply") or "")
        m = re.search(r"\{[\s\S]*\}", reply)
        if m:
            obj = json.loads(m.group(0))
            score = max(0.0, min(1.0, float(obj.get("score", 0))))
            return score, str(obj.get("reason") or "")[:200]
    except Exception as exc:  # noqa: BLE001
        return 0.0, "评审调用失败: %s" % exc
    return 0.0, "评审输出无法解析"


# ---------------------------------------------------------------- 反思与提案

REFLECT_PROMPT = (
    "【反思】请回顾刚才这次任务:得分 %s(%s)。\n"
    "任务: %s\n输出: %s\n工具使用: %s\n"
    "用 2-4 句中文写一段具体反思:哪里做得好、哪里可以更快更准、下次应该采取什么策略。"
)

PROPOSE_PROMPT = (
    "【提案】你正在改进一个叫 Qball 的桌面 AI 助手。基于这次反思:\n%s\n"
    "当前附加指令(agent/system_prompt_addendum.md)内容:\n%s\n"
    "已有技能文件: %s\n"
    "请给出一代改进提案,只输出 JSON:\n"
    '{"scaffold": {"<agent/ 下的相对路径>": "<完整新内容>"},'
    '"code_target": "<可选:要修改的代码文件相对路径,没有则 null>",'
    '"summary": "<一句话说明>"}\n'
    "约束:scaffold 里只放你认为能提升表现的小改动(比如追加一条经验到 system_prompt_addendum.md 或新增一个技能文件 agent/skills/xx.md);"
    "如果没有把握就给出空 scaffold 并把 code_target 设为 null。"
)

CODE_PROPOSAL_PROMPT = (
    "【代码提案】这是文件 %s 的当前内容:\n----\n%s\n----\n"
    "请给出一个最小化、可验证的改进(修复问题或增强能力),只输出 JSON:\n"
    '{"find": "<原文中一段唯一的片段>", "replace": "<替换后的片段>", "summary": "<一句话>"}\n'
    "要求:find 必须在文件中唯一出现且逐字符一致;改动不超过 %d 行;不要修改测试或安装脚本。"
)


def reflect(state_dir, cfg, server, task, transcript, score, reason):
    prompt = REFLECT_PROMPT % (
        round(score, 2), reason,
        task.get("title"),
        (transcript.get("text") or "")[:600],
        "; ".join("%s(%s)" % (t["name"], "ok" if t["ok"] else "fail") for t in transcript.get("tools", [])) or "无",
    )
    try:
        data = server.chat(prompt, cfg["executor_model"])
        text = str(data.get("reply") or "").strip()
    except Exception as exc:  # noqa: BLE001
        text = "反思调用失败: %s" % exc
    p = paths(state_dir)
    journal = p["agent"] / "memory" / "journal.md"
    try:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write("- [%s] %s\n" % (now(), text.replace("\n", " ")[:500]))
    except OSError:
        pass
    return text


def propose(state_dir, cfg, server, reflection):
    p = paths(state_dir)
    addendum = (p["agent"] / "system_prompt_addendum.md").read_text(encoding="utf-8")[:1500] if (p["agent"] / "system_prompt_addendum.md").exists() else ""
    skills = ", ".join(f.name for f in (p["agent"] / "skills").glob("*.md")) or "无"
    try:
        data = server.chat(PROPOSE_PROMPT % (reflection[:800], addendum, skills), cfg["executor_model"])
        reply = str(data.get("reply") or "")
        m = re.search(r"\{[\s\S]*\}", reply)
        obj = json.loads(m.group(0)) if m else {}
    except Exception as exc:  # noqa: BLE001
        return {"error": "提案调用失败: %s" % exc}
    proposal = {"scaffold": {}, "code_target": None, "summary": str(obj.get("summary") or "")[:200], "raw": {}}
    scaffold = obj.get("scaffold")
    if isinstance(scaffold, dict):
        for rel, content in list(scaffold.items())[:5]:
            rel = str(rel).replace("\\", "/").lstrip("/")
            if rel.startswith("agent/") and isinstance(content, str) and len(content) <= 20000:
                proposal["scaffold"][rel] = content
    target = obj.get("code_target")
    if isinstance(target, str) and target.strip() and cfg.get("allow_code_changes"):
        rel = target.strip().replace("\\", "/").lstrip("/")
        if is_allowed_path(cfg, rel) and (Path(cfg["_app_dir"]) / rel).is_file():
            file_path = Path(cfg["_app_dir"]) / rel
            if file_path.stat().st_size <= cfg.get("max_file_bytes", 120000):
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    data2 = server.chat(
                        CODE_PROPOSAL_PROMPT % (rel, content, cfg.get("max_find_replace", 200)),
                        cfg["executor_model"])
                    reply2 = str(data2.get("reply") or "")
                    m2 = re.search(r"\{[\s\S]*\}", reply2)
                    obj2 = json.loads(m2.group(0)) if m2 else {}
                    find = str(obj2.get("find") or "")
                    replace = str(obj2.get("replace") or "")
                    if find and content.count(find) == 1:
                        changed = replace.count("\n") + 1
                        if changed <= cfg.get("max_find_replace", 200):
                            proposal["code"] = {"file": rel, "find": find, "replace": replace,
                                                "summary": str(obj2.get("summary") or "")[:200]}
                except Exception as exc:  # noqa: BLE001
                    proposal["code_error"] = str(exc)[:200]
    return proposal


def is_allowed_path(cfg, rel):
    rel = str(rel).replace("\\", "/").lstrip("/")
    for bad in cfg.get("protected_paths", []):
        if rel == bad.rstrip("/") or rel.startswith(bad):
            return False
    for good in cfg.get("allowed_paths", []):
        if rel == good.rstrip("/") or rel.startswith(good):
            return True
    return rel.startswith("agent/")


# ---------------------------------------------------------------- 应用与测试门

def apply_proposal(state_dir, cfg, proposal):
    """应用改动,返回 (changed_files, note)。"""
    app = Path(cfg["_app_dir"])
    p = paths(state_dir)
    changed = []
    for rel, content in proposal.get("scaffold", {}).items():
        target = p["root"] / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        changed.append(rel)
    code = proposal.get("code")
    if code:
        target = app / code["file"]
        content = target.read_text(encoding="utf-8", errors="replace")
        if content.count(code["find"]) == 1:
            target.write_text(content.replace(code["find"], code["replace"]), encoding="utf-8")
            changed.append(code["file"])
    return changed


def run_gate(cfg):
    cmd = cfg.get("gate_command") or ""
    if not cmd:
        return True, "无测试门(未配置)"
    shell = cmd if IS_WINDOWS else "bash"
    args = cmd if IS_WINDOWS else ["bash", "-lc", cmd]
    try:
        proc = subprocess.run(args, shell=(shell == cmd), cwd=cfg["_app_dir"],
                              capture_output=True, timeout=cfg.get("gate_timeout_seconds", 1800))
    except subprocess.TimeoutExpired:
        return False, "测试门超时"
    out = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")
    tail = out[-1200:]
    return proc.returncode == 0, tail


# ---------------------------------------------------------------- 一代循环

def run_generation(state_dir, cfg=None, server=None):
    p = init_workspace(state_dir)
    cfg = cfg or load_config(state_dir)
    cfg["_app_dir"] = str(cfg.get("app_dir") or (Path(state_dir) / "app"))
    st = load_state(state_dir)
    server = server or Server(cfg["server_url"])

    st["generation"] = int(st.get("generation", 0)) + 1
    gen = st["generation"]
    gen_dir = p["outcomes"] / ("gen-%04d" % gen)
    gen_dir.mkdir(parents=True, exist_ok=True)
    shadow = gen <= int(cfg.get("shadow_generations", 0))
    result = {"gen": gen, "when": now(), "shadow": shadow, "steps": []}

    def step(name, detail=""):
        result["steps"].append({"name": name, "detail": detail, "t": now()})
        log("gen %d · %s %s" % (gen, name, detail))

    def track(phase, model, text):
        append_jsonl(p["usage"], {"when": now(), "gen": gen, "phase": phase, "model": model,
                                  "est_tokens": max(1, len(text or "") // 3)})

    task = pick_task(state_dir)
    if not task:
        result["error"] = "任务库为空"
        _write_json(gen_dir / "result.json", result)
        save_state(state_dir, st)
        return result
    step("pick-task", task["id"])

    # 1) 执行
    transcript = server.chat_stream(task["prompt"], cfg["executor_model"], "evo-gen-%d" % gen)
    if transcript.get("error") and not transcript.get("text"):
        result["error"] = "执行失败: %s" % transcript["error"]
    track("execute", cfg["executor_model"], transcript.get("text", ""))
    step("execute", "tools=%d text=%d" % (len(transcript.get("tools") or []), len(transcript.get("text") or "")))
    _write_json(gen_dir / "transcript.json", transcript)

    # 2) 评审
    if task.get("kind") == "soft":
        score, reason = judge_task(state_dir, cfg, server, task, transcript)
        track("judge", cfg["judge_model"], reason)
    else:
        ok, detail = verify_task(state_dir, task)
        score, reason = (1.0 if ok else 0.0), detail
    result["score"] = score
    result["reason"] = reason
    step("score", "%.2f %s" % (score, reason))

    # 3) 反思
    reflection = reflect(state_dir, cfg, server, task, transcript, score, reason)
    result["reflection"] = reflection
    track("reflect", cfg["executor_model"], reflection)
    step("reflect", reflection[:60])

    # 4) 提案(可选:得分已满且任务简单时也照常提案,持续迭代)
    proposal = propose(state_dir, cfg, server, reflection)
    result["proposal_summary"] = proposal.get("summary") or proposal.get("error") or ""
    track("propose", cfg["executor_model"], result["proposal_summary"])
    _write_json(gen_dir / "proposal.json", proposal)
    step("propose", result["proposal_summary"][:80])

    changed = []
    if proposal.get("scaffold") or proposal.get("code"):
        if shadow:
            step("shadow", "影子模式:提案仅记录,不落地")
        else:
            changed = apply_proposal(state_dir, cfg, proposal)
            step("apply", ",".join(changed))
            gate_ok, gate_note = True, ""
            if proposal.get("code"):
                gate_ok, gate_note = run_gate(cfg)
                (gen_dir / "gate.log").write_text(gate_note, encoding="utf-8", errors="replace")
                step("gate", "ok" if gate_ok else "fail")
            if gate_ok and changed:
                commits = []
                if proposal.get("scaffold"):
                    try:
                        commits.append(("evo", git_commit(p["root"], "evo(gen %d): %s" % (gen, result["proposal_summary"][:60]))))
                    except Exception as exc:  # noqa: BLE001
                        step("commit-evo-fail", str(exc)[:100])
                if proposal.get("code"):
                    try:
                        commits.append(("app", git_commit(Path(cfg["_app_dir"]), "evo(gen %d): %s" % (gen, result["proposal_summary"][:60]))))
                    except Exception as exc:  # noqa: BLE001
                        step("commit-app-fail", str(exc)[:100])
                st.setdefault("adopted", []).append({"gen": gen, "changed": changed, "commits": commits})
                st["restart_needed"] = bool(proposal.get("code"))
                step("adopt", ",".join(changed))
            elif changed:
                git_restore(p["root"], [f for f in changed if f.startswith("agent/")])
                git_restore(Path(cfg["_app_dir"]), [f for f in changed if not f.startswith("agent/")])
                step("rollback", "测试门未通过,已回滚")
    else:
        step("propose", "无提案")

    append_jsonl(p["scores"], {
        "gen": gen, "when": now(), "task": task["id"], "title": task.get("title"),
        "kind": task.get("kind"), "score": score, "reason": reason,
        "adopted": bool(changed) and not shadow,
    })
    st["last_error"] = result.get("error", "")
    st["last_score"] = score
    st["updated"] = now()
    save_state(state_dir, st)
    _write_json(gen_dir / "result.json", result)
    return result


# ---------------------------------------------------------------- 守护进程

def daemon(state_dir):
    p = init_workspace(state_dir)
    (p["pid"]).write_text(str(os.getpid()), encoding="ascii")
    control_flag(state_dir, ".stop").unlink(missing_ok=True)
    log("daemon started, pid=%d" % os.getpid())
    try:
        while not control_flag(state_dir, ".stop").exists():
            cfg = load_config(state_dir)
            if control_flag(state_dir, ".pause").exists() or not cfg.get("enabled"):
                time.sleep(5)
                continue
            run_once = control_flag(state_dir, ".run_once")
            if run_once.exists():
                run_once.unlink(missing_ok=True)
            try:
                run_generation(state_dir, cfg)
            except Exception as exc:  # noqa: BLE001
                log("generation failed: %s" % exc)
            time.sleep(max(10, int(cfg.get("cooldown_seconds", 60))))
    finally:
        p["pid"].unlink(missing_ok=True)
        log("daemon stopped")


def daemon_pid(state_dir):
    try:
        pid = int(paths(state_dir)["pid"].read_text(encoding="ascii").strip())
        if IS_WINDOWS:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True, timeout=15)
            return pid if str(pid) in out.stdout.decode("utf-8", "replace") else None
        os.kill(pid, 0)
        return pid
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- CLI

def main():
    state_dir = os.environ.get("QBALL_HOME") or str(Path.home() / ".qball")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "daemon":
        daemon(state_dir)
    elif cmd == "once":
        result = run_generation(state_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    elif cmd == "status":
        st = load_state(state_dir)
        cfg = load_config(state_dir)
        pid = daemon_pid(state_dir)
        print(json.dumps({
            "enabled": cfg.get("enabled"), "running": pid is not None, "pid": pid,
            "generation": st.get("generation"), "last_score": st.get("last_score"),
            "shadow_generations": cfg.get("shadow_generations"),
            "models": {"executor": cfg.get("executor_model"), "judge": cfg.get("judge_model")},
        }, ensure_ascii=False, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
