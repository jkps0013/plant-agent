import os
import time
from datetime import datetime
from typing import TypedDict
from dotenv import load_dotenv
from google import genai
from langgraph.graph import StateGraph, END
from agent1_listener import live_buffer, start_listener

load_dotenv()

client = genai.Client()


class AgentState(TypedDict):
    metrics: list
    readings: dict
    history: dict
    trends: dict
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
    history = state.get("history") or {}
    trends = {}

    for m in state["metrics"]:
        val = get_live_value(m)
        readings[m] = val
        m_hist = history.get(m, [])
        m_hist.append(val)
        history[m] = m_hist[-3:]

        if len(history[m]) >= 2 and history[m][-1] is not None and history[m][0] is not None:
            if history[m][-1] > history[m][0]:
                trends[m] = "rising steadily"
            elif history[m][-1] < history[m][0]:
                trends[m] = "falling steadily"
            else:
                trends[m] = "holding steady"
        else:
            trends[m] = "holding steady"

        print(f"[OBSERVE] {m}: value={val}  trend={trends[m]}")

    state["readings"] = readings
    state["history"] = history
    state["trends"] = trends
    return state


def reason(state: AgentState) -> AgentState:
    summary_lines = []
    for m, val in state["readings"].items():
        trend = state["trends"].get(m, "holding steady")
        summary_lines.append(f"{m}: currently {val}, {trend}")
    summary = "\n".join(summary_lines)

    prompt = (
        f"You are monitoring Line 3 in the Polymerization area at the Bristol site.\n"
        f"Here are the live metrics across the equipment on this line:\n{summary}\n\n"
        f"Write a short, direct operational status report a maintenance engineer could read "
        f"in five seconds at 3AM. State the condition of each equipment clearly - do not "
        f"ask questions, do not hedge, do not use markdown formatting, headers, or bullet points. "
        f"Two or three plain sentences only."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    state["report"] = response.text
    return state


def report(state: AgentState) -> AgentState:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    formatted = f"[{timestamp}] Line 3 (Polymerization, Bristol):\n{state['report']}"
    state["report"] = formatted
    print(f"\n[REPORT]\n{formatted}\n")
    return state


def build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("observe", observe)
    graph.add_node("reason", reason)
    graph.add_node("report", report)
    graph.add_edge("observe", "reason")
    graph.add_edge("reason", "report")
    graph.add_edge("report", END)
    graph.set_entry_point("observe")
    return graph.compile()


if __name__ == "__main__":
    start_listener()
    time.sleep(3)

    agent = build_agent()
    state = {
        "metrics": [
            "Bristol/Polymerization/Line3/Reactor2/Temperature",
            "Bristol/Polymerization/Line3/Reactor2/Pressure",
            "Bristol/Polymerization/Line3/CC-01/FlowRate"
        ],
        "readings": {},
        "history": {},
        "trends": {},
        "report": ""
    }

    for cycle in range(3):
        print(f"\n--- Cycle {cycle + 1} ---")
        state = agent.invoke(state)
        if cycle < 2:
            print("Waiting 30 seconds for the next cycle...")
            time.sleep(30)
