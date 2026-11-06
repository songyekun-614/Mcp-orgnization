# 原生 Qt 桌面工作台

入口：`mcp-manager gui`（由项目脚本定义）或 `python -m mcp_manager.gui.app`。

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src /path/to/venv/bin/python -m mcp_manager.gui.app --data-dir /tmp/mcp-manager-smoke --smoke-test --screenshot /tmp/mcp-manager.png
```

`--data-dir` 应用于临时验收数据库，截图启动不扫描、不连接服务、不修改客户端。`--screenshot` 会截图并退出，`--smoke-test` 仅启动并退出。窗口最低 880×640，可自由调整，Qt 自动处理 DPI；主题支持浅色与深色。

八页：总览、已安装服务、发现服务、安装中心、客户端、扩展中心、监控与日志、设置。指标来自 `dashboard()`，服务与工具来自 `list_servers()`，客户端和日志展示 Core 提供的脱敏结果。底部记录运行状态和错误，无模拟成功路径。

## Core 依赖

同步本地读取：`dashboard/list_servers/list_clients/list_tasks/get_logs/get_settings`。执行型与可能耗时的方法统一由 QThreadPool 运行，通过 Qt 信号回主线程更新控件：`discover/search_registry/plan_connection/plan_action/plan_install/plan_client_change/execute_plan/save_settings/export_configuration/import_configuration`。

批准流程：后台创建计划 → `QMessageBox` 展示计划及可展开的完整脱敏详情 → 默认取消 → 用户点击“批准并执行” → 本地主线程 `approve_plan(id)` → 后台 `execute_plan(id)`。取消不会调用批准或执行。

深度扫描需要 `plan_scan(roots=...,max_depth=8,max_files=50000,max_seconds=60)`；若 Core 没有该方法，界面明确报告不支持，不直接运行深度扫描。扩展按钮只调用存在的 `core.extensions` 方法，未实现会明确报告不支持；扩展 JSON 参数原样交给 Core 校验，返回 pending 计划走同一批准流程。界面不实现隔离、协议、安装或授权业务规则。

安装页请求包含 `kind/package/name/version`；高级 JSON 支持 `module/local_path/executable/commit/args` 等 Core 参数。Git 来源映射到 `repository`。实际支持的来源与请求参数以 Core 安装适配器为准。配置导入只展示候选结果，不自动批准。设置包含 `theme/scan_roots/mirror/registry_url/agent_permission/extension_sandbox_image`。

## 验证

```bash
QT_QPA_PLATFORM=offscreen /path/to/venv/bin/python -m pytest -q tests/test_gui.py
```

界面测试的 ReadOnlyCore 是明确的契约 stub，仅验证导航、数据映射、线程调度、批准和取消，不声称真实后端已运行。真实临时 SQLite 集成由 Core 集成测试补充。测试销毁窗口时必须在 QApplication 仍存在期间显式处理 Qt 对象；应用入口也执行同样销毁顺序，避免 offscreen 环境退出崩溃。
