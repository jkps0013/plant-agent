import json
from datetime import datetime
from mcp.server import Server
from mcp.types import Tool, TextContent
from agent1_listener import live_buffer, start_listener
from baseline import get_baseline
from guardrails import check_action, check_equipment

server = Server("jk-gmbh-mcp-server")

IGNITION_TAGS = [
    "Bristol/Polymerization/Line 3/Reactor 2/Temperature",
    "Bristol/Polymerization/Line 3/Reactor 2/Pressure",
    "Bristol/Polymerization/Line 3/CC-01/FlowRate",
]


def resolve_live_metric(metric_path: str):
    if metric_path in live_buffer:
        return metric_path, live_buffer[metric_path]

    norm_target = metric_path.replace(" ", "").lower()
    for k, v in live_buffer.items():
        if k.replace(" ", "").lower() == norm_target:
            return k, v

    for k, v in live_buffer.items():
        if norm_target in k.replace(" ", "").lower():
            return k, v

    return metric_path, None


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="read_live_data",
            description="Read the current live value of an Ignition metric from the JK GmbH namespace (supports whitespace tolerance)",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric_path": {
                        "type": "string",
                        "description": "Tag path, e.g. Bristol/Polymerization/Line 3/Reactor 2/Temperature or 'all'"
                    }
                },
                "required": ["metric_path"]
            }
        ),
        Tool(
            name="read_history",
            description="Read recent historical baseline values for a metric from InfluxDB",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric_path": {"type": "string"},
                    "hours": {"type": "number", "default": 48}
                },
                "required": ["metric_path"]
            }
        ),
        Tool(
            name="create_work_order",
            description="Creates a maintenance work order from an approved recommendation (checked against safety guardrails)",
            inputSchema={
                "type": "object",
                "properties": {
                    "equipment": {"type": "string"},
                    "action": {"type": "string"},
                    "urgency": {"type": "string"},
                    "rationale": {"type": "string"},
                    "approved_by": {"type": "string", "default": "supervisor"}
                },
                "required": ["equipment", "action"]
            }
        ),
        Tool(
            name="list_ignition_tags",
            description="List all configured Ignition tags and their current live buffer status",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "read_live_data":
        metric = arguments.get("metric_path", "")
        if metric.lower() in ["all", "list", "*"]:
            return [TextContent(type="text", text=json.dumps(live_buffer, indent=2))]

        matched_key, value = resolve_live_metric(metric)
        if value is not None:
            return [TextContent(type="text", text=json.dumps({metric: value, "metric": matched_key, "value": value}))]

        return [TextContent(type="text", text=json.dumps({
            metric: "metric not found",
            "error": f"Metric '{metric}' not found in live buffer",
            "available_metrics": list(live_buffer.keys()) or IGNITION_TAGS
        }))]

    if name == "read_history":
        metric = arguments.get("metric_path", "")
        hours = arguments.get("hours", 48)
        result = get_baseline(metric, hours=hours)

        if result.get("normal_low") is None:
            alt_metric = metric.replace("Line 3", "Line3").replace("Reactor 2", "Reactor2") if " " in metric else metric.replace("Line3", "Line 3").replace("Reactor2", "Reactor 2")
            alt_result = get_baseline(alt_metric, hours=hours)
            if alt_result.get("normal_low") is not None:
                result = alt_result

        return [TextContent(type="text", text=json.dumps(result))]

    if name == "create_work_order":
        equipment = arguments.get("equipment", "")
        action = arguments.get("action", "")
        urgency = arguments.get("urgency", "")
        rationale = arguments.get("rationale", "")
        approved_by = arguments.get("approved_by", "supervisor")

        action_ok, action_reason = check_action(action)
        combined_text = f"{action} {equipment}"
        equipment_ok, equipment_reason = check_equipment(combined_text)

        if not action_ok or not equipment_ok:
            blocked_response = {
                "status": "blocked",
                "action_allowed": action_ok,
                "action_reason": action_reason,
                "equipment_allowed": equipment_ok,
                "equipment_reason": equipment_reason
            }
            return [TextContent(type="text", text=json.dumps(blocked_response, indent=2))]

        order = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "equipment": equipment,
            "action": action,
            "urgency": urgency,
            "rationale": rationale,
            "approved_by": approved_by
        }

        try:
            with open("work_orders.json", "r") as f:
                orders = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            orders = []

        orders.append(order)
        with open("work_orders.json", "w") as f:
            json.dump(orders, f, indent=2)

        return [TextContent(type="text", text=json.dumps({"status": "created", "order": order}, indent=2))]

    if name == "list_ignition_tags":
        status = {}
        for tag in IGNITION_TAGS:
            _, val = resolve_live_metric(tag)
            status[tag] = val
        return [TextContent(type="text", text=json.dumps(status, indent=2))]


if __name__ == "__main__":
    start_listener()
    import asyncio
    from mcp.server.stdio import stdio_server

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )

    asyncio.run(main())
