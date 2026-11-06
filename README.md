# MCP Manager 2.0

供用户与 Agent 共用的 MCP 管理工具：Python Core、Qt 桌面界面和标准管理型 MCP Server。独立运行，不内置大模型，不实现工业软件 API。

已实现有界静态发现、本地 SQLite 注册、stdio / Streamable HTTP 握手与工具查询、官方 Registry JSON 搜索及离线缓存、审批安装和客户端配置、Generic 扩展隔离测试与版本发布。当前 Linux 已执行验收；Windows 提供适配和部署脚本，原生测试未执行。工业软件扩展的原生测试适配器尚未实现，不能将 Generic 测试视为 Abaqus 求解验证。

## 安装与运行

需要 Python 3.11+；GUI 使用 PySide6。先查看项目源码与依赖，再在自己的独立环境安装：

```bash
python3 -m venv ~/venvs/mcp-manager
~/venvs/mcp-manager/bin/python -m pip install '.[gui,dev]'
~/venvs/mcp-manager/bin/mcp-manager gui
```

Windows PowerShell 可运行 `scripts/install-windows.ps1`；Linux 可运行 `scripts/install-linux.sh`。脚本安装到用户目录，不要求 root。镜像可通过 pip 标准配置设置；Manager 中的新 MCP 安装镜像在设置页单独配置。

```bash
mcp-manager detect
mcp-manager discover --mode standard /path/to/projects
mcp-manager list
mcp-manager search filesystem
mcp-manager search filesystem --offline
mcp-manager serve
```

`--data-dir /path/to/data` 必须放在 CLI 子命令之前（serve/gui 也接受子命令之后）。默认数据目录由 XDG 或 Windows LOCALAPPDATA 决定，不绑定开发电脑路径。

## 连接 Agent

客户端启动命令：该环境内的 `mcp-manager-server`，无参数即 stdio。也可使用该环境 Python 加参数 `-m mcp_manager serve`。设置完整可执行文件路径，避免客户端使用错误的 Python。

管理型 MCP 默认拒绝 Agent 执行变更；Agent 可以查询和提交计划。本地 GUI/CLI 审查确切命令、来源、版本、目录及权限后批准。若希望 Agent 执行已批准的计划，在本地设置 `mcp-manager permissions approved_only`。没有协议级自授权工具。stdio 通常由客户端按需启动；GUI 的持有连接属于额外的显式操作。

## 文档与验收

- [中文用户手册](docs/USER_MANUAL.md)
- [架构与边界](docs/ARCHITECTURE.md)
- [共享 Core API](docs/CORE_API.md)
- [管理工具完整 Schema](docs/MANAGEMENT_TOOLS.json)
- [扩展开发及隔离](docs/EXTENSIONS.md)
- [GUI 使用说明](docs/GUI.md)
- [开发报告与实际测试范围](docs/MCP_MANAGER_DEVELOPMENT_REPORT.md)

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q -rs
python -m build
```

无可用 OS 沙箱时扩展测试明确 blocked/skip；虚拟环境只隔离依赖。Docker 测试须本地操作者显式指定已存在的不可变镜像 ID，详见扩展文档。所有验收区分协议握手、工具发现、实际调用与原生软件计算。
