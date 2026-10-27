import os
import json
import time
from datetime import datetime
from typing import TypedDict, Optional
from dotenv import load_dotenv
from google import genai
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from agent1_listener import live_buffer, start_listener
from baseline import get_baseline
from guardrails import check_action, check_equipment

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
    approval: Optional[bool]
    action_taken: bool


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
    if state.get("value") is None:
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


FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]


def generate_ai_content(prompt: str) -> str:
    for model_name in FALLBACK_MODELS:
        try:
            resp = client.models.generate_content(model=model_name, contents=prompt)
            if resp and resp.text:
                return resp.text.strip()
        except Exception as e:
            print(f"[MODEL FALLBACK] Model {model_name} failed ({type(e).__name__}): {e}")
            continue
    raise RuntimeError("All configured Gemini API models exhausted or unavailable.")


def deterministic_diagnosis(ctx: dict, anomaly: dict) -> str:
    metric = ctx.get("metric", "Temperature")
    equipment = ctx.get("equipment", "Reactor2")
    val = anomaly.get("value", "unknown")
    low = anomaly.get("normal_low", "unknown")
    high = anomaly.get("normal_high", "unknown")

    if "Temp" in metric:
        return (
            f"The elevated temperature ({val} °C) on {equipment} indicates cooling system jacket restriction "
            f"or thermocouple RTD sensor drift against historical baseline [{low}, {high}]. "
            f"Confidence: high (Deterministic Plant Safety Engine fallback)."
        )
    elif "Press" in metric:
        return (
            f"The pressure drift ({val} psi) on {equipment} indicates relief valve bypass or supply pressure regulator issue "
            f"outside expected bounds [{low}, {high}]. "
            f"Confidence: high (Deterministic Plant Safety Engine fallback)."
        )
    else:
        return (
            f"Flow rate anomaly ({val} gpm) on {equipment} indicates control valve stiction or pump cavitation "
            f"outside expected bounds [{low}, {high}]. "
            f"Confidence: high (Deterministic Plant Safety Engine fallback)."
        )


def deterministic_recommendation(ctx: dict, anomaly: dict, diagnosis_text: str) -> dict:
    metric = ctx.get("metric", "Temperature")
    equipment = ctx.get("equipment", "Reactor2")
    val = anomaly.get("value", 0)

    if "Temp" in metric:
        action = f"inspect {equipment}"
        urgency = "Address promptly during current shift"
        confidence = "high (Deterministic Rule Fallback)"
        rationale = f"Observed temperature {val} °C exceeds operational baseline."
    elif "Press" in metric:
        action = f"check {equipment}"
        urgency = "Immediate shift inspection"
        confidence = "medium-high (Deterministic Rule Fallback)"
        rationale = f"Observed pressure {val} psi deviated from nominal operating limits."
    else:
        action = f"verify {equipment}"
        urgency = "Within 4 hours"
        confidence = "high (Deterministic Rule Fallback)"
        rationale = f"Flow rate {val} gpm deviates from normal process transfer baseline."

    return {
        "action": action,
        "urgency": urgency,
        "confidence": confidence,
        "rationale": rationale,
        "raw": f"ACTION: {action}\nURGENCY: {urgency}\nCONFIDENCE: {confidence}\nRATIONALE: {rationale}"
    }


def diagnose(state: AgentState) -> AgentState:
    ctx = state["context"]
    anomaly = state["anomalies"][0]

    line_history = get_line_history(ctx.get("site"), ctx.get("area"), ctx.get("line"), hours=48)

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
        f"What is the most likely cause? Name the specific equipment if "
        f"possible. State your confidence. Two or three plain sentences, "
        f"no markdown, no headers, no bullet points."
    )

    try:
        state["report"] = generate_ai_content(prompt)
    except Exception as e:
        print(f"[DIAGNOSE FALLBACK] {e}")
        state["report"] = deterministic_diagnosis(ctx, anomaly)

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

    try:
        raw_text = generate_ai_content(prompt)
        rec = parse_recommendation(raw_text)
        rec["raw"] = raw_text
        if not rec.get("action"):
            rec = deterministic_recommendation(ctx, anomaly, state["report"])
        state["recommendation"] = rec
    except Exception as e:
        print(f"[RECOMMEND FALLBACK] {e}")
        state["recommendation"] = deterministic_recommendation(ctx, anomaly, state["report"])

    return state


def act(state: AgentState) -> AgentState:
    ctx = state["context"]
    rec = state["recommendation"]

    if state.get("approval") is True:
        action_text = rec.get("action", "")
        action_ok, action_reason = check_action(action_text)
        equipment_ok, equipment_reason = check_equipment(action_text)

        if not equipment_ok and ctx.get("equipment"):
            equipment_ok, equipment_reason = check_equipment(ctx.get("equipment"))

        if not action_ok or not equipment_ok:
            print(f"[GUARDRAIL BLOCKED] {action_reason} | {equipment_reason}")
            state["action_taken"] = False
            return state

        order = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "equipment": ctx.get("equipment"),
            "action": rec.get("action"),
            "urgency": rec.get("urgency"),
            "rationale": rec.get("rationale"),
            "approved_by": "supervisor"
        }

        try:
            with open("work_orders.json", "r") as f:
                orders = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            orders = []

        orders.append(order)
        with open("work_orders.json", "w") as f:
            json.dump(orders, f, indent=2)

        print(f"[ACT] Work order created: {order['action']}")
        state["action_taken"] = True
    else:
        print("[ACT] Not approved. No work order created.")
        state["action_taken"] = False

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
            f"RATIONALE: {rec.get('rationale')}\n"
            f"ACTION TAKEN: {state.get('action_taken')}"
        )
    elif state.get("report"):
        formatted = (
            f"[{timestamp}] {ctx.get('equipment')} ({ctx.get('line')}, "
            f"{ctx.get('site')}):\n{state['report']}\n"
            f"ACTION TAKEN: {state.get('action_taken')}"
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


def log_cycle(state: AgentState) -> AgentState:
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "metric_path": state.get("metric_path"),
        "value": state.get("value"),
        "anomalies": state.get("anomalies", []),
        "recommendation": state.get("recommendation", {}),
        "approval": state.get("approval"),
        "action_taken": state.get("action_taken", False)
    }

    with open("agent_audit_log.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")

    return state


def route_after_detect(state: AgentState) -> str:
    if state["anomalies"]:
        return "diagnose"
    else:
        return "log_cycle"


def build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("observe", observe)
    graph.add_node("detect", detect)
    graph.add_node("diagnose", diagnose)
    graph.add_node("recommend", recommend)
    graph.add_node("act", act)
    graph.add_node("report", report)
    graph.add_node("log_cycle", log_cycle)

    graph.add_edge("observe", "detect")
    graph.add_conditional_edges(
        "detect", route_after_detect,
        {"diagnose": "diagnose", "log_cycle": "log_cycle"}
    )
    graph.add_edge("diagnose", "recommend")
    graph.add_edge("recommend", "act")
    graph.add_edge("act", "report")
    graph.add_edge("report", "log_cycle")
    graph.add_edge("log_cycle", END)

    graph.set_entry_point("observe")

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["act"])


if __name__ == "__main__":
    start_listener()
    time.sleep(3)

    metric = "Bristol/Polymerization/Line 3/Reactor 2/Temperature"
    baseline = get_baseline(metric, hours=48)

    agent = build_agent()
    config = {"configurable": {"thread_id": "jk-gmbh-demo-1"}}

    initial_state = {
        "metric_path": metric,
        "value": None,
        "context": {},
        "report": "",
        "baseline": baseline,
        "anomalies": [],
        "recommendation": {},
        "approval": None,
        "action_taken": False
    }

    result = agent.invoke(initial_state, config=config)

    if agent.get_state(config).next == ("act",):
        print("\n>>> PAUSED - recommendation ready, awaiting approval <<<")
        print("Recommendation:", result.get("recommendation"))
    else:
        print("\nRun completed without triggering the approval gate.")
