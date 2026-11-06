# MCP Manager V2.0 实施计划

规格依据：用户本轮完整二十二节需求。当前无现有 Manager 源码；复用已安装 MCP SDK 和既有 AbaqusMCP 协议接口，不修改工业 MCP。

架构：src/mcp_manager 下 models.py 为共享类型；core 为统一业务；adapters 为平台/客户端/安装实现；gui 和 server 调用相同 ManagerCore。SQLite 记录服务器、验证事实、计划、任务、扩展、审计。高风险变更使用内容摘要绑定的计划，由本地 GUI/CLI 批准；管理型 MCP 不提供自授权工具。

明确选择：stdio 连接测试临时启动并在完成后关闭，常驻 stdio 会话仅由显式启动操作维持；不接管发现的外部 PID。所有发现默认静态读取，不执行未知项目。Windows 使用 pathlib/平台适配器，原生验收没有设备则记未执行。

- [x] 审查系统/客户端/源码，建立零代码基线与独立 venv。
- [x] TDD 实现平台路径、严格 ServerSpec、来源去重、SQLite 迁移、敏感信息与授权计划。
- [x] 客户端只读发现与有界目录扫描；测试 Codex TOML 和 DSH Cordis YAML、符号链接/中文路径/取消。
- [x] 实现临时 stdio/HTTP 握手、工具发现、受权调用、持久验证状态、日志与后台任务。
- [x] 基础 Qt GUI 共用 Core，扫描/测试不阻塞主线程，真实后端指标与工具详情。
- [x] 官方 Registry v0.1 API + 缓存；隔离 Python/Node/Docker/Git 安装计划、更新回滚卸载。
- [x] 客户端配置 preview/hash校验/备份/原子目标修改/恢复，不改动用户真实配置验收。
- [x] 管理型 MCP 暴露统一 Core，默认只读，授权从本地可信界面产生。
- [x] 扩展工作区与静态检查、实际 OS 隔离测试、分级证据、批准发布/启停/回滚；Abaqus 注册表仅在明确计划授权后写入。
- [x] 迁移导出脱敏模板，导入重新定位与安装计划；无自动安装覆盖。
- [x] 真实 AbaqusMCP 只读握手、本地低风险 MCP 安装、stdio/HTTP与Qt测试、Linux wheel/启动入口、Windows CI与打包脚本。
- [x] 全量回归、独立审查、中文用户/架构/开发报告，明确剩余能力与未执行的Windows测试。

安全约束：无LLM；无任意Shell执行工具；未知代码只在可用OS隔离中运行；虚拟环境仅依赖隔离；凭据引用不明文入库；源项目和客户端原配置保持原样，实际配置修改需要独立授权。

验收详见 MCP_MANAGER_DEVELOPMENT_REPORT.md。上述任务项表示已完成本阶段实施与明确缺口记录，不能解读为 Windows 原生、工业扩展或所有联网安装分支均已通过。
