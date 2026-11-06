"""Management MCP facade; approval is deliberately absent from this protocol."""
from functools import partial
import json
from typing import Annotated,Any,Literal

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from .core.security import redact

Limit=Annotated[int,Field(strict=True,ge=1,le=100)]
Index=Annotated[int,Field(strict=True,ge=0)]


class ManagerMCP(FastMCP):
    async def list_tools(self):
        tools=await super().list_tools()
        for tool in tools:tool.inputSchema['additionalProperties']=False
        return tools
    async def call_tool(self,name,arguments):
        tool=next((t for t in await self.list_tools() if t.name==name),None)
        if tool is None:raise ToolError(json.dumps({'status':'failed','error':{'type':'OBJECT_NOT_FOUND','operation':name}}))
        if set(arguments or {})-set(tool.inputSchema.get('properties',{})):
            raise ToolError(json.dumps({'status':'failed','error':{'type':'INVALID_PARAMETER','operation':name,'message':'Unexpected arguments'}}))
        try:return await super().call_tool(name,arguments or {})
        except Exception as exc:
            current=exc
            while current.__cause__ is not None:current=current.__cause__
            code='PERMISSION_DENIED' if isinstance(current,PermissionError) else 'OBJECT_NOT_FOUND' if isinstance(current,(KeyError,FileNotFoundError)) else 'INVALID_PARAMETER' if isinstance(current,(ValueError,TypeError)) else 'OPERATION_FAILED'
            raise ToolError(json.dumps({'status':'failed','error':{'type':code,'operation':name,'original_exception':type(current).__name__,'message':redact(str(current))}},ensure_ascii=False)) from exc


def create_server(core,host='127.0.0.1',port=8791):
    mcp=ManagerMCP('MCP Manager',host=host,port=port,instructions='Discover and manage MCP deployments. Read-only by default. Mutation tools return reviewable pending plans. Only the local GUI/CLI can approve a plan. No model, shell or code generation engine is built in. Windows and Linux source adapters do not imply native Windows validation.')
    read=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
    plan=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=False)
    async def invoke(method,*args,**kwargs):
        result=await anyio.to_thread.run_sync(partial(method,*args,**kwargs))
        if isinstance(result,dict):return result
        return {'status':'completed','items':result}
    @mcp.tool(annotations=read)
    async def detect_platform()->dict[str,Any]:
        """Read platform, runtime executables, user directories and WSL/native distinction. Does not install or execute discovered MCPs."""
        return await invoke(core.detect_platform)
    @mcp.tool(annotations=plan)
    async def discover_mcp(roots:list[str]|None=None,mode:Literal['quick','standard']='quick',max_depth:Annotated[int,Field(ge=0,le=32)]=4)->dict[str,Any]:
        """Start bounded static discovery. Reads client configuration and source metadata; never executes candidates. Poll get_manager_task. Deep scans require a separate locally approved plan."""
        return await invoke(core.start_task,'discover',{'roots':roots,'mode':mode,'max_depth':max_depth})
    @mcp.tool(annotations=read)
    async def list_installed_mcp()->dict[str,Any]:
        """List registered configurations, installation ownership and separate validation facts. Registration is not evidence of a working industrial software connection."""
        return {'status':'completed','servers':await invoke_list(core.list_servers)}
    async def invoke_list(method):return await anyio.to_thread.run_sync(method)
    @mcp.tool(annotations=read)
    async def get_mcp_info(server_id:str)->dict[str,Any]:
        """Read one local registry entry and its provenance, clients and validation evidence."""
        return await invoke(core.get_server,server_id)
    @mcp.tool(annotations=read)
    async def list_mcp_tools(server_id:str,offset:Index=0,limit:Limit=50)->dict[str,Any]:
        """Read paginated cached tool schemas from an actual previous handshake; does not launch a server."""
        return await invoke(core.list_tools,server_id,offset,limit)
    @mcp.tool(annotations=read)
    async def get_mcp_status(server_id:str)->dict[str,Any]:
        """Read owned connection status and last verification separately; external PIDs are never taken over."""
        return await invoke(core.get_status,server_id)
    @mcp.tool(annotations=read)
    async def get_mcp_logs(server_id:str|None=None)->dict[str,Any]:
        """Read bounded redacted Manager audit/task logs and optional owned connection log."""
        return await invoke(core.get_logs,server_id)
    @mcp.tool(annotations=read)
    async def search_mcp_registry(query:str,cursor:str|None=None,offline:bool=False)->dict[str,Any]:
        """Search official Registry JSON API with cache. Registry publishing is not a security certification; search requires >=3 characters."""
        return await invoke(core.search_registry,query,cursor,offline)
    @mcp.tool(annotations=read)
    async def get_mcp_install_options(name:str|None=None,version:str='latest',offline:bool=False)->dict[str,Any]:
        """Read Registry package/remote metadata or supported installer options; never executes packages."""
        if name:return await invoke(core.registry_detail,name,version,offline)
        return {'status':'completed','kinds':['python','node','docker','git','remote'],'requires_local_approval':True,'python_local_format':'prebuilt wheel','version_policy':'explicit pinned version/commit/digest'}
    @mcp.tool(annotations=plan)
    async def register_mcp(spec:dict[str,Any])->dict[str,Any]:
        """Register a strict launch configuration without executing it. Credential values should be environment/keyring references."""
        return await invoke(core.register_server,spec)
    @mcp.tool(annotations=plan)
    async def install_mcp(request:dict[str,Any])->dict[str,Any]:
        """Generate a source/version/command/path installation plan. Does not install until independently approved locally and executed."""
        return await invoke(core.plan_install,request)
    @mcp.tool(annotations=plan)
    async def configure_mcp_client(client:Literal['codex','dsh'],path:str,name:str,server_id:str|None=None,remove:bool=False)->dict[str,Any]:
        """Generate an atomic, hash-bound target-only client configuration plan. Original configuration is not written before local approval."""
        return await invoke(core.plan_client_change,client,path,name,server_id,remove)
    @mcp.tool(annotations=plan)
    async def test_mcp_connection(server_id:str,tool_name:str|None=None,arguments:dict[str,Any]|None=None)->dict[str,Any]:
        """Plan a real SDK connection/handshake/tool discovery and optional explicitly selected tool call. Starting unknown code requires local approval."""
        return await invoke(core.plan_connection,server_id,tool_name,arguments)
    @mcp.tool(annotations=plan)
    async def enable_mcp(server_id:str)->dict[str,Any]:
        """Plan enabling the Manager entry; client settings require their own target-only configuration plan."""
        return await invoke(core.plan_action,'enable',server_id)
    @mcp.tool(annotations=plan)
    async def disable_mcp(server_id:str)->dict[str,Any]:
        """Plan disabling the Manager entry. Does not silently rewrite all client configurations."""
        return await invoke(core.plan_action,'disable',server_id)
    @mcp.tool(annotations=plan)
    async def control_mcp(server_id:str,action:Literal['start','stop','restart','unmanage'])->dict[str,Any]:
        """Plan an owned connection lifecycle action. stdio is normally client-owned on demand; external processes are not forcefully adopted."""
        return await invoke(core.plan_action,action,server_id)
    @mcp.tool(annotations=read)
    async def check_mcp_updates(server_id:str)->dict[str,Any]:
        """Read package version metadata; no automatic update or server execution."""
        return await invoke(core.check_updates,server_id)
    @mcp.tool(annotations=plan)
    async def update_mcp(server_id:str,request:dict[str,Any])->dict[str,Any]:
        """Plan a version-pinned isolated replacement, retaining previous managed deployments for rollback."""
        return await invoke(core.plan_action,'update',server_id,request=request)
    @mcp.tool(annotations=plan)
    async def rollback_mcp(server_id:str)->dict[str,Any]:
        """Plan returning a managed deployment to a recorded previous version."""
        return await invoke(core.plan_action,'rollback',server_id)
    @mcp.tool(annotations=plan)
    async def uninstall_mcp(server_id:str)->dict[str,Any]:
        """Plan deletion only of Manager-owned deployments. External projects are merely unmanaged."""
        return await invoke(core.plan_action,'uninstall',server_id)
    @mcp.tool(annotations=plan)
    async def execute_approved_plan(plan_id:str)->dict[str,Any]:
        """Execute a single-use locally approved plan only under approved_only Agent policy. This tool cannot approve plans."""
        permission=core.db.get_setting('agent_permission','read_only')
        if permission!='approved_only':raise PermissionError('Agent execution is disabled; select approved_only in local CLI permissions after review')
        return await invoke(core.start_task,'execute_plan',{'plan_id':plan_id})
    @mcp.tool(annotations=read)
    async def get_manager_task(task_id:str)->dict[str,Any]:
        """Read an asynchronous Manager task and its actual completion/error state."""
        return await invoke(core.task_status,task_id)
    @mcp.tool(annotations=read)
    async def list_manager_tasks()->dict[str,Any]:
        """Read recent persisted installation, scan and validation tasks."""
        return {'status':'completed','tasks':await invoke_list(core.list_tasks)}
    @mcp.tool(annotations=plan)
    async def control_scan(task_id:str,action:Literal['pause','resume','cancel'])->dict[str,Any]:
        """Pause, resume or cancel a bounded directory scan. Other operations do not pretend to support interrupt-safe cancellation."""
        return await invoke(core.control_task,task_id,action)
    @mcp.tool(annotations=plan)
    async def create_extension_workspace(target_server_id:str)->dict[str,Any]:
        """Create a private draft directory for Agent-authored source; does not execute it."""
        return await invoke(core.extensions.workspace,target_server_id)
    @mcp.tool(annotations=plan)
    async def submit_extension(workspace_id:str,manifest:dict[str,Any])->dict[str,Any]:
        """Snapshot declared source, schemas, dependencies, permissions and tests with content hashes."""
        return await invoke(core.extensions.submit,workspace_id,manifest)
    @mcp.tool(annotations=plan)
    async def validate_extension(extension_id:str)->dict[str,Any]:
        """Perform syntax/schema/dependency/conflict checks; AST danger hints are not a sandbox or security certificate."""
        return await invoke(core.extensions.validate,extension_id)
    @mcp.tool(annotations=plan)
    async def test_extension(extension_id:str,level:Literal['host','native','e2e']='host')->dict[str,Any]:
        """Plan an OS-isolated extension test. Missing sandbox/native adapter produces blocked/not_run, never a simulated pass."""
        return await invoke(core.extensions.test,extension_id,level)
    @mcp.tool(annotations=plan)
    async def install_extension(extension_id:str)->dict[str,Any]:
        """Plan publication only after required tests match the exact source; installation requires local approval and restart."""
        return await invoke(core.extensions.plan_install,extension_id)
    @mcp.tool(annotations=plan)
    async def enable_extension(extension_id:str)->dict[str,Any]:
        """Plan enabling a previously tested and installed extension; restart/reconnect required."""
        return await invoke(core.extensions.enable,extension_id)
    @mcp.tool(annotations=plan)
    async def disable_extension(extension_id:str)->dict[str,Any]:
        """Plan removing an extension from startup discovery; does not inject code into live sessions."""
        return await invoke(core.extensions.disable,extension_id)
    @mcp.tool(annotations=plan)
    async def rollback_extension(extension_id:str)->dict[str,Any]:
        """Plan restoring a specific retained, tested extension snapshot and restart discovery."""
        return await invoke(core.extensions.rollback,extension_id)
    @mcp.tool(annotations=read)
    async def list_extensions()->dict[str,Any]:
        """Read submitted/validated/tested/installed extension states and evidence."""
        return {'status':'completed','extensions':await invoke_list(lambda:core.db.list_records('extensions'))}
    @mcp.tool(annotations=read)
    async def export_mcp_configuration()->dict[str,Any]:
        """Export portable nonsecret templates, omitting private absolute paths and engineering data."""
        return await invoke(core.export_configuration)
    @mcp.tool(annotations=plan)
    async def inspect_migration(bundle:dict[str,Any])->dict[str,Any]:
        """Validate an import and locate existing packages; never install or overwrite configuration automatically."""
        return await invoke(core.import_configuration,bundle)
    return mcp


def main():
    from .cli import main as cli
    import sys
    return cli(['serve',*sys.argv[1:]])
