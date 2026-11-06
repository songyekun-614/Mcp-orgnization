import asyncio
import os
from pathlib import Path
import sys
import anyio
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_management_mcp_is_read_only_and_cannot_self_authorize(tmp_path):
    async def exercise():
        parameters=StdioServerParameters(command=sys.executable,args=['-m','mcp_manager','serve','--data-dir',str(tmp_path/'data')],env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')})
        with anyio.fail_after(20):
            async with stdio_client(parameters) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    tools=(await client.list_tools()).tools;names={t.name for t in tools}
                    assert {'detect_platform','discover_mcp','list_installed_mcp','list_mcp_tools','install_mcp','submit_extension'}<=names
                    assert 'approve_plan' not in names
                    for t in tools:assert t.inputSchema.get('additionalProperties') is False
                    reply=await client.call_tool('detect_platform',{})
                    assert not reply.isError
                    assert reply.structuredContent['system'] in ('Linux','Windows','Darwin')
                    refused=await client.call_tool('execute_approved_plan',{'plan_id':'unknown'})
                    assert refused.isError
                    typo=await client.call_tool('list_installed_mcp',{'typo':1})
                    assert typo.isError
                    await client.send_ping()
    asyncio.run(exercise())
