"""SMOKE 02 - ClickHouse Cloud reachable, then the same via mcp-clickhouse.

Part A proves credentials and network (port 8443, HTTP interface - 9440 is
native TCP and will not work here).
Part B proves the MCP server runs and answers. The track REQUIRES runtime use
via the official MCP server, so part B is the hard gate for the whole entry.
"""
import os, asyncio, json
from dotenv import load_dotenv

load_dotenv()

# ---- Part A: direct -------------------------------------------------------
import clickhouse_connect

client = clickhouse_connect.get_client(
    host=os.environ["CLICKHOUSE_HOST"],
    port=int(os.environ["CLICKHOUSE_PORT"]),
    username=os.environ["CLICKHOUSE_USER"],
    password=os.environ["CLICKHOUSE_PASSWORD"],
    secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
)
print("A OK -", client.command("SELECT version()"))

# ---- Part B: through the MCP server ---------------------------------------
# mcp-clickhouse reads the same CLICKHOUSE_* env vars. Launch it over stdio and
# ask it to list tables. If the ADK McpToolset import path below is wrong for
# ADK 2.0, fix it here first - that is exactly what this smoke test is for.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_clickhouse.main"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("B OK - tools:", [t.name for t in tools.tools])

asyncio.run(main())
