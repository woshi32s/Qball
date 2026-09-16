"""测试用 MCP 服务器(fixture):stdio JSON-RPC,提供 echo / add / boom 三个工具。

用法: python fake_mcp_server.py   (由 Qball 的 MCP 客户端按 stdio 拉起)
"""
import json
import sys

TOOLS = [
    {"name": "echo", "description": "回显文本",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "add", "description": "两数相加",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                     "required": ["a", "b"]}},
    {"name": "boom", "description": "总是报错(用于测试错误传播)",
     "inputSchema": {"type": "object", "properties": {}}},
]


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1.0"}}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                result = {"content": [{"type": "text", "text": "echo:" + str(args.get("text") or "")}]}
            elif name == "add":
                try:
                    total = float(args.get("a", 0)) + float(args.get("b", 0))
                except (TypeError, ValueError):
                    total = float("nan")
                result = {"content": [{"type": "text", "text": ("%g" % total)}]}
            elif name == "boom":
                result = {"content": [{"type": "text", "text": "intentional failure"}], "isError": True}
            else:
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32601, "message": "unknown tool " + str(name)}})
                continue
            send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": "unknown method " + str(method)}})


if __name__ == "__main__":
    main()
