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

        content = pick_reply(payload.get("messages"))
        model = str(payload.get("model") or "fake-model")

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

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()

        def sse(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        def delta_piece(text):
            return {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                    "choices": [{"index": 0, "delta": text, "finish_reason": None}]}

        try:
            for piece in chunks(REASONING, 10):
                sse(delta_piece({"reasoning_content": piece}))
                time.sleep(0.01)
            for piece in chunks(content, 6):
                sse(delta_piece({"content": piece}))
                time.sleep(0.01)
            sse({"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("[kit] fake openai listening on %d, %d models" % (PORT, len(MODELS)))
    server.serve_forever()
