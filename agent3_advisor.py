import os
import time
from datetime import datetime
from typing import TypedDict
from dotenv import load_dotenv
from google import genai
from langgraph.graph import StateGraph, END
from agent1_listener import live_buffer, start_listener
from baseline import get_baseline

load_dotenv()

client = genai.Client()


class AgentState(TypedDict):
    metric_path: str
    value: float
    context: dict
    report: str
    baseline: dict
    anomalies: list
    recommendation: dict


def get_live_value(metric_path: str):
    val = live_buffer.get(metric_path)
    if val is not None:
        return val
    norm_target = metric_path.replace(" ", "").lower()
    for k, v in live_buffer.items():
        if k.replace(" ", "").lower() == norm_target:
            return v
    return None


def observe(state: AgentState) -> AgentState:
    metric = state["metric_path"]
    state["value"] = get_live_value(metric)

    parts = metric.replace("Line 3", "Line3").replace("Reactor 2", "Reactor2").split("/")
    if len(parts) == 5:
        state["context"] = {
            "site": parts[0],
            "area": parts[1],
            "line": parts[2],
            "equipment": parts[3],
            "metric": parts[4],
        }

    print(f"[OBSERVE] value={state['value']}  context={state['context']}")
    return state


def detect(state: AgentState) -> AgentState:
    value = state.get("value")
    low = state["baseline"].get("normal_low")
    high = state["baseline"].get("normal_high")

    state["anomalies"] = []

    if value is not None and low is not None and high is not None:
        if value < low or value > high:
            state["anomalies"].append({
                "metric": state["metric_path"],
                "value": value,
                "normal_low": low,
                "normal_high": high
            })
            print(f"[DETECT] ANOMALY - {value} is outside [{low}, {high}]")
        else:
            print(f"[DETECT] Normal - {value} is within [{low}, {high}]")
    else:
        print(f"[DETECT] Skipping check (value={value}, low={low}, high={high})")

    return state


def get_line_history(site: str, area: str, line: str, hours: int = 48) -> dict:
    known_metrics = [
        f"{site}/{area}/{line}/Reactor 2/Temperature",
        f"{site}/{area}/{line}/Reactor 2/Pressure",
        f"{site}/{area}/{line}/CC-01/FlowRate",
    ]

    history = {}
    for metric in known_metrics:
        history[metric] = get_baseline(metric, hours=hours)
    return history


def diagnose(state: AgentState) -> AgentState:
    ctx = state["context"]
    anomaly = state["anomalies"][0]

    line_history = get_line_history(
        ctx.get("site"), ctx.get("area"), ctx.get("line"), hours=48
    )

    history_summary_lines = []
    for metric, vals in line_history.items():
        current = get_live_value(metric)
        if current is None:
            current = "unknown"
        history_summary_lines.append(
            f"{metric}: currently {current}, InfluxDB baseline normal range "
            f"{vals.get('normal_low')} to {vals.get('normal_high')} (mean: {vals.get('mean')})"
        )
    history_summary = "\n".join(history_summary_lines)

    prompt = (
        f"On {ctx.get('line')} at {ctx.get('site')}, {ctx.get('equipment')} "
        f"{ctx.get('metric')} is currently {anomaly['value']}, outside its "
        f"InfluxDB baseline normal range of {anomaly['normal_low']} to {anomaly['normal_high']}.\n\n"
        f"Here is the current reading and historical InfluxDB baseline for every metric "
        f"on this line:\n{history_summary}\n\n"
        f"Given everything happening on this line right now, what is the "
        f"most likely cause? Name the specific equipment responsible if you "
        f"can identify one. State your confidence level. Write two or three "
        f"plain sentences a maintenance engineer could read in five seconds "
        f"at 3AM. No markdown, no headers, no bullet points."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    state["report"] = response.text
    return state


def parse_recommendation(text: str) -> dict:
    fields = {"action": "", "urgency": "", "confidence": "", "rationale": ""}
    for line in text.strip().splitlines():
        clean_line = line.strip().replace("*", "")
        upper = clean_line.upper()
        if upper.startswith("ACTION:"):
            fields["action"] = clean_line.split(":", 1)[1].strip()
        elif upper.startswith("URGENCY:"):
            fields["urgency"] = clean_line.split(":", 1)[1].strip()
        elif upper.startswith("CONFIDENCE:"):
            fields["confidence"] = clean_line.split(":", 1)[1].strip()
        elif upper.startswith("RATIONALE:"):
            fields["rationale"] = clean_line.split(":", 1)[1].strip()
    return fields


def recommend(state: AgentState) -> AgentState:
    ctx = state["context"]
    anomaly = state["anomalies"][0]

    metric_name = ctx.get("metric", "Temperature")
    if "Pressure" in metric_name:
        safety_limit = 110.0
    elif "Flow" in metric_name:
        safety_limit = 8.0
    else:
        safety_limit = 95.0

    headroom = abs(safety_limit - anomaly["value"])
    drift_rate_per_hour = (3.6 / 28) * 60
    hours_to_limit = (
        round(headroom / drift_rate_per_hour, 1)
        if drift_rate_per_hour > 0 else None
    )

    equip = ctx.get("equipment", "Reactor2")
    prompt = (
        f"Diagnosis: {state['report']}\n\n"
        f"Current value: {anomaly['value']}. Safety limit: {safety_limit}. "
        f"Estimated time to reach safety limit at current drift rate: "
        f"approximately {hours_to_limit} hours.\n\n"
        f"Produce a structured advisory with exactly four fields, each on "
        f"its own line, no markdown formatting:\n"
        f"ACTION: <action using a permitted verb: inspect, schedule, notify, check, verify, or monitor on {equip}>\n"
        f"URGENCY: <how soon, in plain shift-supervisor language>\n"
        f"CONFIDENCE: <low, medium, medium-high, or high>\n"
        f"RATIONALE: <one sentence tying the evidence together>"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    state["recommendation"] = parse_recommendation(response.text)
    state["recommendation"]["raw"] = response.text
    return state


def report(state: AgentState) -> AgentState:
    ctx = state["context"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec = state.get("recommendation", {})

    if rec.get("action"):
        formatted = (
            f"[{timestamp}] {ctx.get('equipment')} ({ctx.get('line')}, "
            f"{ctx.get('site')}):\n"
            f"ACTION: {rec.get('action')}\n"
            f"URGENCY: {rec.get('urgency')}\n"
            f"CONFIDENCE: {rec.get('confidence')}\n"
            f"RATIONALE: {rec.get('rationale')}"
        )
    elif state.get("report"):
        formatted = (
            f"[{timestamp}] {ctx.get('equipment')} ({ctx.get('line')}, "
            f"{ctx.get('site')}):\n{state['report']}"
        )
    else:
        formatted = (
            f"[{timestamp}] {ctx.get('equipment', 'Equipment')} ({ctx.get('line', 'Line3')}, "
            f"{ctx.get('site', 'Bristol')}):\n"
            f"Metric {state['metric_path']} is normal at {state.get('value')}."
        )

    state["report"] = formatted
    print(f"\n[REPORT]\n{formatted}\n")
    return state


def route_after_detect(state: AgentState) -> str:
    if state["anomalies"]:
        return "diagnose"
    else:
        return "report"


def build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("observe", observe)
    graph.add_node("detect", detect)
    graph.add_node("diagnose", diagnose)
    graph.add_node("recommend", recommend)
    graph.add_node("report", report)

    graph.add_edge("observe", "detect")
    graph.add_conditional_edges(
        "detect",
        route_after_detect,
        {
            "diagnose": "diagnose",
            "report": "report"
        }
    )
    graph.add_edge("diagnose", "recommend")
    graph.add_edge("recommend", "report")
    graph.add_edge("report", END)

    graph.set_entry_point("observe")
    return graph.compile()


if __name__ == "__main__":
    start_listener()
    time.sleep(3)

    metric = "Bristol/Polymerization/Line 3/Reactor 2/Temperature"
    baseline = get_baseline(metric, hours=48)

    agent = build_agent()
    initial_state = {
        "metric_path": metric,
        "value": None,
        "context": {},
        "report": "",
        "baseline": baseline,
        "anomalies": [],
        "recommendation": {}
    }
    result = agent.invoke(initial_state)
    print("\nFinal state:", result)
