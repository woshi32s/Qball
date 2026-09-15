"""Qball 自我进化引擎(M1)

核心原则(不可动摇):
- **不训练模型权重**。模型始终是外部 API(用户配置的云端模型,一个字节都不改)。
- 本引擎让模型反复执行真实任务,然后改进 **Agent 本身**:
  行为规则(agent/system_prompt_addendum.md)/ 技能库(agent/skills)/ 记忆(agent/memory)/
  以及(受最严门禁保护的)代码。

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
    "consolidate_addendum_chars": 1500,   # 附加指令超过此长度触发知识合并
    "consolidate_skills": 8,              # 技能超过此数量触发知识合并
    "retire_after_fails": 3,              # 卡题连败次数上限(达到即退休,腾出探索空间)
    "task_library_limit": 30,             # 任务库上限;满时退休最弱的自产任务再补充
    "soft_mastery": 0.85,                 # 软任务的"已掌握"分数线(达到后不再常驻轮换)
    "regression_quiet_scores": 3,         # 连续 N 次高分后,该任务视为回归演练:改动只记录不采纳
    "regression_quiet_min": 0.85,
}

META_SYSTEM = (
    "你是 Qball 自我进化系统的内部工具,不是聊天助手。"
    "严格按用户指令执行:只输出要求的 JSON 或文本,不要寒暄、不要表演人设。"
)

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


_LOG_FILE = None


def log(msg):
    line = "[%s] %s" % (now(), msg)
    print(line, flush=True)
    if _LOG_FILE:
        try:
            path = Path(_LOG_FILE)
            if path.exists() and path.stat().st_size > 1_000_000:
                path.replace(path.with_suffix(".log.old"))
            with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
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

    # agent 产物自身的版本库(只版本化 agent/ 等产出,状态文件不进库,避免回滚冲突)
    if not (p["root"] / ".git").exists():
        gitignore = p["root"] / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "scores.jsonl\nusage.jsonl\nstate.json\nconfig.json\n"
                "daemon.log\ndaemon.pid\ncontrol/\noutcomes/\nlast-task-gen.json\n",
                encoding="utf-8")
        try:
            _run(["git", "init", "-q"], cwd=p["root"])
            _run(["git", "-C", str(p["root"]), "config", "user.name", "Qball Evolution"])
            _run(["git", "-C", str(p["root"]), "config", "user.email", "evo@qball.local"])
            git_commit(p["root"], "init evolution workspace")
        except Exception as exc:  # noqa: BLE001
            log("git init failed: %s" % exc)
    # 维护:把已跟踪但属于运行状态的忽略文件移出版本库(修复历史遗留,幂等)
    try:
        runtime_files = ["scores.jsonl", "usage.jsonl", "state.json", "config.json",
                         "daemon.log", "daemon.pid", "last-task-gen.json"]
        for t in runtime_files:
            _run(["git", "-C", str(p["root"]), "rm", "--cached", "-q", "--ignore-unmatch", "--", t])
        _run(["git", "-C", str(p["root"]), "rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", "control", "outcomes"])
        code, staged = _run(["git", "-C", str(p["root"]), "diff", "--cached", "--name-only"])
        if staged.strip():
            git_commit(p["root"], "chore: untrack runtime state files")
            log("untracked runtime files from evolution repo:\n%s" % staged.strip()[:300])
    except Exception as exc:  # noqa: BLE001
        log("untrack maintenance failed: %s" % exc)
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
    cfg = load_config(state_dir)
    soft_target = float(cfg.get("soft_mastery", 0.85))
    last_scores = {}
    last_kind = {}
    for line in read_jsonl(paths(state_dir)["scores"])[-200:]:
        last_scores[line.get("task")] = line.get("score", 0)
        last_kind[line.get("task")] = line.get("kind")
    fails = st.get("task_fails") or {}
    candidates = [t for t in tasks if int(fails.get(t["id"], 0)) < 3]
    if not candidates:
        # 全部卡住:松绑失败次数最少的一个,给它再一次机会
        weakest = min(tasks, key=lambda t: int(fails.get(t["id"], 0)))
        fails.pop(weakest["id"], None)
        st["task_fails"] = fails
        save_state(state_dir, st)
        candidates = [t for t in tasks if int(fails.get(t["id"], 0)) < 3] or list(tasks)

    def target_of(t):
        kind = t.get("kind") or last_kind.get(t["id"])
        return 1.0 if kind != "soft" else soft_target

    pending = [t for t in candidates if last_scores.get(t["id"], -1) < target_of(t)]
    pool = pending or candidates
    pool.sort(key=lambda t: (last_scores.get(t["id"], -1), t.get("priority", 50)))
    return pool[0]


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

    def chat(self, message, model, timeout=180, system=None):
        payload = {"message": message, "history": []}
        if system:
            payload["system"] = system
        body = json.dumps(payload).encode("utf-8")
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
        data = server.chat(prompt, cfg["judge_model"], system=META_SYSTEM)
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
    '{"scaffold": {"<相对路径,必须以 agent/ 开头,例如 agent/system_prompt_addendum.md 或 agent/skills/xxx.md>": "<完整新内容>"},'
    '"code_target": "<可选:要修改的代码文件相对路径,没有则 null>",'
    '"summary": "<一句话说明>"}\n'
    "约束:scaffold 里只放你认为能提升表现的小改动(比如追加一条经验到 agent/system_prompt_addendum.md 或新增一个技能文件 agent/skills/xx.md);"
    "如果没有把握就给出空 scaffold 并把 code_target 设为 null。"
)

CODE_PROPOSAL_PROMPT = (
    "【代码提案】这是文件 %s 的当前内容:\n----\n%s\n----\n"
    "请给出一个最小化、可验证的改进(修复问题或增强能力),只输出 JSON:\n"
    '{"find": "<原文中一段唯一的片段>", "replace": "<替换后的片段>", "summary": "<一句话>"}\n'
    "要求:find 必须在文件中唯一出现且逐字符一致;改动不超过 %d 行;不要修改测试或安装脚本。"
)

TASK_GEN_PROMPT = (
    "【出题】你是 Qball 进化系统的任务设计师。当前任务库:\n%s\n"
    "请设计一个新任务,用来锻炼 Qball 的实用能力(文件整理 / 写小脚本 / 查资料并总结 / 写作 / 数据收拾等),"
    "难度循序渐进、不要重复上面的任务。只输出 JSON:\n"
    '{"title": "<短标题>", "prompt": "<给执行者的完整中文指令,具体、可独立完成>", '
    '"kind": "verify" 或 "soft", '
    '"verify": {"type": "file_contains", "path": "<工作区内文件>", "text": "<必须包含的文字>"} '
    '或 {"type": "shell", "command": "<工作区内可运行的命令>", "expect_contains": "<输出中应出现的内容>"}, '
    '"rubric": "<kind=soft 时的评分点>"}\n'
    "约束:验收必须能在工作区内自动判定;不要涉及删除文件、下载大文件、系统级操作;prompt 不超过 300 字。"
)

CONSOLIDATE_PROMPT = (
    "【合并】你是 Qball 进化系统的知识管理员。当前积累如下:\n"
    "=== 附加指令(addendum) ===\n%s\n"
    "=== 技能库 ===\n%s\n"
    "请做一次知识整理:合并重复、删除冗余、保留所有真正有用的经验教训,并保持表述精炼。只输出 JSON:\n"
    '{"addendum": "<整理后的完整附加指令内容>", '
    '"skills": {"<技能文件名如 xxx.md>": "<该技能整理后的完整内容>"}, '
    '"summary": "<一句话说明这次整理做了什么>"}\n'
    "约束:addendum 不超过 1200 字;技能总数不超过 8 个(其余会被删除,请把它们的要点并入保留的);"
    "每个技能不超过 2500 字;不要丢失仍然有效的具体操作纪律(如:用 fs.write 落盘、先写后跑、单次验证等)。"
)


def validate_new_task(obj, existing_titles):
    if not isinstance(obj, dict):
        return None, "非法 JSON"
    title = str(obj.get("title") or "").strip()[:60]
    prompt = str(obj.get("prompt") or "").strip()
    kind = str(obj.get("kind") or "").strip()
    if not title or not prompt or len(prompt) > 800:
        return None, "标题或指令不合法"
    if title in existing_titles:
        return None, "任务重复"
    task = {"title": title, "prompt": prompt, "priority": 40, "created_by": "agent"}
    if kind == "soft":
        task["kind"] = "soft"
        task["rubric"] = str(obj.get("rubric") or "评分点:完成度、表达质量、效率。")[:500]
    else:
        v = obj.get("verify") or {}
        vtype = str(v.get("type") or "")
        if vtype == "file_contains":
            path = str(v.get("path") or "").replace("\\", "/").lstrip("/")
            text = str(v.get("text") or "")
            if not path or ".." in path or not text or len(text) > 200:
                return None, "file_contains 验收不合法"
            task["kind"] = "verify"
            task["verify"] = {"type": "file_contains", "path": path, "text": text}
        elif vtype == "shell":
            command = str(v.get("command") or "").strip()[:300]
            if not command:
                return None, "shell 验收不合法"
            if IS_WINDOWS:
                command = re.sub(r"\bpython3(\.\d+)?\b", "python", command)  # Windows 下统一 python
            task["kind"] = "verify"
            task["verify"] = {"type": "shell", "command": command,
                              "expect_contains": str(v.get("expect_contains") or "")[:200]}
        else:
            return None, "未知验收类型"
    return task, ""


def generate_task(state_dir, cfg, server):
    p = paths(state_dir)
    tasks = bench_tasks(state_dir)
    limit = int(cfg.get("task_library_limit", 30))
    if len(tasks) >= limit:
        # 腾位置:退休"自产且最近得分最低"的旧任务(种子任务保留)
        last_scores = {}
        for line in read_jsonl(p["scores"])[-200:]:
            last_scores[line.get("task")] = line.get("score", 0)
        agent_tasks = [t for t in tasks if t.get("created_by") == "agent"]
        if not agent_tasks:
            return None, "任务库已满(无可退休的自产任务)"
        weakest = min(agent_tasks, key=lambda t: (last_scores.get(t["id"], -1), t.get("created_at", "")))
        try:
            retired_dir = p["root"] / "bench" / "retired"
            retired_dir.mkdir(parents=True, exist_ok=True)
            src = p["tasks"] / ("%s.json" % weakest["id"])
            if src.exists():
                src.replace(retired_dir / src.name)
            log("task library full: retired %s (%s)" % (weakest["id"], weakest.get("title", "")))
        except OSError as exc:
            return None, "腾位失败: %s" % exc
        tasks = bench_tasks(state_dir)
    menu = "\n".join("- %s:%s" % (t["title"], (t.get("prompt") or "")[:80]) for t in tasks[-10:]) or "(空)"
    try:
        data = server.chat(TASK_GEN_PROMPT % menu, cfg["executor_model"], system=META_SYSTEM)
        reply = str(data.get("reply") or "")
        m = re.search(r"\{[\s\S]*\}", reply)
        obj = json.loads(m.group(0)) if m else None
    except Exception as exc:  # noqa: BLE001
        return None, "出题调用失败: %s" % exc
    task, err = validate_new_task(obj, {t["title"] for t in tasks})
    if not task:
        try:
            (p["outcomes"] / "last-task-gen.json").write_text(
                json.dumps({"reply": reply[:800], "obj": obj}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return None, "出题校验失败: %s" % err
    task["id"] = "gen-%s" % re.sub(r"[^0-9A-Za-z]+", "-", task["title"])[:24].strip("-").lower()
    if not task["id"] or task["id"] == "gen-":
        task["id"] = "gen-task-%d" % int(time.time())
    task["created_at"] = now()
    save_task(state_dir, task)
    return task, ""


def _snapshot_repo(repo):
    """回滚前快照:把未提交的变动先提交,保证 git revert 有干净工作区。"""
    try:
        code, out = _run(["git", "-C", str(repo), "status", "--porcelain"])
        if out.strip():
            _run(["git", "-C", str(repo), "add", "-A"])
            _run(["git", "-C", str(repo), "commit", "-q", "-m", "evo: snapshot before revert"])
    except Exception:  # noqa: BLE001
        pass


def needs_consolidation(cfg, p):
    add = p["agent"] / "system_prompt_addendum.md"
    size = 0
    if add.exists():
        try:
            size = len(add.read_text(encoding="utf-8"))
        except OSError:
            size = 0
    skills = list((p["agent"] / "skills").glob("*.md"))
    return size > int(cfg.get("consolidate_addendum_chars", 1500)) or len(skills) > int(cfg.get("consolidate_skills", 8))


def consolidate(state_dir, cfg, server, p):
    """知识合并(维护代):整理附加指令与技能库,同步删除冗余,提交进化仓库。"""
    add_path = p["agent"] / "system_prompt_addendum.md"
    addendum = ""
    if add_path.exists():
        try:
            addendum = add_path.read_text(encoding="utf-8")
        except OSError:
            addendum = ""
    skills = {}
    for f in sorted((p["agent"] / "skills").glob("*.md")):
        try:
            skills[f.name] = f.read_text(encoding="utf-8")
        except OSError:
            continue
    menu = "\n\n".join("### %s\n%s" % (name, content[:1500]) for name, content in skills.items()) or "(空)"
    try:
        data = server.chat(CONSOLIDATE_PROMPT % (addendum[:3000], menu), cfg["executor_model"], system=META_SYSTEM)
        reply = str(data.get("reply") or "")
        m = re.search(r"\{[\s\S]*\}", reply)
        obj = json.loads(m.group(0)) if m else {}
    except Exception as exc:  # noqa: BLE001
        return "合并调用失败: %s" % exc
    new_addendum = str(obj.get("addendum") or "").strip()
    if not new_addendum or len(new_addendum) > 2000:
        return "合并结果无效(addendum 为空或过长)"
    raw_skills = obj.get("skills") if isinstance(obj.get("skills"), dict) else {}
    clean_skills = {}
    for name, content in list(raw_skills.items())[:8]:
        n = re.sub(r"[^0-9A-Za-z._-]", "", str(name))
        if not n.endswith(".md"):
            n += ".md"
        if isinstance(content, str) and content.strip() and len(content) <= 3000:
            clean_skills[n] = content.strip() + "\n"
    # 应用:重写附加指令;技能库与结果同步(未列出的删除)
    add_path.write_text(new_addendum + "\n", encoding="utf-8")
    skills_dir = p["agent"] / "skills"
    existing = [f.name for f in skills_dir.glob("*.md")]
    for f in skills_dir.glob("*.md"):
        if f.name not in clean_skills:
            try:
                f.unlink()
            except OSError:
                pass
    for name, content in clean_skills.items():
        (skills_dir / name).write_text(content, encoding="utf-8")
    try:
        git_commit(p["root"], "evo(maintenance): consolidate knowledge (%d->%d skills)" % (len(existing), len(clean_skills)))
    except Exception as exc:  # noqa: BLE001
        log("consolidate commit failed: %s" % exc)
    summary = str(obj.get("summary") or "知识合并完成")[:150]
    return "addendum %d->%d 字 / 技能 %d->%d 个 · %s" % (
        len(addendum), len(new_addendum), len(existing), len(clean_skills), summary)


def revert_last(state_dir, cfg=None):
    """回滚最近一次采纳(应用仓库与进化仓库各自的提交)。"""
    cfg = cfg or load_config(state_dir)
    st = load_state(state_dir)
    adopted = st.get("adopted") or []
    if not adopted:
        return False, "没有可回滚的改动"
    last = adopted[-1]
    evo_root = paths(state_dir)["root"]
    app_root = Path(cfg.get("app_dir") or (Path(state_dir) / "app"))
    done, failed = [], []
    for repo_name, commit in last.get("commits", []):
        repo = evo_root if repo_name == "evo" else app_root
        _snapshot_repo(repo)
        code, out = _run(["git", "-C", str(repo), "revert", "--no-edit", commit], timeout=60)
        if code == 0:
            done.append(repo_name)
        else:
            _run(["git", "-C", str(repo), "revert", "--abort"])
            failed.append("%s(%s)" % (repo_name, out[-120:]))
    if failed and not done:
        return False, "回滚失败: " + "; ".join(failed)
    st["adopted"] = adopted[:-1]
    if "app" in done:
        st["restart_needed"] = True
    save_state(state_dir, st)
    if failed:
        return False, "部分回滚失败: " + "; ".join(failed)
    return True, "已回滚第 %d 代采纳(%s),重启后生效" % (last.get("gen", 0), ",".join(done) or "无提交")


def reflect(state_dir, cfg, server, task, transcript, score, reason):
    prompt = REFLECT_PROMPT % (
        round(score, 2), reason,
        task.get("title"),
        (transcript.get("text") or "")[:600],
        "; ".join("%s(%s)" % (t["name"], "ok" if t["ok"] else "fail") for t in transcript.get("tools", [])) or "无",
    )
    try:
        data = server.chat(prompt, cfg["executor_model"], system=META_SYSTEM)
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
        data = server.chat(PROPOSE_PROMPT % (reflection[:800], addendum, skills), cfg["executor_model"], system=META_SYSTEM)
        reply = str(data.get("reply") or "")
        m = re.search(r"\{[\s\S]*\}", reply)
        obj = json.loads(m.group(0)) if m else {}
    except Exception as exc:  # noqa: BLE001
        return {"error": "提案调用失败: %s" % exc}
    proposal = {"scaffold": {}, "code_target": None, "summary": str(obj.get("summary") or "")[:200], "raw_reply": reply[:400]}
    scaffold = obj.get("scaffold")
    if isinstance(scaffold, dict):
        for raw_rel, content in list(scaffold.items())[:5]:
            rel = str(raw_rel).replace("\\", "/").strip().lstrip("./").strip()
            if not rel:
                continue
            if not rel.startswith("agent/"):
                rel = "agent/" + rel.lstrip("/")
            if rel.count("..") == 0 and isinstance(content, str) and len(content) <= 20000:
                if rel == "agent/system_prompt_addendum.md" and len(content) > 3000:
                    continue  # 防止提示词膨胀:附加指令体积上限
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
                        cfg["executor_model"], system=META_SYSTEM)
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

    # 维护:把"连败达到上限"的卡题移入退休区(含历史遗留状态),腾出探索空间
    try:
        fails_map0 = st.get("task_fails") or {}
        retire_at = int(cfg.get("retire_after_fails", 3))
        for t in bench_tasks(state_dir):
            if int(fails_map0.get(t["id"], 0)) >= retire_at:
                retired_dir = p["root"] / "bench" / "retired"
                retired_dir.mkdir(parents=True, exist_ok=True)
                src = p["tasks"] / ("%s.json" % t["id"])
                if src.exists():
                    src.replace(retired_dir / src.name)
                fails_map0.pop(t["id"], None)
                step("retire-task", t["id"])
        st["task_fails"] = fails_map0
    except Exception as exc:  # noqa: BLE001
        log("stuck cleanup failed: %s" % exc)

    # 知识库维护代:附加指令过长或技能过多时,本代专门做知识合并
    try:
        if needs_consolidation(cfg, p):
            summary = consolidate(state_dir, cfg, server, p)
            result["consolidate"] = summary
            step("consolidate", summary)
            append_jsonl(p["scores"], {
                "gen": gen, "when": now(), "task": "maintenance", "title": "知识库合并",
                "kind": "maintenance", "score": 1.0, "reason": summary[:120], "adopted": True,
            })
            st["last_score"] = 1.0
            st["updated"] = now()
            save_state(state_dir, st)
            _write_json(gen_dir / "result.json", result)
            return result
    except Exception as exc:  # noqa: BLE001
        step("consolidate", "失败: %s" % str(exc)[:120])

    task = pick_task(state_dir)
    # 派活:弱项不足或每 3 代,让模型出一个新任务
    try:
        last_scores = {}
        for line in read_jsonl(p["scores"])[-200:]:
            last_scores[line.get("task")] = line.get("score", 0)
        weak = sum(1 for t in bench_tasks(state_dir) if last_scores.get(t["id"], -1) < 0.6)
        fails_map = st.get("task_fails") or {}
        stuck = sum(1 for t in bench_tasks(state_dir) if int(fails_map.get(t["id"], 0)) >= 3)
        if weak < 2 or stuck >= 2 or gen % 3 == 0:
            new_task, terr = generate_task(state_dir, cfg, server)
            if new_task:
                step("generate-task", "%s:%s" % (new_task["id"], new_task["title"]))
            elif terr:
                step("generate-task", "跳过: %s" % terr)
            task = pick_task(state_dir)
    except Exception as exc:  # noqa: BLE001
        step("generate-task", "异常: %s" % str(exc)[:80])

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

    # 卡题追踪:连续低分达到上限的任务退休(不再参与轮换)
    fails_map = st.setdefault("task_fails", {})
    if score < 0.5:
        fails_map[task["id"]] = int(fails_map.get(task["id"], 0)) + 1
        if int(fails_map[task["id"]]) >= int(cfg.get("retire_after_fails", 5)):
            try:
                retired_dir = p["root"] / "bench" / "retired"
                retired_dir.mkdir(parents=True, exist_ok=True)
                src = p["tasks"] / ("%s.json" % task["id"])
                if src.exists():
                    src.replace(retired_dir / src.name)
                fails_map.pop(task["id"], None)
                step("retire-task", task["id"])
            except OSError as exc:
                log("retire failed: %s" % exc)
    else:
        fails_map.pop(task["id"], None)

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
    step("propose", result["proposal_summary"][:80] or "无有效提案")

    changed = []
    regression_quiet = False
    try:
        quiet_n = int(cfg.get("regression_quiet_scores", 3))
        quiet_min = float(cfg.get("regression_quiet_min", 0.85))
        hist = [line.get("score", 0) for line in read_jsonl(p["scores"])
                if line.get("task") == task["id"]][-quiet_n:]
        if len(hist) >= quiet_n and all(float(s) >= quiet_min for s in hist):
            regression_quiet = True
    except Exception:  # noqa: BLE001
        pass
    if proposal.get("scaffold") and score < 0.5:
        step("scaffold-gated", "得分 %.2f 偏低:提示词/技能改动仅记录不采纳" % score)
        proposal["scaffold"] = {}
    elif proposal.get("scaffold") and regression_quiet:
        step("scaffold-quiet", "回归演练(该任务连续高分):改动仅记录不采纳")
        proposal["scaffold"] = {}
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
                st.setdefault("adopted", []).append({
                    "gen": gen, "changed": changed, "commits": commits,
                    "summary": result["proposal_summary"][:120], "when": now(),
                })
                st["restart_needed"] = bool(proposal.get("code"))
                step("adopt", ",".join(changed))
            elif changed:
                git_restore(p["root"], [f for f in changed if f.startswith("agent/")])
                git_restore(Path(cfg["_app_dir"]), [f for f in changed if not f.startswith("agent/")])
                step("rollback", "测试门未通过,已回滚")
    else:
        pass

    append_jsonl(p["scores"], {
        "gen": gen, "when": now(), "task": task["id"], "title": task.get("title"),
        "kind": task.get("kind"), "score": score, "reason": reason,
        "adopted": bool(changed) and not shadow,
    })
    st["last_error"] = result.get("error", "")
    st["last_score"] = score
    st["updated"] = now()
    result["adopted"] = bool(changed) and not shadow
    save_state(state_dir, st)
    _write_json(gen_dir / "result.json", result)
    return result


# ---------------------------------------------------------------- 守护进程

def server_alive(base):
    try:
        req = urllib.request.Request(base.rstrip("/") + "/api/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def restart_server(state_dir, cfg):
    """采纳代码改动后重启服务端(停 → 拉活 → 等健康)。"""
    base = cfg.get("server_url") or "http://127.0.0.1:8600"
    try:
        req = urllib.request.Request(base.rstrip("/") + "/api/shutdown", method="POST")
        urllib.request.urlopen(req, timeout=8)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(50):
        time.sleep(0.6)
        if not server_alive(base):
            break
    app_dir = Path(cfg.get("app_dir") or (Path(state_dir) / "app"))
    script = app_dir / "server.py"
    if not script.exists():
        return False
    env = dict(os.environ)
    env["QBALL_HOME"] = str(state_dir)
    kwargs = {"cwd": str(app_dir), "env": env,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, str(script), "--no-browser"], **kwargs)
    for _ in range(80):
        time.sleep(0.6)
        if server_alive(base):
            return True
    return False


def _sleep_checked(state_dir, seconds):
    """可被停止信号打断的休眠(长退避时也能及时暂停/退出)。"""
    end = time.time() + seconds
    while time.time() < end:
        if control_flag(state_dir, ".stop").exists():
            return False
        time.sleep(1)
    return True


def daemon(state_dir):
    global _LOG_FILE
    p = init_workspace(state_dir)
    _LOG_FILE = str(p["log"])
    (p["pid"]).write_text(str(os.getpid()), encoding="ascii")
    control_flag(state_dir, ".stop").unlink(missing_ok=True)
    log("daemon started, pid=%d" % os.getpid())
    failures = 0
    no_progress = 0
    try:
        while not control_flag(state_dir, ".stop").exists():
            cfg = load_config(state_dir)
            if control_flag(state_dir, ".pause").exists() or not cfg.get("enabled"):
                time.sleep(5)
                continue

            # 采纳了代码改动 → 重启服务端生效
            st = load_state(state_dir)
            if st.get("restart_needed"):
                ok = restart_server(state_dir, cfg)
                st = load_state(state_dir)
                st["restart_needed"] = False
                st["last_restart"] = now() if ok else ""
                if not ok:
                    st["last_error"] = "采纳后重启服务端失败,请手动 qball stop && qball start"
                save_state(state_dir, st)
                log("server restarted" if ok else "server restart FAILED")

            run_once = control_flag(state_dir, ".run_once")
            if run_once.exists():
                run_once.unlink(missing_ok=True)
            try:
                result = run_generation(state_dir, cfg)
                if result.get("error"):
                    failures += 1
                else:
                    failures = 0
                score = float(result.get("score") or 0)
                if result.get("adopted") or score >= 0.7:
                    no_progress = 0
                else:
                    no_progress += 1
                if failures >= 8:
                    st = load_state(state_dir)
                    st["last_error"] = "连续失败 %d 次,已自动暂停" % failures
                    save_state(state_dir, st)
                    control_flag(state_dir, ".pause").touch()
                    log("too many failures, paused")
                elif failures >= 3:
                    log("failures=%d,backoff 15min" % failures)
                    if not _sleep_checked(state_dir, 900):
                        break
                elif no_progress >= 12:
                    log("no progress for %d generations, backoff 30min" % no_progress)
                    if not _sleep_checked(state_dir, 1800):
                        break
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log("generation failed: %s" % exc)
            if not _sleep_checked(state_dir, max(10, int(cfg.get("cooldown_seconds", 60)))):
                break
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
