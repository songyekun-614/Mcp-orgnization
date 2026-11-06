# Agent 扩展与批准发布

代码由 Agent 在工作区编写；Manager 负责不可变快照、静态检查、隔离测试、审批、发布与版本管理，不生成业务逻辑。示例 `examples/extensions/math/` 仅提供模块、测试和 manifest，不包含预制通过证据。

`ExtensionService(db, approvals, root)` 的 root 是 Manager data dir 下的 `extensions`，不得指向其他项目。工作区、源码、证据、注册表必须是普通文件；拒绝路径穿越和各级符号链接。每个源码/测试文件最多 1 MiB，提交只复制明确声明文件。修改工作区不影响已提交快照；修改快照会使测试、启用、发布失败。损坏扩展仍可经审批禁用。

API（同步，供 Core 后台线程代理）：

- `workspace(target_server_id)` 返回 `{id,path,target_server_id,status}`；Agent 写入 `module.py` 和 `test_host.py` / `test_native.py` / `test_e2e.py`。
- `submit(workspace_id, manifest)` 返回 extension record。record.id 是 Manager 版本记录 UUID；manifest.id 是稳定扩展名，manifest.version 是显示版本。record 包含 module_sha256、snapshot_sha256、files、snapshot 和 tests。
- `validate(extension_id)` 返回 valid/errors/review_findings；执行 AST 语法、注册入口、JSON Schema、工具名称和冲突、依赖及权限声明检查，不执行模块。危险 API 只是人工审查线索，静态检查不是沙箱。
- `test(extension_id, level='host')` / `plan_test(...)` 只生成 `action=test_extension` 的 pending 计划，绑定完整源码/测试快照和显式配置的不可变容器镜像。
- 本地可信 GUI/CLI 调用 `approvals.approve_local(plan_id)`；Core 调用 `approvals.consume(plan_id, 'test_extension')`，再将返回的 payload 传给 `execute_test(payload)`。后者再次原子领取执行权，拒绝无批准、过期和重放。MCP 不暴露批准接口。
- `plan_install(extension_id,target_registry=None)` 生成 `action=install_extension`。Core 消费批准后调用 `install(payload)`。target_registry 仅可为 Manager 自己的 `<root>/installed/<target_server_id>/registry.json`，不能写其他项目。
- `enable(extension_id)`、`disable(extension_id)`、`rollback(extension_id)` 都只生成 `action=extension_state` 计划；Core 消费批准后调用 `execute_state(payload)`。rollback 的参数是要恢复的历史 UUID；每个历史版本保留独立模块和证据。停用旧的已被替换版本不会误停当前版本。

manifest 固定字段为 `id,version,target,module,tools,dependencies,permissions,tests`。target 为 generic / abaqus；module 固定 module.py。tools 为 name/input_schema/output_schema 对象列表，最多32个。dependencies 是包名/版本声明，拒绝 URL/命令，不会自动安装依赖。permissions 允许 read_workspace/write_workspace/native_software/network；generic Host-only 发布仅允许前两者或空列表。tests 将级别映射到固定文件名 `test_<level>.py`。声明依赖不代表该沙箱已经安装依赖；实际测试缺少依赖则失败。

## 真正的 OS 隔离

Linux 默认使用 bubblewrap：独立 user/pid/network 等命名空间、只读系统/解释器/测试源码、仅临时工作区可写、丢弃 capabilities、隔离进程和环境变量。prlimit 限制 CPU/内存/进程/文件/文件描述符，外层20秒超时终止进程组。先运行固定可信探测，沙箱创建失败标 blocked，不执行 Agent 代码。没有 venv 降级路径。

当操作者显式设置 `db.set_setting('extension_sandbox_image', 'sha256:<64位小写摘要>')` 时，bubblewrap 无法建立隔离后才尝试 Docker。镜像必须已在本地，image inspect 必须匹配 ID，`--pull=never`，不自动安装 Docker、不下载镜像。批准计划明确列出该 ID。Docker 测试禁网络、只读根、丢弃所有 capabilities、no-new-privileges、65534非特权用户、64进程/256MiB/1CPU、CPU及文件大小限制，源码只读挂载，临时 `/work` 可写、`/tmp` 只读。镜像声明的其他 volumes 用只读 tmpfs 覆盖，不创建隐式可写匿名卷。剥离镜像和宿主环境值，不挂载 home、docker.sock 或其他工程；结束或超时强制清理测试容器。只尝试固定 Python 解释器，不启动镜像主服务。安装无关依赖及许可证不属于此步骤。

只有真实执行的退出码0可生成 host passed；记录实际 sandbox、容器镜像、解释器、源码与测试快照哈希、退出码、超时和脱敏 stdout/stderr。stdout/stderr读取上限64KiB；文件写入限制1MiB。任何不可用沙箱均 blocked；Windows 未接入可验证沙箱时不会执行。native/e2e 当前尚无专用软件隔离与结果核验适配器，批准计划消费后仍返回 not_run。Python 测试脚本不能充当原生软件、许可证、CAE/ODB或工程结果证据。

## Generic MCP Host

generic 低风险扩展可依据真实 Host passed 和本地批准发布。`install` 返回 `launch_suggestion`：

```text
<Manager Python> -m mcp_manager.extension_host --registry <root>/installed/<server_id>/registry.json
```

这是独立 stdio MCP Server，由 Core 后续注册/启动管理。Agent 不获得任意 shell 工具。模块使用同步 `register(registry)`，通过 `@registry.tool(name, description)` 注册有类型的 async 函数，返回JSON对象。参数必须显式命名且有类型，不能使用 *args/**kwargs；签名参数和必填集合必须与 manifest Schema 一致。输入顶层固定拒绝额外属性，调用时同时校验输入、输出 Schema。示例中的 ext_add 可用标准 tools/list 和 tools/call 使用。

Generic registry 的条目在 id/module/sha256/tools/tests 基础上增加 `manifest:{file,sha256}`，tests 仅有 host。Host 首先校验所有注册表、模块、manifest、证据与源码摘要，检查 host 确实为认可 OS 沙箱的 passed，再执行模块并注册允许名单内工具。未列入 registry 即未启用；每次发现/调用还复查所有摘要，发现修改则拒绝并要求重启。启动不热加载，不跨服务自动复制工具。annotations 是客户端提示，不是权限机制。

**生产运行是受信任代码加载，不是沙箱。** manifest 的权限声明与 Host-only 发布策略用于操作者审查，不在生产进程中强制限制Python API；批准的模块拥有独立 Host 进程的操作系统权限。隔离测试不证明代码无恶意。应使用受限的运行账户和目录权限，并由本地操作者审查完整计划与源码。

## Abaqus 兼容

不修改 Abaqus 源码。发布要求真实 host 和 native 都为 passed，目前缺少原生适配器会阻止发布。通过后生成现有 schema_version=1 注册表，条目严格包含 id/module/sha256/tools/tests，tests 恰有 host/native，各证据包含 status=passed、level、module_sha256。模块和证据是注册表目录的直接普通子文件。安装只返回 `configuration_suggestion.env.ABAQUS_EXTENSION_REGISTRY`，不替用户修改目标 ServerSpec，不热加载；安全重启由 Core 单独管理。

## 验证命令

普通回归不需要 Docker。真实 Docker 闭环须操作者显式选择本地镜像：

```bash
MCP_MANAGER_TEST_SANDBOX_IMAGE=sha256:<本地镜像ID> /path/to/python -m pytest tests/test_extensions.py -q -rs
```

集成测试真实执行隔离脚本，再批准发布、启动标准 MCP、发现/调用工具、拒绝多余参数、禁用/启用、安装新版本及回滚旧版本，并检查篡改拒载；不制造 host/native 通过证据。
