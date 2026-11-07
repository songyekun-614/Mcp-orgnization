# MCP Manager V2.0 开发报告

日期：2026-11-06。项目：`~/Projects/MCP/MCP-Manager`。本报告区分已经实际运行的闭环、尚未执行的平台测试与缺失适配器，不将目标规格全部标为完成。

## 一、交付结论

已完成可运行的第一阶段核心产品：环境检测 → 静态发现 → SQLite 登记 → 协议连接与工具查询 → 原生 Qt GUI → 官方 Registry 搜索与缓存 → 审批安装/维护 → Codex/DSH 配置 → 管理型 MCP → Generic 扩展真实隔离测试、注册调用与回滚。尚不具备全部工业软件扩展、全平台原生发布及所有安装方式的实际安装验收。

最终全量回归 **88 passed, 1 skipped**，耗时 16.44 秒。wheel/sdist 构建通过，pip check 无依赖冲突，compileall 通过。测试不把 fixture、GUI 契约 stub 或静态检查当作工业软件执行结果。

## 二、原工程与基线

接手时未发现可复用的 MCP Manager 工程，因此在指定目录创建独立包；已有 AbaqusMCP 继续作为外部软件 MCP 使用。复用标准 MCP SDK，不复制其 Bridge、Snapshot Runner 或工业 API。Manager 验收未改 Abaqus 源码、CAE、ODB 或其他 MCP 工程，未安装另一个重复管理系统。

实际开发环境为 Debian/Linux x86_64、Python 3.13、mcp 1.30、PySide6 6.12；独立环境 `~/venvs/mcp-manager`。确切依赖见 DEPENDENCIES_LINUX_TESTED.txt，它是本次 Linux 测试环境清单，不是 Windows 通用锁文件。初始无 Manager 测试，随后按功能补充回归；红灯日志保留在 runtime。既有 Abaqus 工程自己的原生测试记录不计入 Manager 的 88 项结果。

## 三、模块与阶段状态

| 阶段 | 实际完成 | 边界/未完成 |
|---|---|---|
| Phase 1 基础 | 平台检测、分层静态发现、注册库、分页工具查询、真实握手、八页 GUI | 周期自动扫描调度、全系统进程识别尚缺；不默认深扫 |
| Phase 2 搜索安装 | 官方 JSON API、详情/版本及缓存，五类安装计划，Python 本地 wheel 真安装/更新/回滚/卸载 | Node/Docker/Git/uv 的安装分支为计划/单元验证，未逐项联网安装；Git 不自动构建 |
| Phase 3 客户端 | Codex TOML 与 DSH YAML、预览、哈希检查、目标项原子写入、备份恢复 | DSH 同文件重复名称拒绝歧义编辑；不自动刷新所有客户端工具缓存 |
| Phase 4 管理 MCP | 35 个工具、异步任务、默认只读、单次计划、本地批准、错误结构 | 远程管理服务器仅允许 loopback，本阶段无完整远程身份服务 |
| Phase 5 扩展 | 工作区、不可变快照、AST/Schema、OS 隔离、Generic 发布/调用/版本回滚 | 工业 native/e2e 适配器未实现，Abaqus 发布因缺原生证据阻止 |
| Phase 6 发布 | 用户安装脚本、wheel/sdist、桌面入口、脱敏迁移、备份恢复、CI 定义、中文文档 | Windows 原生未执行，无已验收 MSI/EXE/AppImage 安装器 |

源码模块见 ARCHITECTURE.md。新增文件包含 `src/mcp_manager/{__init__,__main__,models,cli,server,extension_host}.py`；`core/{manager,database,discovery,runtime,registry,installer,security,extensions}.py`；`adapters/{platform,clients,isolation}.py`；`gui/{__init__,app}.py`；九个 `tests/test_*.py`；示例协议服务器、扩展源码与 manifest；用户安装脚本、跨平台 CI 和本文档。未删除既有其他项目测试。

## 四、发现、注册与运行语义

配置来自实际 Codex/DSH 客户端目录和显式扫描根，支持环境目录覆盖。目录扫描带深度、文件数、时间、忽略、暂停和取消，默认跳过子符号链接。Python 已安装元数据与静态 Server 入口一并检查；仅安装 SDK 或存在包执行器不判定为已连接 MCP。服务定义、尚未安装的源码和无法解析的环境引用列为候选或问题。

身份由启动配置/路径/端点等组合计算，不按显示名称去重。配置来源分别保存；不同参数的 Abaqus 启动配置保留为独立记录。数据库使用 WAL、schema v1、线程独立连接、备份完整性校验与未来版本拒绝。多实例分布式事务协调与后续 v2 迁移脚本不在本阶段。

协议验证事实分别为 process_started、transport_connected、handshake_completed、tools_discovered、tool_call_tested；失败保留阶段。工具发现支持分页并限制页数、工具数与字节量。临时测试关闭会话，显式启动才由管理器持有会话；不会接管其他客户端进程，不将 stdio 一概设为后台服务。

## 五、全部管理型 MCP 接口

实现文件为 `src/mcp_manager/server.py`，业务落在相应 Core/Adapter。各工具的完整输入和输出 Schema 直接从 SDK 的实际 list_tools 导出至 **MANAGEMENT_TOOLS.json**。输入拒绝未知顶层参数；当前输出 Schema 为通用 JSON 对象，具体内容由 Core 返回，后续可进一步收窄为类型化结果模型。错误返回包含 status/error/type/operation/original_exception/message。计划与任务包含关联 ID、状态及实际结果；MCP 不暴露批准接口。

| 工具名称 | 功能 | 参数 | 实现状态 |
|---|---|---|---|
| `detect_platform` | 检测平台、运行环境与路径 | 无 | 已实现 |
| `discover_mcp` | 启动有界静态发现任务 | roots（可选）, mode（可选）, max_depth（可选） | 已实现 |
| `list_installed_mcp` | 列出本地登记项 | 无 | 已实现 |
| `get_mcp_info` | 读取来源、配置及验证事实 | server_id（必填） | 已实现 |
| `list_mcp_tools` | 分页读取已验证的工具 Schema | server_id（必填）, offset（可选）, limit（可选） | 已实现 |
| `get_mcp_status` | 查询当前连接及最近验证 | server_id（必填） | 已实现 |
| `get_mcp_logs` | 读取脱敏审计与任务日志 | server_id（可选） | 已实现 |
| `search_mcp_registry` | 在线搜索及离线缓存 | query（必填）, cursor（可选）, offline（可选） | 已实现 |
| `get_mcp_install_options` | 读取包来源及安装元数据 | name（可选）, version（可选）, offline（可选） | 已实现 |
| `register_mcp` | 登记配置，不执行 | spec（必填） | 已实现 |
| `install_mcp` | 生成安装计划 | request（必填） | 已实现 |
| `configure_mcp_client` | 生成目标配置变更计划 | client（必填）, path（必填）, name（必填）, server_id（可选）, remove（可选） | 已实现 |
| `test_mcp_connection` | 生成握手与可选调用计划 | server_id（必填）, tool_name（可选）, arguments（可选） | 已实现 |
| `enable_mcp` | 生成启用计划 | server_id（必填） | 已实现 |
| `disable_mcp` | 生成禁用计划 | server_id（必填） | 已实现 |
| `control_mcp` | 持有连接启停或解除管理计划 | server_id（必填）, action（必填） | 已实现 |
| `check_mcp_updates` | 查询 Python/Node 版本元数据 | server_id（必填） | 已实现 |
| `update_mcp` | 生成固定版本更新计划 | server_id（必填）, request（必填） | 已实现 |
| `rollback_mcp` | 生成受管部署回滚计划 | server_id（必填） | 已实现 |
| `uninstall_mcp` | 生成拥有目录卸载计划 | server_id（必填） | 已实现 |
| `execute_approved_plan` | 按本地策略异步消费已批准计划 | plan_id（必填） | 已实现 |
| `get_manager_task` | 查询后台任务状态 | task_id（必填） | 已实现 |
| `list_manager_tasks` | 列出任务 | 无 | 已实现 |
| `control_scan` | 暂停/继续/取消目录扫描 | task_id（必填）, action（必填） | 已实现 |
| `create_extension_workspace` | 创建源码草稿工作区 | target_server_id（必填） | 已实现 |
| `submit_extension` | 提交源码与 manifest 快照 | workspace_id（必填）, manifest（必填） | 已实现 |
| `validate_extension` | 静态语法、Schema 与冲突检查 | extension_id（必填） | 已实现 |
| `test_extension` | 生成分级隔离测试计划 | extension_id（必填）, level（可选） | 已实现；native/e2e 返回 not_run |
| `install_extension` | 生成证据绑定发布计划 | extension_id（必填） | 已实现；Abaqus 缺原生证据时拒绝发布 |
| `enable_extension` | 生成启用扩展计划 | extension_id（必填） | 已实现 |
| `disable_extension` | 生成禁用扩展计划 | extension_id（必填） | 已实现 |
| `rollback_extension` | 生成扩展历史版本恢复计划 | extension_id（必填） | 已实现 |
| `list_extensions` | 查询状态、版本及证据 | 无 | 已实现 |
| `export_mcp_configuration` | 导出脱敏迁移模板 | 无 | 已实现 |
| `inspect_migration` | 校验导入并重新定位，不安装 | bundle（必填） | 已实现 |

## 六、审批、安全及维护

安装、连接执行未知代码、客户端配置覆盖、卸载与扩展发布均使用可审查计划；批准来自本地 GUI/CLI，Agent 无法通过管理型协议自行批准。计划绑定摘要、原文件哈希、版本或当前安装状态，消费一次，超时失效。默认 Agent 权限 read_only；本地 approved_only 策略只允许执行已批准计划。没有无限制 Shell 工具。

Python 独立 venv、Node 独立 prefix 不影响其他 MCP；它们不被称为安全沙箱。未知脚本不自动构建执行；Node 禁安装脚本；远程注册不伪装成本地软件安装。包/来源/版本/命令/日志入库，受管目录带所有权标记，外部项目卸载仅解除管理。更新记录与保留版本支持回滚，旧维护计划遇到部署变化拒绝执行。

敏感值使用环境或系统 keyring 引用；日志、协议返回、工具描述回显和迁移模板做脱敏。原配置含秘密时加密备份，密钥必须由原生系统安全存储保管，不存明文密钥文件。加密与回滚测试使用独立临时密钥验证真实算法；本次未向用户系统安全存储写入测试凭据，也不据此声称所有系统凭据后端均验收。没有可用安全存储时拒绝敏感配置覆盖。

扩展 AST 仅为审查线索。Linux bwrap 探测在本机因 namespace 权限失败，保留 blocked；显式本地 Docker fallback 使用不可变已有镜像 ID、禁网、只读根与源码、非特权用户、无 capabilities、资源限制和超时清理。测试未下载镜像或启动镜像自带主服务。正式 Generic Host 是受批准代码进程，不是生产沙箱；可使用操作系统受限账户进一步约束。

## 七、实际验收证据

| 测试 | 结果 | 证据/实际范围 |
|---|---|---|
| 全量代码、协议、Qt、安装与扩展回归 | 通过 88 / 跳过 1 / 失败 0 | runtime/final-acceptance.log；唯一跳过是 bwrap 真实隔离不可用测试 |
| Manager stdio 真实握手、35 工具、只读与拒绝自授权 | 通过 | tests/test_server.py，真实 SDK 子进程 |
| stdio / Streamable HTTP、工具调用、关闭、错误与分页 | 通过 | tests/test_runtime.py，真实本地进程和 HTTP 服务；非互联网远端生产服务认证验收 |
| 最新真实机器静态发现 | 通过，登记 3 个配置 | runtime/acceptance-final/verification.json，320 文件/29目录，非全盘扫描；不同 Abaqus 配置没有按名错误合并 |
| AbaqusMCP 集成 | 通过，107 工具，get_server_info 真实调用 | 同上；仅元数据，不提交求解、不读写 CAE/ODB |
| Qt 原生组件与线程 | 通过 | 单元 GUI 契约 stub 明确标注；实际后端截图另见 runtime/acceptance-final/gui.png |
| 用户部署入口 | 通过 | ~/Apps/MCP-Manager/start 实际启动、截图并退出；应用菜单项已生成 |
| 官方 Registry 网络请求与缓存离线读取 | 通过，实际返回 20 条搜索结果 | runtime/acceptance/real-discovery-protocol-registry.json |
| Python 本地测试 wheel 安装→握手→更新→回滚→卸载 | 通过 | tests/test_registry_installer.py，临时拥有目录；不是模拟包管理器退出码 |
| Codex 配置实际读取 | 通过 | runtime/client-acceptance/client-config-acceptance.json；实际 Codex CLI 读取隔离配置，无用户配置覆盖，不宣称 Agent 已进行模型会话 |
| DSH 配置→工具发现→ping 调用 | 通过 | runtime/client-acceptance/dsh-client-acceptance.json；实际 Cordis/DSH 插件，隔离配置，无用户配置修改 |
| Generic 扩展静态→Docker 测试→发布→发现→调用→禁用/启用→更新/回滚 | 通过 | tests/test_extensions.py，实际隔离容器与实际 MCP Host；另验源码/证据篡改拒载 |
| Abaqus/HyperWorks/ANSYS/CATIA 原生扩展测试 | 未实现/未执行 | 返回 not_run，不能发布需 native 证据的扩展 |
| 完整工业模型建模→求解→ODB 链 | 未执行 | 本管理器不重复实现该软件 API，现有协议元数据验证不能替代求解 |
| Windows 路径分支与迁移模板 | 代码分支测试通过 | Windows 原生平台、磁盘/UNC、PowerShell/CMD、Qt 打包未执行 |
| 软件包构建、语法和依赖 | 通过 | runtime/build-final.log、compile-final.log、dependencies-final.log |

测试指定本地镜像 `sha256:6c9da4d4d236672a1b722f597d643de2202410a2281b7aa4d98fccca3a8f3732`。这是本机验收检测结果，不是对其他电脑的安装要求。无该镜像的默认测试会额外跳过 Docker 集成，不应复制“88/1”的数字为新电脑结果。

## 八、AbaqusMCP 更新到 DSH 的先行任务

用户明确要求先更新 DSH。此前单独完成四个既有 AbaqusMCP DSH 配置作用域的部署修订标记更新，保留启动配置及其他 MCP。原文件备份在 `~/.dsh/backups/abaqus-tool-only-20261106/`。服务保持运行，四个新实例读取新修订；旧会话保留，用户使用新会话刷新工具。实际 DSH 插件发现 107 个工具并完成元数据/预览/参数错误验证，没有执行工业求解。该独立授权修改区别于 Manager 的隔离配置测试。证据在 Abaqus 项目的 `runtime/dsh-tool-only-20261106/`。

## 九、交付使用方式

运行 `mcp-manager gui` 或应用菜单 MCP Manager。CLI 与管理型 MCP 的连接方式见 README 和 USER_MANUAL。发布包 `dist/mcp_manager_desktop-2.0.0-py3-none-any.whl` 与 `.tar.gz` 已生成；依赖需要由目标环境独立安装。不是内含 Python/Qt 的 Windows 独立可执行安装包。

已交付 README、USER_MANUAL、ARCHITECTURE、CORE_API、GUI、EXTENSIONS、MANAGEMENT_TOOLS.json、Linux 依赖记录、部署脚本、CI 定义、测试及示例。runtime 为机器本地验收产物，不纳入源码发行包；注册库及本地路径证据不作为可移植配置发布。

## 十、未完成内容与后续优先级

1. P0：Windows 真机运行发现、UNC/非系统盘、客户端读写与 Qt 测试，补完原生沙箱能力；不能用 Linux 路径单测代替。
2. P0：分别在隔离环境验收 npm/uv/Docker/Git 安装、网络失败/中断恢复及包认证流程；完善安装取消事务和多实例同服务操作协调。
3. P0：加强类型化输出 Schema、更多客户端配置层及 DSH 重名作用域明确定位；补充批量重连和客户端更新后的配置切换。
4. P1：实现工业软件各自的受控原生测试适配器、许可/版本探测与真实对象/求解证据，再开放目标扩展发布；不得放宽门槛假装完成。
5. P1：自动定时刷新、更多运行环境/全局 npm/Docker 已安装入口静态证据、后台资源监控、完整代理/OAuth 与凭据管理交互。
6. P1：系统签名发行包、第二版数据库迁移、跨电脑 Windows/Linux 实机迁移、更多更新源及失败恢复场景。

Agent 负责代码和工程决策，Manager 负责管理与生命周期，普通 MCP 执行软件操作，工业软件负责真实计算。没有在 Manager 中引入大模型或工程规划引擎。

## 十一、2026-11-07 轻量代理实际交付

针对小上下文本地模型工具定义开销，新增独立三工具代理，不移除原管理型35工具接口。完成代码、测试及本机 DSH 部署，不改写 AbaqusMCP。

新增 `gateway_server.py`、`core/gateway.py`、`core/gateway_connection.py`、`tests/test_gateway.py`、`LIGHTWEIGHT_GATEWAY.md`；修改 `cli.py`、`core/manager.py`、`core/runtime.py`、`core/database.py`、`pyproject.toml` 及使用/架构文档。无新增依赖。运行环境中已更新可编辑安装的 `mcp-manager-gateway` 入口。

接口为 search_tools(query,server,offset,limit)、get_tool_details(tool_id)、call_tool(tool_id,arguments,schema_hash)。授权冻结后端启动配置、状态代次及工具白名单；搜索只读缓存，选中后端后持久会话；调用检查最新Schema，保留原错误和 execute 预览语义；大结果写入私有文件。无无限制 Shell、代码生成或工程判断。原配置变更、禁用再启用均需重新本地审批；凭据轮换不暴露旧会话值。

最终完整回归 **105通过、1跳过、0失败**，新增代理17项测试，覆盖真实 stdio 与 HTTP、三工具发现、调用复用、无授权拒绝、Schema变化/参数错误、撤销、凭据脱敏、超时无写入重试、结果文件及有界搜索。唯一跳过为现有 bwrap OS隔离不可用测试。审查发现的授权复活、巨长工具名/搜索输出、会话凭据轮换问题已修复并复核。

真实 DSH Cordis/ToolRuntime/MCP 插件发现3个入口，搜索→详情→真实 Abaqus 元数据及 execute=false 几何预览通过。DSH 工具定义从178128字节降到1565字节，约减少99.12%；不是精确 tokenizer 计数或全部上下文缩减比例。后端保留107个工具。四个现有预设只替换原 Abaqus 连接，用户patch及数据库已备份，比较保留其他配置；通过 DSH 官方函数重建生成根文件。旧对话可能仍保存原工具，需新建对话。

证据在 runtime/gateway/{regression.log,dsh-acceptance.json,dsh-deployment.json}。本轮未执行原生 Abaqus 建模/求解，未触及用户模型；Windows原生运行与GUI代理策略编辑页未实现/未验收。后续可添加GUI策略编辑和目标模型tokenizer实测，继续按需发现而非自动加载全部目录。
