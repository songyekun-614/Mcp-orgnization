"""Low-risk local server for explicit installation/client acceptance."""
from mcp.server.fastmcp import FastMCP
mcp=FastMCP('Manager Acceptance')


@mcp.tool()
def ping()->dict[str,str]:
    """Return a fixed local health response without file or network operations."""
    return {'status':'completed','reply':'pong'}


if __name__=='__main__':mcp.run()
