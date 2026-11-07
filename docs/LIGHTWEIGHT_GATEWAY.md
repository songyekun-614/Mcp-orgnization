# 小上下文模型的轻量工具代理

新增独立 stdio 入口 `mcp-manager-gateway`，与原管理型 `mcp-manager-server` 共用 Core 和本地注册库。代理只暴露三个工具，不内置大模型。完整后端工具目录保存在主机，搜索返回简短条目；只有被选中的工具才返回完整参数。

| 工具 | 参数 | 行为 |
|---|---|---|
| `search_tools` | query=""、server=null、offset=0、limit=3（最大10） | 中英文关键词搜索，返回 tool_id、短描述、分页；不向模型发送全部 Schema |
| `get_tool_details` | tool_id | 返回一个工具的 input_schema、schema_hash、annotations、后端 instructions；最大64 KiB |
| `call_tool` | tool_id、arguments、schema_hash | 校验最新 Schema 后原样转发参数；保持后端错误标志；大结果返回文件和摘要 |

例如先搜索“创建材料”，获取 `abaqus/create_material` 的详情，再按其实际 Schema 传参。工具参数中的 `execute=false` 等预览选项保持原义。模型必须自己理解参数、决定执行与工程判断。

## 本地配置及授权

先用现有 GUI/CLI 将目标后端登记到 Manager。以下命令使用安装了项目的独立 Python 环境：

```bash
mcp-manager list
mcp-manager plan-gateway BACKEND_ID --result-limit 8192 --call-timeout 240
mcp-manager plan PLAN_ID
mcp-manager approve PLAN_ID
mcp-manager execute PLAN_ID
mcp-manager gateway-policy
mcp-manager-gateway
```

计划批准和执行只开放给本地操作。代理自身没有批准、修改策略、执行 Shell 或代码注入工具。计划会实际启动已审查的后端并完成握手和工具发现，冻结这次发现的工具名；仅登记到数据库不会自动向代理授权。可以使用 `--allowlist /path/to/list.json` 限定工具，例如 `{"BACKEND_ID":["get_server_info","create_material"]}`。仅选择自己的普通 MCP 后端，不将管理器自身作为后端。

默认数据目录与 GUI 相同，由平台/XDG 决定；命令也接受 `--data-dir`。客户端配置示例：

```json
{
  "command": "/path/to/venv/bin/python",
  "args": ["-m", "mcp_manager", "gateway", "--data-dir", "/path/to/manager-data"]
}
```

将客户端中原来直接连接后端的条目替换为该连接。若同时保留直接连接，完整工具定义仍可能进入上下文。原35工具管理型 MCP 仍单独可用，日常小上下文工程会话只需连接轻量入口。当前 GUI 没有新增代理配置页面，使用上述 CLI 配置。

## 执行与结果边界

- 搜索使用已验证的本地主机缓存；不为搜索启动所有后端。详情或实际调用才建立选中后端的持久连接。连接由一个工作任务持有，排队串行执行；不将 stdio 后端错误地改成系统服务。
- 一个代理进程对一个后端复用一个连接。不同 DSH 预设仍可各有自己的代理进程；Abaqus 原有共享 GUI、任务快照、SHA-256 文件保护逻辑继续由原服务负责，代理没有改写其源码。
- 每次调用重新发现工具并校验 Schema 哈希与 JSON Schema；Schema 变动要求重新取详情。拒绝外部 Schema 引用。工具名限制128字符，搜索整页和结果摘要受字节限额约束。
- 默认结果预算8192字节，可在2048至65536之间配置。超过预算的脱敏完整响应保存到 Manager 数据目录 `gateway-results/`，权限0600，返回任务标识、文件路径等有限摘要。超过8 MiB拒绝返回，优先使用后端分页或导出。不要将大文件整个读回对话。
- 保留后端 `isError` 和原结构化错误。调用超时、连接中断不会自动重试写入；之后的新显式调用可以重新连接。连接中断后，工程操作是否已经执行必须由 Agent 查询后端任务状态确认。
- 授权绑定精确启动配置和单调状态代次；后端被禁用、重新启用或修改配置后旧授权失效，即使改回原值也不能复活。重新本地批准计划即可恢复。只刷新验证缓存不会撤销授权。
- 凭据使用既有环境/keyring 引用，持久连接保存其启动凭据用于会话期间脱敏；审计不记录调用参数。代理不是任意后端的沙箱，仍需审查后端及允许的工具。

## 本机 DSH 验收（2026-11-07）

四个既有预设 standard/ptc/minimal/cordis 的 Abaqus 直连均替换为 `mcp_gateway`。已备份用户 patch 与 Manager SQLite，只更新原插件的连接配置，其余解析后配置经序列化比较保持一致。通过 DSH 官方 profile 准备函数重建生成根文件，避免旧缓存再次被识别。服务继续运行；已开启的旧对话可能保留旧工具和历史，使用新对话刷新。

实际使用 DSH Cordis、ToolRuntime 和 MCP 客户端执行搜索→详情→调用，真实 AbaqusMCP 元数据与几何 `execute=false` 预览均通过。没有为此次上下文优化启动新 Abaqus GUI 或求解。

| 项目 | 改造前 | 改造后 |
|---|---:|---:|
| DSH 提供给模型的工具数 | 107 | 3 |
| DSH 工具定义 JSON UTF-8 字节 | 178128 | 1565 |

工具定义字节减少约99.12%，不是目标模型 tokenizer 的精确 token 测量，也不意味着全部系统上下文都减少99%。其他插件、系统提示、选中工具的详情与历史结果仍占上下文，长对话仍需摘要或新会话。较大的单工具 Schema 仍可能达到数千字节；当前实现没有自动改写后端参数契约。

证据（本机私有 runtime，不进入发行包）：`runtime/gateway/dsh-acceptance.json`、`dsh-schemas.json`、`dsh-deployment.json`、`regression.log`。代理测试使用明确标记的真实协议测试服务验证错误、超时、分页及凭据，不将测试服务当作 Abaqus 原生执行。Windows 原生部署本次未执行。
