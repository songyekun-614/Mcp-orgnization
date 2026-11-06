import asyncio
from pathlib import Path
import sys
import pytest
from mcp_manager.models import ServerSpec


def server_script(tmp_path):
    path=tmp_path/'server.py'
    path.write_text('from mcp.server.fastmcp import FastMCP\nmcp=FastMCP("Acceptance")\n@mcp.tool()\ndef add(a:int,b:int)->dict[str,int]:\n return {"sum":a+b}\nif __name__=="__main__":mcp.run()\n')
    return path


def test_real_stdio_handshake_tools_and_call(tmp_path):
    from mcp_manager.core.runtime import RuntimeManager
    spec=ServerSpec(name='acceptance',command=sys.executable,args=[str(server_script(tmp_path))])
    runtime=RuntimeManager(tmp_path/'logs')
    result=runtime.probe(spec,tool_name='add',arguments={'a':2,'b':3})
    assert result['level']=='tool_call_tested'
    assert result['facts']['handshake_completed'] is True
    assert result['tools'][0]['name']=='add'
    assert result['call']['structuredContent']['sum']==5
    assert result['connection_closed'] is True


def test_missing_launcher_is_not_connected(tmp_path):
    from mcp_manager.core.runtime import RuntimeManager
    result=RuntimeManager(tmp_path).probe(ServerSpec(name='absent',command=str(tmp_path/'absent')))
    assert result['status']=='failed'
    assert result['facts']['handshake_completed'] is False
    assert result['level']!='tools_discovered'


def test_runtime_blocks_implicit_download_and_self_recursion(tmp_path):
    from mcp_manager.core.runtime import RuntimeManager
    runtime=RuntimeManager(tmp_path)
    for spec in [ServerSpec(name='download',command='npx',args=['-y','unknown-package']),ServerSpec(name='loop',command=sys.executable,args=['-m','mcp_manager','serve'])]:
        with pytest.raises(PermissionError):runtime.probe(spec)


def test_real_streamable_http_handshake_and_close(tmp_path):
    import socket,subprocess,time,httpx
    from mcp_manager.core.runtime import RuntimeManager
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    script=tmp_path/'http_server.py';script.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("http-test",host="127.0.0.1",port='+str(port)+',stateless_http=True)\n@m.tool()\ndef ping()->str:return "pong"\nif __name__=="__main__":m.run(transport="streamable-http")\n')
    log=(tmp_path/'http.log').open('w')
    process=subprocess.Popen([sys.executable,str(script)],stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):break
            except OSError:time.sleep(.05)
        result=RuntimeManager(tmp_path/'logs').probe(ServerSpec(name='http',transport='streamable-http',url=f'http://127.0.0.1:{port}/mcp'),tool_name='ping')
        assert result['status']=='completed',result
        assert result['facts']['tool_call_tested'] is True
        assert result['facts']['process_started'] is False
        assert result['connection_closed'] is True
    finally:process.terminate();process.wait(timeout=10);log.close()


def test_secret_echo_is_redacted_from_protocol_metadata_and_result(tmp_path):
    from mcp_manager.core.runtime import RuntimeManager
    secret='UNIQUE_PROTOCOL_SECRET_a7c492'
    script=tmp_path/'echo_secret.py'
    script.write_text('import os\nfrom mcp.server.fastmcp import FastMCP\nm=FastMCP(os.environ["ECHO_SECRET"])\n@m.tool()\ndef echo()->dict[str,str]:\n return {"value":os.environ["ECHO_SECRET"]}\nif __name__=="__main__":m.run()\n')
    spec=ServerSpec(name='echo',command=sys.executable,args=[str(script)],env_refs={'ECHO_SECRET':'MY_PRIVATE_TOKEN'})
    import json
    result=RuntimeManager(tmp_path/'logs',{'MY_PRIVATE_TOKEN':secret}).probe(spec,tool_name='echo')
    assert result['status']=='completed',result
    assert secret not in json.dumps(result)
    assert result['call']['structuredContent']['value']=='<redacted>'


def test_owned_connection_discovers_all_tool_pages(tmp_path):
    from mcp_manager.core.runtime import RuntimeManager
    script=tmp_path/'paged_server.py'
    script.write_text('''import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool,ListToolsResult,ListToolsRequest
server=Server("paged")
@server.list_tools()
async def tools(request:ListToolsRequest)->ListToolsResult:
 cursor=request.params.cursor if request.params else None
 return ListToolsResult(tools=[Tool(name="second" if cursor else "first",inputSchema={"type":"object"})],nextCursor=None if cursor else "next")
async def main():
 async with stdio_server() as (read,write):
  await server.run(read,write,server.create_initialization_options())
anyio.run(main)
''')
    runtime=RuntimeManager(tmp_path/'logs')
    try:
        r=runtime.start('paged',ServerSpec(name='paged',command=sys.executable,args=[str(script)]))
        assert r['tool_count']==2,r
        assert {t['name'] for t in r['tools']}=={'first','second'}
    finally:runtime.close()
