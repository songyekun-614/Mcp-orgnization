# 轻量 MCP 工具代理实施计划

用户已批准三入口的按需发现方案。目标：模型仅收到 search_tools/get_tool_details/call_tool，后端完整目录与会话留在本机，原 MCP 继续执行参数/文件/工程安全校验。不引入 LLM，不生成任意代码或 Shell 入口。

接口：search_tools(query,server,offset,limit) 返回短目录；get_tool_details(tool_id) 返回单项完整输入Schema和schema_hash；call_tool(tool_id,arguments,schema_hash) 按已查看的契约转发，保持原MCP错误。大结果落本地受限文件，只回传有界结构摘要与路径。

授权：复用ManagerCore的本地计划审批，选定已登记后端、确切启动配置、允许工具。批准时只握手和发现工具，冻结允许名称；模型不能批准、修改后端或添加新工具。配置/启用状态改变使旧授权失效；禁止管理器/代理递归。预览/execute仍由原工具处理。

连接：每后端一个异步worker持有SDK上下文并串行消费请求，不跨任务退出anyio取消域；同一模型会话持续复用连接。超时不自动重试写入，显式下次调用才允许重连。调用前刷新目录并校验schema_hash，避免过期接口静默执行。日志脱敏。

复用：RuntimeManager负责已有Transport、环境引用、隐私处理与发现；Database保存策略/缓存/审计；新增core/gateway.py负责授权、检索与路由，gateway_server.py只提供三工具。CLI增加本地配置计划和gateway入口。原35工具管理型服务不变。

- [x] 基线与失败测试：真实stdio、三入口体积、中文检索、单项Schema、原始错误、持久PID、权限拒绝、超时/脱敏/大结果。
- [x] 实现授权策略、会话worker、确定性目录检索与有界结果。
- [x] 接入CLI/ManagerCore与MCP三入口；验证HTTP及stdio实际协议。
- [x] 真实Abaqus元数据与预览，再验证单窗口路径未变化。
- [x] 备份DSH配置，把四个预设的Abaqus直连替换为 mcp_gateway 轻量代理；不同时注入107项原始工具。
- [x] 全量回归、实际DSH工具列表/工具定义体积测量、中文文档和限制报告。

在同一仓库分支实现，不改工业软件核心，不改其他MCP。部署授权基于本次已确认方案与既有DSH接入指令。Windows原生测试没有环境则明确未执行。字节/字符统计不能伪装成目标模型的tokenizer计数。

完成证据：105 passed / 1 skipped；真实DSH三入口、Abaqus元数据和预览通过。工具定义178128→1565 UTF-8字节。单窗口链路使用原后端配置，未改Abaqus源码或执行原生求解。Windows原生本轮未执行。审查三项问题修复后复核通过。
