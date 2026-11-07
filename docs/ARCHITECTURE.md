# 架构与实现边界

GUI 与管理型 MCP 均调用 `ManagerCore`。GUI 使用 QThreadPool/信号；MCP 使用 anyio.to_thread；Core 的长任务使用有界 ThreadPoolExecutor。SQLite 保存本地事实，RegistryClient 调用公开在线 Registry，二者的数据语义独立。没有 LLM、代码生成器或自主工程规划器。

| 路径 | 职责 |
|---|---|
| src/mcp_manager/models.py | 严格启动配置、Transport、凭据引用、身份摘要 |
| core/manager.py | 共用 API、授权执行、后台任务、迁移、仪表盘 |
| core/database.py | SQLite v1、注册、记录、审计、完整性检查、备份恢复 |
| core/discovery.py | 客户端、项目、已安装 Python 元数据及用户服务的静态发现 |
| core/runtime.py | 标准 MCP 会话、工具分页、明确调用、持有连接和超时 |
| core/registry.py | 官方 v0.1 JSON API、详情/版本、缓存 |
| core/installer.py | Python/Node/Docker/Git/远程安装计划及拥有目录维护 |
| core/security.py | 一次性授权、摘要、脱敏和凭据引用处理 |
| core/extensions.py | 工作区、快照、静态检查、证据、发布与版本记录 |
| adapters/platform.py | Windows/Linux 用户路径、工具存在性和 WSL 检测 |
| adapters/clients.py | Codex TOML、DSH Cordis YAML 读取、目标变更及备份恢复 |
| adapters/isolation.py | bubblewrap/prlimit 与显式 Docker 的 OS 隔离执行 |
| extension_host.py | 已注册 Generic 扩展的独立 MCP 进程，启动校验，无热注入 |
| gui/app.py / server.py / cli.py | 三个入口，共用相同业务逻辑 |

发现结果分别记录候选/项目存在/启动配置已读；协议测试再记录进程启动、Transport 连接、握手、工具发现和指定调用。静态证据不会升级成求解证据。来源和命令/参数/工作目录/端点参与身份判断，不按名称合并；不同配置仍可能指向同一工程，需用户确认后维护，避免误合并。

所有会执行代码、修改客户端或删除受管部署的入口生成计划。计划绑定内容摘要、原配置哈希和适用的当前部署状态，过期或已消费计划不能重放。批准接口仅在可信本地 GUI/CLI；管理型 MCP 没有批准工具。默认 Agent 权限 read_only，approved_only 允许消费已在本地批准的计划。此边界限制管理型协议调用；拥有本机任意 Shell/写权限的独立 Agent 属于操作系统授权范围，管理器不能把它变成受限账户。

凭据使用 env/keyring 引用。日志、注册信息及导出做脱敏；若原客户端配置含明文秘密，备份通过系统安全存储保管的 Fernet 密钥加密，系统安全存储不可用则中止修改，不降级为明文密钥文件。普通不含秘密的配置可备份为受限权限原文。写入仅改变目标项，原子替换前检查源哈希；恢复同样拒绝已被再次编辑的目标。

管理器只停止自己持有的 MCP 会话，不接管已有外部进程。远程 MCP 只记录协议连接状态，不声称读取远端 CPU/内存。安装使用独立依赖目录；更新保留旧目录供回滚。卸载只删除带管理器所有权标记的目录，外部源码解除登记即可。

扩展测试与正式运行是两个权限阶段：测试必须进入真实 OS 隔离，未具备软件原生适配器的级别记 not_run；正式 Generic Host 加载的是已审批受信任代码，其 Python 进程不是生产沙箱。没有通过静态检查即可任意写工业工程文件的接口。Abaqus 发布门槛保留真实 native 证据，本阶段不放宽。

当前仅 schema_version/user_version=1，有拒绝未来版本及备份恢复校验；尚无第二个数据库版本可执行升级迁移。Windows 适配代码与 CI 定义不构成 Windows 原生验证结果。

## 轻量工具代理

`gateway_server.py` 是与管理型服务分开的 stdio 前端，只注册3个入口。`core/gateway.py` 负责本地审批策略、缓存搜索、Schema 校验和有界结果；`core/gateway_connection.py` 的单任务持有后端 SDK 会话并串行转发，复用原 Runtime transport/凭据适配。数据库的每服务单调 gateway_generation 使状态变更永久撤销旧授权，现有 JSON 记录惰性默认0，无 SQLite 表迁移。CLI 的 plan-gateway/approve/execute 配置策略，协议不提供自授权入口。详情见 [LIGHTWEIGHT_GATEWAY.md](LIGHTWEIGHT_GATEWAY.md)。
