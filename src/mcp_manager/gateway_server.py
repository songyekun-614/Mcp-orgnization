"""Three stable model-visible tools; the backend catalog stays outside the prompt."""
from contextlib import asynccontextmanager
from typing import Any,Annotated
from pydantic import Field
from mcp.types import CallToolResult,ToolAnnotations
from .server import ManagerMCP
from .core.gateway import GatewayService


def create_gateway_server(core):
    gateway=GatewayService(core)
    @asynccontextmanager
    async def lifespan(server):
        async with gateway:yield gateway
    mcp=ManagerMCP('MCP Tool Gateway',lifespan=lifespan,instructions='Use search_tools to find capabilities, get_tool_details for one schema, then call_tool with its schema_hash. Only locally authorized backends/tools are callable. Backend execute flags, snapshots and permissions still apply. No shell or generated-code entry point exists. For truncated results use summary for task IDs/paths; output_file holds the full redacted response. Prefer backend pagination and do not load large artifacts wholesale. Never request the full tool catalog unless the user needs it.')
    read=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
    @mcp.tool(annotations=read)
    async def search_tools(query:Annotated[str,Field(max_length=300)]='',server:str|None=None,offset:Annotated[int,Field(strict=True,ge=0)]=0,limit:Annotated[int,Field(strict=True,ge=1,le=10)]=3)->dict[str,Any]:
        """Search locally granted tools by keyword (Chinese/English); return brief names only. Empty query browses a small page."""
        return await gateway.search_tools(query,server,offset,limit)
    @mcp.tool(annotations=read)
    async def get_tool_details(tool_id:Annotated[str,Field(min_length=3,max_length=512)])->dict[str,Any]:
        """Read ONE tool's complete input schema and schema_hash. Use its namespace/tool_name ID from search_tools."""
        return await gateway.get_tool_details(tool_id)
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=False,openWorldHint=True))
    async def call_tool(tool_id:Annotated[str,Field(min_length=3,max_length=512)],arguments:dict[str,Any],schema_hash:Annotated[str,Field(pattern='^[a-f0-9]{64}$')])->CallToolResult:
        """Forward a granted tool using its inspected schema_hash. Backend validation and execute flags apply. Errors stay errors; large results return an artifact."""
        return await gateway.call_tool(tool_id,arguments,schema_hash)
    mcp.gateway=gateway
    return mcp


def main():
    from .cli import main as cli
    import sys
    return cli(["gateway",*sys.argv[1:]])
