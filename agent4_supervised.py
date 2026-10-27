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
    existing_anomalies = state.get("anomalies") or []
    if existing_anomalies:
        print(f"[DETECT] Preserving {len(existing_anomalies)} pre-detected process anomalies")
        return state

    value = state.get("value")
    low = state["baseline"].get("normal_low")
    high = state["baseline"].get("normal_high")
    anomalies = []

    if value is not None and low is not None and high is not None:
        if value < low or value > high:
            anomalies.append({
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

    state["anomalies"] = anomalies
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


def deterministic_diagnosis(ctx: dict, anomalies: list) -> str:
    anomaly_metrics = {a.get("metric", ""): a for a in anomalies}
    has_temp = any("Temp" in m for m in anomaly_metrics)
    has_press = any("Press" in m for m in anomaly_metrics)
    has_flow = any("Flow" in m for m in anomaly_metrics)

    if has_flow and has_press:
        flow_a = next(a for m, a in anomaly_metrics.items() if "Flow" in m)
        press_a = next(a for m, a in anomaly_metrics.items() if "Press" in m)
        return (
            f"Correlated anomaly detected: Both CC-01 coolant flow ({flow_a['value']} gpm) and Reactor 2 pressure ({press_a['value']} psi) "
            f"are elevated simultaneously above historical baselines. This cross-variable pattern points to an upstream feed pump surge "
            f"or supply regulator malfunction creating simultaneous hydraulic throughput and vessel pressurization while temperature remains stable. "
            f"Confidence: high (Multi-Variable Correlation Engine)."
        )
    elif has_flow and has_temp:
        flow_a = next(a for m, a in anomaly_metrics.items() if "Flow" in m)
        temp_a = next(a for m, a in anomaly_metrics.items() if "Temp" in m)
        return (
            f"Correlated anomaly detected: Reactor 2 temperature ({temp_a['value']} °C) is elevated while CC-01 cooling loop flow ({flow_a['value']} gpm) "
            f"is abnormal. This indicates heat transfer deficiency or cooling jacket flow starvation leading to thermal buildup in Reactor 2. "
            f"Confidence: high (Multi-Variable Correlation Engine)."
        )
    elif has_press and has_temp:
        press_a = next(a for m, a in anomaly_metrics.items() if "Press" in m)
        temp_a = next(a for m, a in anomaly_metrics.items() if "Temp" in m)
        return (
            f"Correlated anomaly detected: Simultaneous elevation of Reactor 2 temperature ({temp_a['value']} °C) and pressure ({press_a['value']} psi). "
            f"This points to an exothermic process reaction surge causing simultaneous thermal expansion and vapor pressurization inside Reactor 2. "
            f"Confidence: high (Multi-Variable Correlation Engine)."
        )
    elif has_temp:
        a = next(a for m, a in anomaly_metrics.items() if "Temp" in m)
        return (
            f"Elevated temperature ({a['value']} °C) on Reactor2 indicates cooling jacket restriction "
            f"or thermocouple RTD sensor drift against historical baseline [{a.get('normal_low')}, {a.get('normal_high')}]. "
            f"Confidence: high (Deterministic Plant Safety Engine fallback)."
        )
    elif has_press:
        a = next(a for m, a in anomaly_metrics.items() if "Press" in m)
        return (
            f"Pressure drift ({a['value']} psi) on Reactor2 indicates relief valve bypass or supply pressure regulator issue "
            f"outside expected bounds [{a.get('normal_low')}, {a.get('normal_high')}]. "
            f"Confidence: high (Deterministic Plant Safety Engine fallback)."
        )
    elif has_flow:
        a = next(a for m, a in anomaly_metrics.items() if "Flow" in m)
        return (
            f"Flow rate anomaly ({a['value']} gpm) on CC-01 indicates control valve stiction or pump cavitation "
            f"outside expected bounds [{a.get('normal_low')}, {a.get('normal_high')}]. "
            f"Confidence: high (Deterministic Plant Safety Engine fallback)."
        )
    else:
        return "Process metrics deviation detected on Line 3. Operational inspection recommended."


def deterministic_recommendation(ctx: dict, anomalies: list, diagnosis_text: str) -> dict:
    anomaly_metrics = {a.get("metric", ""): a for a in anomalies}
    has_flow = any("Flow" in m for m in anomaly_metrics)
    has_press = any("Press" in m for m in anomaly_metrics)
    has_temp = any("Temp" in m for m in anomaly_metrics)

    if has_flow and has_press:
        action = "inspect CC-01"
        urgency = "Immediate shift inspection"
        confidence = "high"
        rationale = "Simultaneous surge in cooling water flow and reactor pressure indicates an upstream supply regulator failure or primary feed pump overpressurization."
    elif has_flow and has_temp:
        action = "inspect CC-01"
        urgency = "Address promptly during current shift"
        confidence = "high"
        rationale = "Coolant flow abnormality combined with elevated reactor temperature requires immediate heat exchanger and jacket inspection."
    elif has_press and has_temp:
        action = "inspect Reactor2"
        urgency = "Immediate shift inspection"
        confidence = "high"
        rationale = "Coincident temperature and pressure rises indicate thermal vapor expansion or exothermic runaway risk."
    elif has_temp:
        action = "inspect Reactor2"
        urgency = "Address promptly during current shift"
        confidence = "high"
        rationale = "Observed temperature exceeds operational baseline."
    elif has_press:
        action = "check Reactor2"
        urgency = "Immediate shift inspection"
        confidence = "medium-high"
        rationale = "Observed pressure deviated from nominal operating limits."
    else:
        action = "verify CC-01"
        urgency = "Within 4 hours"
        confidence = "high"
        rationale = "Flow rate deviates from normal process transfer baseline."

    return {
        "action": action,
        "urgency": urgency,
        "confidence": confidence,
        "rationale": rationale,
        "raw": f"ACTION: {action}\nURGENCY: {urgency}\nCONFIDENCE: {confidence}\nRATIONALE: {rationale}"
    }


def diagnose(state: AgentState) -> AgentState:
    ctx = state["context"]
    anomalies = state.get("anomalies", [])
    if not anomalies and state.get("value") is not None:
        anomalies = [{
            "metric": state["metric_path"],
            "value": state["value"],
            "normal_low": state["baseline"].get("normal_low"),
            "normal_high": state["baseline"].get("normal_high")
        }]

    line_history = get_line_history(ctx.get("site", "Bristol"), ctx.get("area", "Polymerization"), ctx.get("line", "Line3"), hours=48)

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

    anomalies_lines = []
    for a in anomalies:
        m_name = a.get("label", a.get("metric", "Unknown"))
        val = a.get("value")
        low = a.get("normal_low")
        high = a.get("normal_high")
        direction = "ABOVE NORMAL" if val is not None and high is not None and val > high else "BELOW NORMAL"
        anomalies_lines.append(f"- {m_name}: currently {val} ({direction}, normal baseline [{low}, {high}])")
    anomalies_summary = "\n".join(anomalies_lines)

    prompt = (
        f"You are the autonomous supervisory engineer for Line 3 in the Polymerization unit at Bristol.\n\n"
        f"ACTIVE PROCESS ANOMALIES DETECTED:\n{anomalies_summary}\n\n"
        f"COMPLETE LINE TELEMETRY & HISTORICAL INFLUXDB BASELINES:\n{history_summary}\n\n"
        f"Analyze the physical correlation between all observed line metrics (specifically examining relationships "
        f"between cooling flow rate, reactor pressure, and reactor temperature). "
        f"For example, if both flow rate and pressure have increased simultaneously while temperature is stable, determine whether this indicates "
        f"an upstream pump surge, downstream line restriction/backpressure, or supply regulator malfunction. "
        f"State the most likely root cause, identify the specific equipment responsible, and state your confidence. "
        f"Two or three direct sentences, no markdown formatting, no headers, no bullet points."
    )

    try:
        state["report"] = generate_ai_content(prompt)
    except Exception as e:
        print(f"[DIAGNOSE FALLBACK] {e}")
        state["report"] = deterministic_diagnosis(ctx, anomalies)

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
    anomalies = state.get("anomalies", [])
    if not anomalies and state.get("value") is not None:
        anomalies = [{"metric": state.get("metric_path", ""), "value": state.get("value", 0)}]

    limits_info = []
    min_hours = None
    for a in anomalies:
        m = a.get("metric", "")
        val = a.get("value", 0)
        if "Press" in m:
            s_limit = 110.0
            drift_rate = 2.0
        elif "Flow" in m:
            s_limit = 8.0 if val < 10 else 20.0
            drift_rate = 1.0
        else:
            s_limit = 95.0
            drift_rate = (3.6 / 28) * 60
        headroom = abs(s_limit - val)
        hours = round(headroom / drift_rate, 1) if drift_rate > 0 else None
        if hours is not None and (min_hours is None or hours < min_hours):
            min_hours = hours
        limits_info.append(f"{a.get('label', m)}: current {val}, safety limit {s_limit}")

    limits_str = "; ".join(limits_info)
    equip = ctx.get("equipment", "Reactor2")

    prompt = (
        f"Diagnosis: {state['report']}\n\n"
        f"Observed limits: {limits_str}. Estimated time to safety limit: approximately {min_hours} hours.\n\n"
        f"Produce a structured advisory with exactly four fields, each on its own line, no markdown formatting:\n"
        f"ACTION: <action using a permitted verb: inspect, schedule, notify, check, verify, or monitor on {equip} or CC-01>\n"
        f"URGENCY: <how soon, in plain shift-supervisor language>\n"
        f"CONFIDENCE: <low, medium, medium-high, or high>\n"
        f"RATIONALE: <one sentence synthesizing the correlated evidence across all abnormal metrics>"
    )

    try:
        raw_text = generate_ai_content(prompt)
        rec = parse_recommendation(raw_text)
        rec["raw"] = raw_text
        if not rec.get("action"):
            rec = deterministic_recommendation(ctx, anomalies, state["report"])
        state["recommendation"] = rec
    except Exception as e:
        print(f"[RECOMMEND FALLBACK] {e}")
        state["recommendation"] = deterministic_recommendation(ctx, anomalies, state["report"])

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
