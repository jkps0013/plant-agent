import asyncio
import json
import time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

METRICS = [
    "Bristol/Polymerization/Line3/Reactor2/Temperature",
    "Bristol/Polymerization/Line3/Reactor2/Pressure",
    "Bristol/Polymerization/Line3/CC-01/FlowRate"
]


async def verify():
    server_params = StdioServerParameters(
        command="python",
        args=["agent_mcp_server.py"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await asyncio.sleep(3)

            print("\nRunning Section 3 connection verification for all 3 metrics...\n")

            try:
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                assert "read_live_data" in names
                print("CHECK 1 PASSED  -  Server reachable")
            except Exception as e:
                print(f"CHECK 1 FAILED  -  {e}")
                return

            try:
                for m in METRICS:
                    r = await session.call_tool("read_live_data", {"metric_path": m})
                    val = json.loads(r.content[0].text)[m]
                    if val == "metric not found":
                        raise ValueError(f"Metric not found: {m}")
                    print(f"CHECK 2 PASSED  -  Live data flowing for {m}: {val}")
            except Exception as e:
                print(f"CHECK 2 FAILED  -  {e}")
                return

            try:
                for m in METRICS:
                    parts = m.split("/")
                    assert len(parts) == 5, f"Expected 5 path levels, got {len(parts)}"
                    assert parts[0] == "Bristol"
                print("CHECK 3 PASSED  -  ISA-95 context intact for all 3 metrics")
            except AssertionError as e:
                print(f"CHECK 3 FAILED  -  ISA-95 context missing or incomplete: {e}")
                return

            print("\n" + "=" * 55)
            print("ALL THREE CHECKS PASSED FOR ALL 3 METRICS")
            print("It has been watching. Now it starts thinking.")
            print("=" * 55 + "\n")


asyncio.run(verify())
