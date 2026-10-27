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
    metrics: list
    readings: dict
    baselines: dict
    anomalies: list
    report: str


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
    readings = {}
    for m in state["metrics"]:
        val = get_live_value(m)
        readings[m] = val
        print(f"[OBSERVE] {m}: value={val}")
    state["readings"] = readings
    return state


def detect(state: AgentState) -> AgentState:
    anomalies = []
    for m in state["metrics"]:
        val = state["readings"].get(m)
        b = state["baselines"].get(m, {})
        low = b.get("normal_low")
        high = b.get("normal_high")

        if low is not None and high is not None and val is not None:
            if val < low or val > high:
                anomalies.append({
                    "metric": m,
                    "value": val,
                    "normal_low": low,
                    "normal_high": high
                })
                print(f"[DETECT] ANOMALY on {m} - {val} is outside [{low}, {high}]")
            else:
                print(f"[DETECT] Normal on {m} - {val} is within [{low}, {high}]")
        else:
            print(f"[DETECT] Skipping check for {m} (val={val}, baseline={b})")

    state["anomalies"] = anomalies
    return state


def diagnose(state: AgentState) -> AgentState:
    summary_lines = []
    for m in state["metrics"]:
        val = state["readings"].get(m, "unknown")
        b = state["baselines"].get(m, {})
        low = b.get("normal_low")
        high = b.get("normal_high")
        summary_lines.append(f"{m}: current={val}, normal range [{low}, {high}]")
    metrics_summary = "\n".join(summary_lines)

    anomaly_lines = []
    for a in state["anomalies"]:
        anomaly_lines.append(f"- {a['metric']} is {a['value']} (expected [{a['normal_low']}, {a['normal_high']}])")
    anomaly_summary = "\n".join(anomaly_lines)

    prompt = (
        f"You are monitoring Line 3 at the Bristol site.\n"
        f"The following anomalies were detected:\n{anomaly_summary}\n\n"
        f"Here is the complete state across all line metrics:\n{metrics_summary}\n\n"
        f"Given everything happening on this line right now, what is the most likely cause? "
        f"Analyze correlations (e.g. relation between cooling flow, temperature, and pressure). "
        f"Name the specific equipment responsible if you can identify one. State your confidence level. "
        f"Write two or three plain sentences a maintenance engineer could read in five seconds at 3AM. "
        f"No markdown, no headers, no bullet points."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    state["report"] = response.text
    return state


def report(state: AgentState) -> AgentState:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not state.get("report"):
        state["report"] = "All observed metrics (Temperature, Pressure, FlowRate) are within normal baseline ranges."

    formatted = f"[{timestamp}] Line 3 (Polymerization, Bristol):\n{state['report']}"
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
    graph.add_edge("diagnose", "report")
    graph.add_edge("report", END)

    graph.set_entry_point("observe")
    return graph.compile()


if __name__ == "__main__":
    start_listener()
    time.sleep(3)

    metrics = [
        "Bristol/Polymerization/Line3/Reactor2/Temperature",
        "Bristol/Polymerization/Line3/Reactor2/Pressure",
        "Bristol/Polymerization/Line3/CC-01/FlowRate"
    ]

    baselines = {m: get_baseline(m, hours=48) for m in metrics}

    agent = build_agent()
    initial_state = {
        "metrics": metrics,
        "readings": {},
        "baselines": baselines,
        "anomalies": [],
        "report": ""
    }
    result = agent.invoke(initial_state)
    print("\nFinal state:", result)
