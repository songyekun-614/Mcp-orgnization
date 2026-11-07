"""Real MCP backends exercise discovery, forwarding and the local grant boundary."""
import asyncio
import json
import os
from pathlib import Path
import sys
import pytest
from mcp_manager.core.manager import ManagerCore


@pytest.fixture
def backend(tmp_path):
    path=tmp_path/'backend.py'
    path.write_text('''import os
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult,TextContent
m=FastMCP('Gateway fixture')
@m.tool()
def create_material(name:str,value:float=1)->dict[str,object]:
    """Create a named material; explicit protocol fixture, not Abaqus."""
    return {'name':name,'value':value,'pid':os.getpid()}
@m.tool()
def echo_private()->dict[str,object]:
    return {'value':os.environ.get('PRIVATE_KEY','unused')}
@m.tool()
def large()->dict[str,object]:
    return {'task_id':'fixture-task','cae_path':'fixture.cae','rows':['x'*100 for _ in range(500)]}
@m.tool()
def change_schema()->dict[str,object]:
    m._tool_manager.get_tool('create_material').parameters['properties']['name']['minLength']=20
    return {'changed':True}
@m.tool()
async def slow()->dict[str,object]:
    import asyncio
    from pathlib import Path
    counter=Path(__file__).with_name('slow-count')
    counter.write_text(str(int(counter.read_text())+1 if counter.exists() else 1))
    await asyncio.sleep(5)
    return {'done':True}
@m.tool()
def rejected()->CallToolResult:
    return CallToolResult(isError=True,content=[TextContent(type='text',text='native fixture rejection')],structuredContent={'error':{'type':'INVALID_PARAMETER','message':'native fixture rejection'}})
if __name__=='__main__':m.run()
''')
    core=ManagerCore(tmp_path/'data',home=tmp_path,environ={'SOURCE_SECRET':'private-gateway-marker-391'})
    row=core.register_server({'name':'demo','command':sys.executable,'args':[str(path)],'env_refs':{'PRIVATE_KEY':'SOURCE_SECRET'}})
    yield core,row
    core.close()


def authorize(core,row,**kwargs):
    plan=core.plan_gateway([row['id']],**kwargs)
    core.approve_plan(plan['id'])
    return core.execute_plan(plan['id'])


def test_policy_requires_local_approval_and_no_implicit_backend(backend):
    core,row=backend
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            assert (await gateway.search_tools('material'))['tools']==[]
    asyncio.run(exercise())
    plan=core.plan_gateway([row['id']])
    with pytest.raises(PermissionError):core.execute_plan(plan['id'])
    core.db.update_server(row['id'],{'spec':dict(row['spec'],args=['changed.py'])})
    core.approve_plan(plan['id'])
    with pytest.raises(ValueError):core.execute_plan(plan['id'])


def test_search_is_brief_details_are_specific_and_connection_is_reused(backend):
    core,row=backend;authorize(core,row)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            found=await gateway.search_tools('创建材料',limit=2)
            assert found['tools'][0]['tool_id']=='demo/create_material'
            assert 'inputSchema' not in json.dumps(found)
            detail=await gateway.get_tool_details('demo/create_material')
            assert detail['input_schema']['required']==['name']
            result=await gateway.call_tool('demo/create_material',{'name':'Steel'},detail['schema_hash'])
            again=await gateway.call_tool('demo/create_material',{'name':'Other'},detail['schema_hash'])
            assert result.structuredContent['result']['pid']==again.structuredContent['result']['pid']
            assert again.structuredContent['result']['name']=='Other'
    asyncio.run(exercise())


def test_bad_arguments_stale_schema_unknown_tool_and_changed_launch_are_denied(backend):
    core,row=backend;authorize(core,row)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            details=await gateway.get_tool_details('demo/create_material')
            with pytest.raises(ValueError):await gateway.call_tool('demo/create_material',{'name':'Steel'},'wrong')
            with pytest.raises(ValueError):await gateway.call_tool('demo/create_material',{'oops':1},details['schema_hash'])
            with pytest.raises((KeyError,PermissionError)):await gateway.get_tool_details('demo/unknown')
            core.db.update_server(row['id'],{'spec':dict(row['spec'],cwd=str(Path(core.data_dir)))})
            with pytest.raises(PermissionError):await gateway.call_tool('demo/create_material',{'name':'Steel'},details['schema_hash'])
    asyncio.run(exercise())


def test_tool_allowlist_and_disabled_server_are_enforced(backend):
    core,row=backend;authorize(core,row,tool_allowlist={row['id']:['create_material']})
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            assert len((await gateway.search_tools(''))['tools'])==1
            with pytest.raises(PermissionError):await gateway.get_tool_details('demo/large')
            core.db.update_server(row['id'],{'enabled':False})
            with pytest.raises(PermissionError):await gateway.get_tool_details('demo/create_material')
    asyncio.run(exercise())


def test_backend_error_redaction_and_large_result_file(backend):
    core,row=backend;authorize(core,row,result_limit_bytes=2048)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            detail=await gateway.get_tool_details('demo/rejected')
            error=await gateway.call_tool('demo/rejected',{},detail['schema_hash'])
            assert error.isError and error.structuredContent['result']['error']['type']=='INVALID_PARAMETER'
            detail=await gateway.get_tool_details('demo/echo_private')
            echo=await gateway.call_tool('demo/echo_private',{},detail['schema_hash'])
            assert 'private-gateway-marker-391' not in echo.model_dump_json()
            detail=await gateway.get_tool_details('demo/large')
            large=await gateway.call_tool('demo/large',{},detail['schema_hash'])
            data=large.structuredContent
            assert data['truncated'] is True and data['summary']['task_id']=='fixture-task'
            assert Path(data['output_file']).is_relative_to(core.data_dir)
            assert Path(data['output_file']).is_file()
            assert len(json.dumps(data,ensure_ascii=False).encode())<=2048
            assert len(Path(data['output_file']).read_bytes())>2048
    asyncio.run(exercise())


def test_gateway_exposes_only_three_strict_small_tools(backend):
    core,_=backend
    from mcp_manager.gateway_server import create_gateway_server
    tools=asyncio.run(create_gateway_server(core).list_tools())
    assert {t.name for t in tools}=={'search_tools','get_tool_details','call_tool'}
    assert all(t.inputSchema['additionalProperties'] is False for t in tools)
    assert len(json.dumps([{'name':t.name,'description':t.description,'inputSchema':t.inputSchema} for t in tools]).encode())<6000


def test_real_gateway_stdio_protocol_and_backend_error_flag(backend):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    import anyio
    core,row=backend;authorize(core,row)
    async def exercise():
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src'),'SOURCE_SECRET':'private-gateway-marker-391'}
        parameters=StdioServerParameters(command=sys.executable,args=['-m','mcp_manager','gateway','--data-dir',str(core.data_dir)],env=env)
        with anyio.fail_after(25):
            async with stdio_client(parameters) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    tools=(await client.list_tools()).tools
                    assert {t.name for t in tools}=={'search_tools','get_tool_details','call_tool'}
                    search=await client.call_tool('search_tools',{'query':'material'})
                    assert not search.isError and search.structuredContent['tools'][0]['tool_id']=='demo/create_material'
                    details=await client.call_tool('get_tool_details',{'tool_id':'demo/create_material'})
                    value=await client.call_tool('call_tool',{'tool_id':'demo/create_material','arguments':{'name':'Wire'},'schema_hash':details.structuredContent['schema_hash']})
                    assert not value.isError and value.structuredContent['result']['name']=='Wire'
                    bad=await client.call_tool('search_tools',{'unexpected':1});assert bad.isError
                    details=await client.call_tool('get_tool_details',{'tool_id':'demo/rejected'})
                    rejected=await client.call_tool('call_tool',{'tool_id':'demo/rejected','arguments':{},'schema_hash':details.structuredContent['schema_hash']})
                    assert rejected.isError and rejected.structuredContent['result']['error']['type']=='INVALID_PARAMETER'
    asyncio.run(exercise())


def test_gateway_rejects_recursive_backend_and_model_cannot_configure(backend):
    core,_=backend
    for spec in [dict(name='self',command='mcp-manager',args=['gateway']),dict(name='self',command=sys.executable,args=['-m','mcp_manager.gateway_server']),dict(name='self',command='mcp-manager-server')]:
        row=core.register_server(spec)
        with pytest.raises(PermissionError):core.plan_gateway([row['id']])


def test_changed_policy_revokes_existing_connection(backend):
    core,row=backend;authorize(core,row)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            detail=await gateway.get_tool_details('demo/create_material')
            policy=core.db.get_setting('gateway_policy');policy['backends']=[];policy['id']='revoked';core.db.set_setting('gateway_policy',policy)
            with pytest.raises(PermissionError):await gateway.call_tool('demo/create_material',{'name':'No'},detail['schema_hash'])
    asyncio.run(exercise())



def test_schema_change_is_detected_after_inspection_without_restarting_backend(backend):
    core,row=backend;authorize(core,row)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            previous=await gateway.get_tool_details('demo/create_material')
            change=await gateway.get_tool_details('demo/change_schema')
            await gateway.call_tool('demo/change_schema',{},change['schema_hash'])
            with pytest.raises(ValueError,match='schema changed'):
                await gateway.call_tool('demo/create_material',{'name':'OriginallyValid'},previous['schema_hash'])
            current=await gateway.get_tool_details('demo/create_material')
            assert previous['schema_hash']!=current['schema_hash']
            accepted=await gateway.call_tool('demo/create_material',{'name':'A_material_name_that_is_long_enough'},current['schema_hash'])
            assert not accepted.isError
    asyncio.run(exercise())


def test_timeout_is_not_automatically_retried(backend):
    core,row=backend;authorize(core,row,call_timeout=1)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            details=await gateway.get_tool_details('demo/slow')
            with pytest.raises(RuntimeError):await gateway.call_tool('demo/slow',{},details['schema_hash'])
            assert Path(row['spec']['args'][0]).with_name('slow-count').read_text()=='1'
            # Recovery happens only because this is a new explicit request.
            details=await gateway.get_tool_details('demo/create_material')
            result=await gateway.call_tool('demo/create_material',{'name':'Recovered'},details['schema_hash'])
            assert not result.isError
    asyncio.run(exercise())


def test_gateway_connects_to_real_streamable_http_backend(tmp_path):
    import socket,subprocess,time
    from mcp_manager.core.gateway import GatewayService
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    script=tmp_path/'gateway_http_backend.py';script.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("http",host="127.0.0.1",port='+str(port)+',stateless_http=True)\n@m.tool()\ndef ping()->dict[str,str]:return {"message":"pong"}\nif __name__=="__main__":m.run(transport="streamable-http")\n')
    log=(tmp_path/'http.log').open('w');process=subprocess.Popen([sys.executable,str(script)],stdout=log,stderr=log)
    core=ManagerCore(tmp_path/'data')
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):break
            except OSError:time.sleep(.05)
        row=core.register_server(dict(name='http',transport='streamable-http',url=f'http://127.0.0.1:{port}/mcp'))
        authorize(core,row)
        async def exercise():
            async with GatewayService(core) as gateway:
                detail=await gateway.get_tool_details('http/ping')
                value=await gateway.call_tool('http/ping',{},detail['schema_hash'])
                assert value.structuredContent['result']['message']=='pong'
        asyncio.run(exercise())
    finally:core.close();process.terminate();process.wait(timeout=10);log.close()


def test_external_schema_references_are_not_fetched():
    from mcp_manager.core.gateway import validate_arguments
    with pytest.raises(ValueError,match='External schema'):
        validate_arguments({'type':'object','properties':{'value':{'$ref':'file:///never-read-a-local-file'}}},{'value':1})
    validate_arguments({'type':'object','$defs':{'number':{'type':'integer'}},'properties':{'value':{'$ref':'#/$defs/number'}}},{'value':1})


def test_disabling_then_reenabling_requires_new_gateway_grant(backend):
    core,row=backend;authorize(core,row)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            await gateway.get_tool_details('demo/create_material')
            core.db.update_server(row['id'],{'enabled':False})
            core.db.update_server(row['id'],{'enabled':True})
            with pytest.raises(PermissionError):await gateway.get_tool_details('demo/create_material')
    asyncio.run(exercise())


def test_policy_rejects_backend_names_too_large_for_the_model_interface(tmp_path):
    path=tmp_path/'giant_name.py';path.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("giant")\n@m.tool(name="x"*4096)\ndef value()->dict[str,int]:return {"value":1}\nif __name__=="__main__":m.run()\n')
    core=ManagerCore(tmp_path/'data')
    try:
        row=core.register_server({'name':'large','command':sys.executable,'args':[str(path)]})
        with pytest.raises(ValueError,match='name'):authorize(core,row)
    finally:core.close()


def test_search_uses_cached_catalog_and_pages_are_bounded(backend):
    core,row=backend;authorize(core,row,result_limit_bytes=2048)
    from mcp_manager.core.gateway import GatewayService,encoded
    record=core.get_server(row['id']);tools=record['validation']['tools']
    for tool in tools:tool['description']='中'*180
    core.db.record_validation(row['id'],dict(record['validation'],tools=tools))
    async def exercise():
        async with GatewayService(core) as gateway:
            offset=0;seen=[]
            while True:
                page=await gateway.search_tools(limit=10,offset=offset)
                assert len(encoded(page))<=2048
                assert not gateway.connections
                seen.extend(t['tool_id'] for t in page['tools'])
                if page['next_offset'] is None:break
                assert page['next_offset']>offset;offset=page['next_offset']
            assert len(seen)==len(tools)==len(set(seen))
    asyncio.run(exercise())


def test_running_session_redacts_launch_credentials_after_rotation(backend):
    core,row=backend;authorize(core,row)
    from mcp_manager.core.gateway import GatewayService
    async def exercise():
        async with GatewayService(core) as gateway:
            detail=await gateway.get_tool_details('demo/echo_private')
            core.runtime.environ['SOURCE_SECRET']='new-secret-marker-781'
            result=await gateway.call_tool('demo/echo_private',{},detail['schema_hash'])
            assert 'private-gateway-marker-391' not in result.model_dump_json()
            assert '<redacted>' in result.model_dump_json()
    asyncio.run(exercise())
