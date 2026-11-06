# 中文用户手册

## 启动及发现

启动 `mcp-manager gui`。本机已创建应用菜单项 MCP Manager 和 `~/Apps/MCP-Manager/start`，使用专用 venv。首次仪表盘为实际注册库统计，无演示服务器。

在已安装页面运行快速扫描，读取 Codex/DSH 配置和静态证据；标准扫描允许选择项目目录。在设置页保存多个扫描根、镜像、主题和 Registry 来源。深度扫描必须明确选择目录与界限并审查计划。扫描支持暂停、继续、取消，忽略常见依赖和构建目录，默认不追踪子目录符号链接。自选根目录若为符号链接，会先解析该根；不会据此跟随其所有子链接。

候选项目可能尚未安装，缺少有效启动入口时不会假定可运行。Python/Node/Docker 可执行程序存在也不等于 MCP 已安装。发现不会运行 npx/uvx 下载或执行未知源码。

## 查看和验证

选中服务可查看配置、来源、最近验证和工具 Schema。静态发现的工具数量可为零。选择连接测试会生成确切启动命令或远程端点的计划；确认后实际握手并缓存工具。测试完成关闭临时会话，因此“当前连接”可为零而“最近验证”成功。指定工具调用可能影响外部软件，必须审查参数。

stdio 一般由 Agent 客户端按需启动。启动/停止/重启按钮只维护管理器自己持有的会话，不会强行关闭其他客户端启动的 Abaqus 或 MCP。HTTP 远程服务不能在本机卸载或终止远程进程。

## Registry 与安装

在 Discover MCP 搜索至少三个字符的关键词。页面读取官方 JSON API，显示发布来源与版本；登记不是安全认证。缓存支持离线浏览。通过包/版本/Transport 元数据选择安装请求，再查看计划里的命令、独立目录和权限。

Python 支持固定版本 pip/uv 和预构建本地 wheel；Node 使用独立 npm prefix 并禁用安装脚本；Docker 使用固定镜像引用，不隐式开放主机挂载；Git 只克隆固定提交，不自动执行仓库构建脚本；远程接入单独登记 HTTPS 端点。Git 后续构建、缺少依赖或需要认证时会要求用户另行处理，不伪造安装验证。

CLI 审批示例：

```bash
mcp-manager plan-install request.json
mcp-manager plan PLAN_ID
mcp-manager approve PLAN_ID
mcp-manager execute PLAN_ID
```

计划 ID 取实际返回值，不复制示例占位符。CLI 交互确认需要输入 yes；`--yes` 只供已完成审查的本地操作。安装完成后连接测试是独立计划，不会自动执行刚安装的未知服务。失败不会无限重试。取消扫描不代表可以中断任意安装事务。

## 客户端配置

Clients 显示检测到的 Codex/DSH 配置路径。选择目标服务、配置文件和条目名称，预览后添加、修改或删除该条目。写入前检查当前文件，保留其他设置和注释，生成备份并原子替换。检测到预览后文件变化则拒绝执行，重新生成计划。

Codex 使用自身 TOML 格式；DSH 使用 Cordis YAML，`!!js` 仅作为文本处理，不求值。如果一个 DSH 文件多个作用域有重复 serverName，当前适配器拒绝不明确的修改，需明确调整配置结构或使用 DSH 自身能力，不能覆盖四个作用域碰运气。已有明文凭据备份依赖系统 keyring，未解锁时先处理安全存储。引用环境变量需在客户端实际启动环境可见；管理器不会把原配置密钥复制进注册库。

添加后按客户端支持方式重连或新建会话；工具缓存未刷新时重新启动对应客户端会话。管理器不会注入已运行的 Agent。

## Agent 管理接口

用当前 venv 的 `mcp-manager-server` 作为 stdio 启动命令。管理工具输入输出 Schema 见 MANAGEMENT_TOOLS.json。默认只允许查询、登记和产生计划，拒绝执行变更。用户在本地设置 approved_only 并批准确切计划后，Agent 才能调用 execute_approved_plan；后台执行返回任务 ID，随后 get_manager_task 查询。协议本身没有 approve 工具。

## 扩展

Extension Center 支持工作区、提交、静态检查、测试、安装/启停/回滚，详细 manifest 和代码示例见 EXTENSIONS.md。代码由 Agent 编写，管理器不生成代码。测试须真实 OS 隔离；系统未提供沙箱时显示 blocked。当前工业软件 native/e2e 级别显示 not_run。不要将 Host 通过或工具定义成功解释为 Abaqus 原生求解通过。

Generic 扩展发布返回独立 Host 的启动建议，注册该启动配置后可用标准 MCP 发现和调用。更新、启停与回滚后重启/重连，不热注入。正式 Host 是获批准代码进程，需使用受限运行账户；权限声明不等于运行时强制沙箱。

## 迁移、备份及故障

`mcp-manager export` 输出不含秘密的模板，绝对路径成为待重新定位项。导入仅检验目标环境并生成匹配/安装建议，不跨系统替换斜杠或直接安装。

`mcp-manager backup-db /path/to/backup.sqlite` 备份注册数据库；通过 plan-restore-db 生成恢复计划。先停止管理器任务和持有连接再恢复。Monitoring 和 Installation Center 显示实际状态及脱敏错误，无许可证、程序不存在、依赖缺失和协议握手失败应分别处理，不将进程启动当作工具调用成功。
