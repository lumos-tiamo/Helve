# 31 · Helve 作为 MCP Server

> **代码锚点**：`server/app/mcp/`、`server/app/routers/mcp.py`
> 测试：`server/tests/test_mcp_server.py`（19 条）

Helve 一直是 MCP **client**（`engine/mcp/`，消费别人的工具）。这一节是反方向：
Claude Code、Cursor 或任何 MCP host 都可以调用进来。

## 端点

```
POST /api/mcp        JSON-RPC 2.0，Bearer 鉴权（与其余 API 同一个 token）
```

支持 `initialize` / `ping` / `tools/list` / `tools/call`，以及握手期的通知。
通知返回 `202` 且无 body。其它方法返回标准的 `-32601`，而不是 500——host 探测
可选能力是正常流量，不是错误。

协议版本取自 `engine.mcp.client.PROTOCOL_VERSION`。同一个运行时在两个方向上
说两个版本，是那种没人会去找的 bug。

## 暴露什么，以及为什么只暴露这些

| 工具 | 作用 |
| --- | --- |
| `memory_search` | 检索本机 Helve 积累的项目记忆，返回最相关的条目 |
| `skills_list` | 列出这个 Helve 能跑的技能 |

**暴露的是"知道什么"，不是"能做什么"。这是设计决定，不是第一版偷懒。**

这个运行时里每一个写类工具都要停下来等人确认——那是产品的整个论点。MCP 调用
方的另一端没有人。暴露一个写类工具意味着二选一：要么永远阻塞在一个没人会回答
的审批上，要么悄悄跳过审批、让 guard 变成装饰。两个都不能接受，所以写操作留在
终端后面。

**只读的文件类工具也被排除，理由是另一条**：外部调用方不携带工作区。没有绑定
工作目录的 `read_file` 要么无用，要么是一个路径穿越面；给一个从未声明工作区的
调用方**发明**一个工作区，正是 guard 被意外绕过的方式。如果要放开，需要的是
一次显式的工作区握手，而不是把白名单加宽。

`test_no_exposed_tool_can_mutate_anything` 向 registry 反查这条边界，而不是钉住
一份名字清单——把工具加进暴露面却没想清楚，会在这里失败而不是发布出去。

## 在 Claude Code 里挂上

```json
{
  "mcpServers": {
    "helve": {
      "type": "http",
      "url": "http://127.0.0.1:8000/api/mcp",
      "headers": { "Authorization": "Bearer <cat ~/.helve/auth_token>" }
    }
  }
}
```

挂上之后，另一个 Agent 就能问 Helve"这个项目里 sqlite 并发写有什么已知的坑"，
而答案来自你自己这些会话里沉淀下来的记忆。

## 返回形状

`tools/call` 同时给 `structuredContent` 和一段 JSON 文本 block。只读文本的 host
不该看到一个空结果；两者内容一致这件事有测试钉着。
