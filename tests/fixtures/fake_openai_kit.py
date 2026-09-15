"""Qball 测试用假 OpenAI 兼容服务(SSE 流式 + reasoning + 模型列表)。

启动: python fake_openai_kit.py            # 默认 0.0.0.0:8484

行为约定(测试用例依赖):
- GET  /v1/models            -> 17 个模型(含未知品牌 mystery-model-v1 用于兜底图标)
- POST /v1/chat/completions  -> stream=true 时 SSE;含 reasoning_content 增量
  - 探针消息 "请用一句话回复我" -> "我是小球,连接成功啦!"(引导页连接测试)
  - 其他消息 -> 富 Markdown 回复(粗体/斜体/列表/行内码/代码块/链接/删除线)
  - 请求 system 提到 emotionId 时 -> 包一层 JSON {"emotionId":"10","reply":...}
"""
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8484"))

MODELS = [
    "gpt-4o", "gpt-4o-mini", "o3-mini", "claude-3.7-sonnet", "claude-3-5-haiku",
    "gemini-2.0-flash", "deepseek-chat", "deepseek-reasoner", "qwen-max",
    "qwen2.5-72b", "kimi-k2", "glm-4-plus", "minimax-abab6.5", "hunyuan-turbo",
    "llama-3.3-70b", "mimo-7b", "mystery-model-v1",
]

PROBE_TEXT = "请用一句话回复我"
PROBE_REPLY = "我是小球,连接成功啦!"

RICH_REPLY = (
    "你好呀,我是**小球**。这是真实链路测试:\n\n"
    "- 第一行列表,带 `行内代码`\n"
    "- 第二行有 *斜体* 和 ~~删除线~~\n"
    "- 第三行只是凑数\n\n"
    "比如这段代码:\n\n"
    "```js\nball.setEmotion(\"10\"); // 开心\n```\n\n"
    "参考 [链接](https://example.com) 就行。"
)

REASONING = "让我想想怎么回答比较好…… 先抓住重点,再给出简短的回答。"

LAST_HEADERS = {}

# 工具调用脚本(测试用):触发词 → 工具名与参数
TOOL_TRIGGERS = [
    ("列一下工作区", "fs.list", {"path": "."}),
    ("跑一条命令", "shell.run", {"command": "echo hi-from-shell"}),
    ("evo-hello.txt", "fs.write", {"path": "evo-hello.txt", "content": "hi from qball\n"}),
    ("fib.py", "fs.write", {
        "path": "fib.py",
        "content": "a, b = 0, 1\nfor _ in range(11):\n    print(a)\n    a, b = b, a + b\n",
    }),
]

_PROPOSAL_STATE = {"n": 0}


def proposal_reply():
    _PROPOSAL_STATE["n"] += 1
    n = _PROPOSAL_STATE["n"]
    if n == 1:
        data = {
            "scaffold": {"system_prompt_addendum.md": "# 附加指令\n- 执行文件类任务前,先列出目录再动手。\n"},
            "code_target": None,
            "summary": "把'先列目录'经验写进附加指令",
        }
    elif n == 2:
        data = {"scaffold": {}, "code_target": "README.md", "summary": "给 README 加进化标记"}
    else:
        data = {
            "scaffold": {"agent/skills/evo-demo.md": "# 技能:先列目录\n执行文件任务前先 fs.list 看一遍结构,再动手。"},
            "code_target": None,
            "summary": "沉淀一个技能文件",
        }
    return json.dumps(data, ensure_ascii=False)


def evolution_reply(user):
    if "【评审】" in user:
        return '{"score": 0.85, "reason": "工具使用顺畅,输出完整"}'
    if "【反思】" in user:
        return "这次任务完成得干净,但可以先规划再动手;下次直接复用已有目录结构,少走一步探索。"
    if "【出题】" in user:
        return json.dumps({
            "title": "写一个待办清单",
            "prompt": "在工作区创建 todo.md,写三行待办事项,最后一行以 3. 开头。完成后简短说明。",
            "kind": "verify",
            "verify": {"type": "file_contains", "path": "todo.md", "text": "3."},
        }, ensure_ascii=False)
    if "【提案】" in user:
        return proposal_reply()
    if "【代码提案】" in user:
        m = re.search(r"(?ms)----\n(.*?)\n----", user)
        content = m.group(1) if m else ""
        find = ""
        for line in content.splitlines():
            if line.startswith("# "):
                find = line
                break
        if not find and content.splitlines():
            find = content.splitlines()[0]
        replace = find + "\n\n<!-- evolved by qball -->"
        return json.dumps({"find": find, "replace": replace, "summary": "README 标记进化痕迹"}, ensure_ascii=False)
    return None


def pick_scripted_tool(user_text):
    for trigger, name, args in TOOL_TRIGGERS:
        if trigger in user_text:
            return name, args
    return None, None


def chunks(text, size):
    for i in range(0, len(text), size):
        yield text[i:i + size]


def pick_reply(messages):
    user = ""
    system = ""
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "system":
            system = str(m.get("content") or "")
        elif m.get("role") == "user":
            user = str(m.get("content") or "")
    evo = evolution_reply(user)
    if evo is not None:
        reply = evo
    else:
        reply = PROBE_REPLY if PROBE_TEXT in user else RICH_REPLY
    if "emotionId" in system:
        return json.dumps({"emotionId": "10", "reply": reply}, ensure_ascii=False)
    return reply


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[kit] " + (fmt % args) + "\n")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})
            return
        if path == "/debug/last_headers":
            self._json(200, LAST_HEADERS)
            return
        self._json(404, {"error": {"message": "not found: " + path}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {}
        path = self.path.split("?")[0].rstrip("/")
        if not path.endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found: " + path}})
            return
        LAST_HEADERS.clear()
        for k, v in self.headers.items():
            LAST_HEADERS[k.lower()] = v

        messages = payload.get("messages") or []
        model = str(payload.get("model") or "fake-model")
        wants_tools = "tools" in payload

        # 元任务(评审/反思/提案/出题)走专用回复,不触发脚本化工具调用
        last_user = ""
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "user":
                last_user = str(m.get("content") or "")
        is_meta = any(mk in last_user for mk in ("【评审】", "【反思】", "【提案】", "【出题】", "【代码提案】"))

        # 模拟"不支持 tools"的上游,触发服务端文本协议降级
        if wants_tools and model == "fake-model-notools":
            self._json(400, {"error": {"message": "tools are not supported by this model"}})
            return

        content = pick_reply(messages)
        tool_name, tool_args = (None, None) if is_meta else self._scripted_tool(messages)

        if tool_name and wants_tools:
            self._stream_tool_call(model, tool_name, tool_args)
            return
        if tool_name and not wants_tools:
            content = json.dumps({
                "emotionId": "30",
                "reply": "我来看看…",
                "action": {"tool": tool_name, "args": tool_args},
            }, ensure_ascii=False)

        if not payload.get("stream"):
            self._json(200, {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }],
            })
            return

        self._stream_text(model, content)

    @staticmethod
    def _scripted_tool(messages):
        last_user = ""
        saw_tool_result = False
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role == "user":
                last_user = str(m.get("content") or "")
                if last_user.startswith("[工具结果"):
                    saw_tool_result = True
            elif role == "tool":
                saw_tool_result = True
        if saw_tool_result:
            return None, None
        return pick_scripted_tool(last_user)

    def _begin_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()

    def _sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _stream_tool_call(self, model, name, args):
        self._begin_stream()
        try:
            self._sse({"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                       "choices": [{"index": 0, "finish_reason": None, "delta": {"tool_calls": [{
                           "index": 0, "id": "call_kit_1", "type": "function",
                           "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                       }]}}]})
            self._sse({"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _stream_text(self, model, content):
        self._begin_stream()

        def delta_piece(text):
            return {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                    "choices": [{"index": 0, "delta": text, "finish_reason": None}]}

        try:
            for piece in chunks(REASONING, 10):
                self._sse(delta_piece({"reasoning_content": piece}))
                time.sleep(0.01)
            for piece in chunks(content, 6):
                self._sse(delta_piece({"content": piece}))
                time.sleep(0.01)
            self._sse({"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("[kit] fake openai listening on %d, %d models" % (PORT, len(MODELS)))
    server.serve_forever()
