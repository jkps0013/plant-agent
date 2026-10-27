import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test():
    server_params = StdioServerParameters(
        command="python",
        args=["agent_mcp_server.py"]
    )
    metrics = [
        "Bristol/Polymerization/Line3/Reactor2/Temperature",
        "Bristol/Polymerization/Line3/Reactor2/Pressure",
        "Bristol/Polymerization/Line3/CC-01/FlowRate",
        "Bristol/Polymerization/Line 3/Reactor 2/Temperature"
    ]
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to MCP server.")

            print("\n--- 1. Testing list_ignition_tags ---")
            tags = await session.call_tool("list_ignition_tags", {})
            print("Ignition tags:", tags.content[0].text)

            print("\n--- 2. Testing read_history ---")
            hist = await session.call_tool("read_history", {"metric_path": metrics[0], "hours": 48})
            print(f"History [{metrics[0]}]:", hist.content[0].text)

            print("\n--- 3. Testing create_work_order (Permitted Action) ---")
            wo_allowed = await session.call_tool("create_work_order", {
                "equipment": "Reactor2",
                "action": "Inspect cooling loop and schedule cleaning",
                "urgency": "within 4 hours",
                "rationale": "Temperature approaching safety threshold"
            })
            print("Work order result (permitted):", wo_allowed.content[0].text)

            print("\n--- 4. Testing create_work_order (Prohibited Action - Guardrail Test) ---")
            wo_blocked = await session.call_tool("create_work_order", {
                "equipment": "Reactor2",
                "action": "Bypass safety interlock and adjust setpoint",
                "urgency": "immediate",
                "rationale": "Testing safety guardrail"
            })
            print("Work order result (blocked by guardrail):", wo_blocked.content[0].text)

            print("\n--- 5. Testing read_live_data ---")
            for m in metrics:
                result = await session.call_tool("read_live_data", {"metric_path": m})
                print(f"Live data [{m}]:", result.content[0].text)


asyncio.run(test())
