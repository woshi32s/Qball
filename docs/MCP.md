# MCP 扩展(接入生态工具)

Qball 支持 **MCP(Model Context Protocol)**——任何 MCP 服务器(文件系统、GitHub、浏览器、
数据库、搜索……)都能直接变成小球的工具,无需改代码。

## 配置

编辑 `~/.qball/mcp.json`(设置面板 →「MCP 扩展」里能看到路径),格式:

```json
{
  "servers": {
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\me\\Documents"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx" }
    }
  }
}
```

- `command` / `args`:怎么启动这个 MCP 服务器(和 mcp.so、awesome-mcp-servers 上的说明一致)
- `env`:给服务器注入环境变量(API Key 等;不会外传)
- `cwd`:可选,服务器进程的工作目录
- `enabled`:默认 `true`;设 `false` 可临时停用
- `auto_approve`:可选。`true` 放行该服务器全部工具;或写成数组只放行指定工具,例如
  `"auto_approve": ["read_text_file", "list_directory"]`

改完在设置面板点「**重新加载**」,或重启 Qball。工具会以 `mcp.<服务器名>.<工具名>`
出现(例:`mcp.files.read_text_file`)。

## 审批与安全

- MCP 工具的改动/执行默认**逐次询问**(和命令执行同样的审批条)
- 只读类工具建议用 `auto_approve` 放行,减少打扰
- 服务器进程由 Qball 按需启动、随 Qball 退出而关闭;崩溃会自动重启一次

## Windows 注意事项

- `npx` / `npm` / `.cmd` 脚本会自动用 `cmd /c` 包装(否则 Node 18+ 会报 EINVAL)
- 首次运行 `npx -y` 需要联网下载服务器包,可能要等十几秒;装过的会快很多
- 需要 Node.js 已安装(多数 MCP 服务器是 npm 包)

## 当前限制

- 仅支持 **stdio** 传输(HTTP/SSE 传输暂未支持;绝大多数本地服务器都是 stdio)
- 单用户本机使用;不支持需要 OAuth 的远端服务器
- 工具数量过多会稀释模型注意力——只挂你真正需要的服务器
