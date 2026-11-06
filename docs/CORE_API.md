# GUI / MCP 共用 Core API

模块 `mcp_manager.core.manager`，类 `ManagerCore(data_dir:Path|None=None, home:Path|None=None, environ:dict|None=None)`。
同步方法供GUI工作线程和MCP anyio.to_thread使用：

- detect_platform()->dict
- discover(roots:list[str]|None=None,mode='quick',**limits)->dict：静态发现并入库；deep须先生成scan授权计划。
- list_servers()->list[dict]：records含id,spec{name,command,args,transport,url,version,description,env_refs},enabled,managed,sources,validation{level,tools,tool_count,error}。
- get_server(server_id)->dict
- dashboard()->dict：registered,connected,tools,updates,recent_errors,recent_tasks。
- register_server(spec:dict,source:dict|None=None)->record：只入库，不执行、不安装。
- search_registry(query,cursor=None,offline=False)->dict
- plan_connection(server_id,tool_name=None,arguments=None)->plan：执行明确命令/远程连接或可选工具调用之前的授权计划。
- plan_install(request:dict)->plan
- plan_client_change(client,path,name,server_id=None,remove=False)->plan
- plan_action(action:str,server_id:str,**kwargs)->plan：enable/disable/start/stop/restart/update/rollback/uninstall/unmanage。
- approve_plan(plan_id)->dict：仅本地GUI/CLI调用；MCP不暴露。
- execute_plan(plan_id)->dict：消费一次性批准后统一执行。
- list_clients()->dict：仅配置路径/状态和脱敏连接摘要。
- list_tasks()->list；task_status(task_id)->dict；start_task(operation,parameters)->dict（operation白名单：discover,execute_plan,search_registry）
- get_logs(server_id=None)->dict：脱敏审计和对应任务日志。
- get_settings()->dict；save_settings(values)->dict（扫描根/主题等非敏感设置）。
- export_configuration()->dict：可迁移模板，默认剔除秘密和私有绝对路径。
- import_configuration(bundle)->dict：验证和候选计划，不直接安装/覆盖。
- extensions: ExtensionService对象，workspace/submit/validate/test/plan_install/enable/disable/rollback方法由Core代理授权。

计划record含 id,action,payload,digest,state(pending/approved/consumed),expires。客户端完整 after_text 不写入 SQLite 或计划文件；仅保存目标条目、脱敏预览、前后摘要。执行前重新读取并计算目标修改，核对原配置和生成内容的 SHA256。

GUI在后台线程执行discover/execute_plan/search，所有UI变更经Qt信号；页内错误可见。主线程仅快速读DB或启动任务。
