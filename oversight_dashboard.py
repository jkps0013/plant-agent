import os
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import json
import time
from datetime import datetime
import streamlit as st
from agent4_supervised import build_agent, start_listener, get_baseline, get_live_value, live_buffer

st.set_page_config(page_title="JK GmbH Oversight Dashboard", page_icon="🏭", layout="wide")

st.markdown("""
<style>
    .metric-card {
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 8px;
        border-left: 5px solid #1E88E5;
        margin-bottom: 10px;
    }
    .anomaly-banner {
        background-color: #ffebee;
        color: #c62828;
        padding: 12px 18px;
        border-radius: 8px;
        border: 1px solid #ef5350;
        margin-bottom: 15px;
        font-weight: 600;
    }
    [data-testid="stDeployButton"],
    .stDeployButton,
    [data-testid="stToolbarActions"],
    #MainMenu,
    header [data-testid="stToolbar"] {
        display: none !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("🏭 JK GmbH — Agent Oversight Dashboard")
st.caption("Autonomous Real-Time Plant Sentinel with InfluxDB Baselines, Gemini Reasoning & Guardrails")

if "listener_started" not in st.session_state:
    start_listener()
    time.sleep(2)
    st.session_state.listener_started = True

if "agent" not in st.session_state:
    st.session_state.agent = build_agent()
    st.session_state.config = {"configurable": {"thread_id": "jk-gmbh-dashboard-session"}}
    st.session_state.paused_state = None
    st.session_state.last_result = None
    st.session_state.active_anomaly = None
    st.session_state.latched_anomalies = set()
    st.session_state.approval_message = None
    st.session_state.simulated_overrides = {}
    st.session_state.pending_diagnosis = None

METRICS_CONFIG = {
    "Reactor 2 Temperature": {
        "path": "Bristol/Polymerization/Line 3/Reactor 2/Temperature",
        "unit": "°C"
    },
    "Reactor 2 Pressure": {
        "path": "Bristol/Polymerization/Line 3/Reactor 2/Pressure",
        "unit": "psi"
    },
    "CC-01 Flow Rate": {
        "path": "Bristol/Polymerization/Line 3/CC-01/FlowRate",
        "unit": "gpm"
    }
}


@st.cache_data(ttl=60)
def fetch_influxdb_baselines():
    baselines = {}
    for label, cfg in METRICS_CONFIG.items():
        baselines[cfg["path"]] = get_baseline(cfg["path"], hours=48)
    return baselines


def get_current_reading(tag_path: str):
    if tag_path in st.session_state.simulated_overrides:
        return st.session_state.simulated_overrides[tag_path]
    return get_live_value(tag_path)


def evaluate_plant_state():
    baselines = fetch_influxdb_baselines()
    detected_anomalies = []
    for label, cfg in METRICS_CONFIG.items():
        val = get_current_reading(cfg["path"])
        b = baselines.get(cfg["path"], {})
        low = b.get("normal_low")
        high = b.get("normal_high")

        if val is not None and low is not None and high is not None:
            if val < low or val > high:
                detected_anomalies.append({
                    "label": label,
                    "metric": cfg["path"],
                    "value": round(float(val), 2),
                    "normal_low": low,
                    "normal_high": high,
                    "baseline": b
                })
    return detected_anomalies


col_status, col_approval = st.columns([1, 1], gap="large")

with col_status:
    st.subheader("📊 Live Plant Telemetry")

    @st.fragment(run_every="2s")
    def render_live_telemetry_and_sentinel():
        baselines = fetch_influxdb_baselines()

        temp = get_current_reading(METRICS_CONFIG["Reactor 2 Temperature"]["path"])
        temp_b = baselines.get(METRICS_CONFIG["Reactor 2 Temperature"]["path"], {})

        pressure = get_current_reading(METRICS_CONFIG["Reactor 2 Pressure"]["path"])
        press_b = baselines.get(METRICS_CONFIG["Reactor 2 Pressure"]["path"], {})

        flow = get_current_reading(METRICS_CONFIG["CC-01 Flow Rate"]["path"])
        flow_b = baselines.get(METRICS_CONFIG["CC-01 Flow Rate"]["path"], {})

        now_str = datetime.now().strftime("%H:%M:%S")

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric(
                label="Reactor 2 Temp",
                value=f"{temp:.1f} °C" if temp is not None else "Waiting...",
                delta=f"InfluxDB: {temp_b.get('normal_low')} - {temp_b.get('normal_high')} °C" if temp_b.get("normal_low") is not None else "No baseline",
                delta_color="off"
            )
        with m2:
            st.metric(
                label="Reactor 2 Pressure",
                value=f"{pressure:.1f} psi" if pressure is not None else "Waiting...",
                delta=f"InfluxDB: {press_b.get('normal_low')} - {press_b.get('normal_high')} psi" if press_b.get("normal_low") is not None else "No baseline",
                delta_color="off"
            )
        with m3:
            st.metric(
                label="CC-01 Flow Rate",
                value=f"{flow:.1f} gpm" if flow is not None else "Waiting...",
                delta=f"InfluxDB: {flow_b.get('normal_low')} - {flow_b.get('normal_high')} gpm" if flow_b.get("normal_low") is not None else "No baseline",
                delta_color="off"
            )

        c_sub1, c_sub2 = st.columns([3, 1])
        with c_sub1:
            st.caption(f"🟢 **Streaming Every 2s** • InfluxDB Cloud Connected • Time: `{now_str}`")
        with c_sub2:
            if st.button("🔄 Refresh", use_container_width=True):
                st.cache_data.clear()
                st.rerun(scope="app")

        anomalies = evaluate_plant_state()

        if anomalies:
            primary = anomalies[0]
            metric_key = primary["metric"]

            st.error(f"🚨 **ACTIVE ISSUE DETECTED ({len(anomalies)} metric(s) out of InfluxDB baseline):**")
            for a in anomalies:
                st.markdown(f"- **{a['label']}** is `{a['value']}` (InfluxDB 48h Baseline: `[{a['normal_low']}, {a['normal_high']}]`)")

            if metric_key not in st.session_state.latched_anomalies:
                if st.session_state.paused_state is not None:
                    st.warning("⏸️ **Advisory ready — awaiting Supervisor Decision in the Approval Panel on the right.**")
                elif st.session_state.get("pending_diagnosis"):
                    st.info("🤖 **Auto-Sentinel: Anomaly detected! Diagnosing line with Gemini in the Approval Panel...**")
                else:
                    st.session_state.pending_diagnosis = primary
                    st.rerun(scope="app")
            else:
                st.info("ℹ️ **Active issue acknowledged & logged by supervisor.** Monitoring process recovery to baseline.")
                if st.button("⚡ Re-diagnose Issue", use_container_width=True):
                    st.session_state.latched_anomalies.discard(metric_key)
                    st.session_state.paused_state = None
                    st.session_state.pending_diagnosis = primary
                    st.rerun(scope="app")
        else:
            if st.session_state.latched_anomalies:
                st.session_state.latched_anomalies.clear()
            if st.session_state.paused_state is not None:
                st.session_state.paused_state = None
            st.session_state.approval_message = None
            st.success("✅ **All Line 3 equipment operating within InfluxDB baseline limits.**")
            st.caption("Live metrics conform to historical 48-hour Gaussian distribution parameters.")

    render_live_telemetry_and_sentinel()

    st.divider()
    st.markdown("##### 🧪 Anomaly Simulator (Test Scenarios)")
    sim_baselines = fetch_influxdb_baselines()
    sim_col1, sim_col2, sim_col3, sim_col4 = st.columns(4)
    with sim_col1:
        if st.button("🔥 High Temp (78°C)", use_container_width=True):
            m_path = METRICS_CONFIG["Reactor 2 Temperature"]["path"]
            b = sim_baselines.get(m_path, {})
            st.session_state.simulated_overrides[m_path] = 78.5
            st.session_state.latched_anomalies.discard(m_path)
            st.session_state.paused_state = None
            st.session_state.pending_diagnosis = {
                "label": "Reactor 2 Temperature",
                "metric": m_path,
                "value": 78.5,
                "normal_low": b.get("normal_low"),
                "normal_high": b.get("normal_high"),
                "baseline": b
            }
            st.rerun(scope="app")
    with sim_col2:
        if st.button("⚠️ Low Press (92 psi)", use_container_width=True):
            m_path = METRICS_CONFIG["Reactor 2 Pressure"]["path"]
            b = sim_baselines.get(m_path, {})
            st.session_state.simulated_overrides[m_path] = 92.0
            st.session_state.latched_anomalies.discard(m_path)
            st.session_state.paused_state = None
            st.session_state.pending_diagnosis = {
                "label": "Reactor 2 Pressure",
                "metric": m_path,
                "value": 92.0,
                "normal_low": b.get("normal_low"),
                "normal_high": b.get("normal_high"),
                "baseline": b
            }
            st.rerun(scope="app")
    with sim_col3:
        if st.button("⚠️ High Flow (120 gpm)", use_container_width=True):
            m_path = METRICS_CONFIG["CC-01 Flow Rate"]["path"]
            b = sim_baselines.get(m_path, {})
            st.session_state.simulated_overrides[m_path] = 120.5
            st.session_state.latched_anomalies.discard(m_path)
            st.session_state.paused_state = None
            st.session_state.pending_diagnosis = {
                "label": "CC-01 Flow Rate",
                "metric": m_path,
                "value": 120.5,
                "normal_low": b.get("normal_low"),
                "normal_high": b.get("normal_high"),
                "baseline": b
            }
            st.rerun(scope="app")
    with sim_col4:
        if st.button("🔄 Reset Live", use_container_width=True):
            st.session_state.simulated_overrides.clear()
            st.session_state.paused_state = None
            st.session_state.latched_anomalies.clear()
            st.session_state.pending_diagnosis = None
            st.session_state.approval_message = None
            st.rerun(scope="app")

with col_approval:
    st.subheader("🛡️ Supervisor Approval Panel (Gate)")

    if st.session_state.approval_message:
        st.success(st.session_state.approval_message)

    if st.session_state.get("pending_diagnosis") and st.session_state.paused_state is None:
        target = st.session_state.pending_diagnosis
        st.session_state.pending_diagnosis = None

        with st.spinner(f"🤖 Auto-Sentinel: Diagnosing {target['label']} ({target['value']}) with Gemini..."):
            thread_id = f"diagnosis-{int(time.time() * 1000)}"
            st.session_state.config = {"configurable": {"thread_id": thread_id}}

            initial_state = {
                "metric_path": target["metric"],
                "value": target["value"],
                "context": {},
                "report": "",
                "baseline": target["baseline"],
                "anomalies": [target],
                "recommendation": {},
                "approval": None,
                "action_taken": False
            }

            try:
                result = st.session_state.agent.invoke(initial_state, config=st.session_state.config)
                next_node = st.session_state.agent.get_state(st.session_state.config).next

                if next_node == ("act",):
                    st.session_state.paused_state = result
                    st.session_state.active_anomaly = target
                    st.session_state.approval_message = None
            except Exception as e:
                st.error(f"⚠️ **Sentinel Reasoning Warning:** {e}")

    if st.session_state.paused_state:
        rec = st.session_state.paused_state.get("recommendation", {})
        active_anomalies = st.session_state.paused_state.get("anomalies", [])

        st.error("🚨 **ISSUE REPORTED TO SUPERVISOR — AWAITING OPERATOR DECISION**")
        if active_anomalies:
            a = active_anomalies[0]
            st.markdown(
                f"- **Equipment / Metric:** `{a.get('metric')}`\n"
                f"- **Observed Value:** `{a.get('value')}`\n"
                f"- **InfluxDB Baseline:** `[{a.get('normal_low')}, {a.get('normal_high')}]`"
            )

        st.markdown("#### 🧠 Agent Diagnosis & Analysis")
        st.info(st.session_state.paused_state.get("report", "Analysis in progress..."))

        st.markdown("#### 📋 Recommended Action Plan")
        st.markdown(f"**Action:** `{rec.get('action')}`")
        st.markdown(f"**Urgency:** `{rec.get('urgency')}`")
        st.markdown(f"**Confidence:** `{rec.get('confidence')}`")
        st.markdown(f"**Rationale:** {rec.get('rationale')}")

        st.markdown("---")
        c_app, c_rej = st.columns(2)
        with c_app:
            if st.button("✅ Approve Action", use_container_width=True, type="primary"):
                st.session_state.agent.update_state(
                    st.session_state.config, {"approval": True}
                )
                final = st.session_state.agent.invoke(None, config=st.session_state.config)
                if st.session_state.active_anomaly:
                    st.session_state.latched_anomalies.add(st.session_state.active_anomaly["metric"])
                st.session_state.paused_state = None
                st.session_state.last_result = final

                if final.get("action_taken"):
                    st.session_state.approval_message = "🎉 **Approved! Guardrails verified & Work Order created in `work_orders.json`.**"
                else:
                    st.session_state.approval_message = "🛑 **Blocked by Safety Guardrails! Action violated plant safety policy.**"
                st.rerun(scope="app")

        with c_rej:
            if st.button("❌ Reject Action", use_container_width=True):
                st.session_state.agent.update_state(
                    st.session_state.config, {"approval": False}
                )
                final = st.session_state.agent.invoke(None, config=st.session_state.config)
                if st.session_state.active_anomaly:
                    st.session_state.latched_anomalies.add(st.session_state.active_anomaly["metric"])
                st.session_state.paused_state = None
                st.session_state.last_result = final
                st.session_state.approval_message = "⚠️ **Action rejected by operator. No work order created.**"
                st.rerun(scope="app")
    else:
        st.success("🟢 **No Action Pending Approval**")
        st.caption("When any metric drifts into an issue state in Ignition, the agent automatically diagnoses the line, formulates an advisory, and presents the decision here.")

st.divider()

col_hist1, col_hist2 = st.columns(2)

with col_hist1:
    with st.expander("📋 Maintenance Work Orders (`work_orders.json`)", expanded=True):
        try:
            with open("work_orders.json", "r") as f:
                orders = json.load(f)
            if orders:
                for idx, o in enumerate(reversed(orders[-6:])):
                    st.markdown(f"**Order #{len(orders)-idx}** — `{o.get('timestamp')}` ({o.get('equipment')})")
                    st.markdown(f"- **Action:** `{o.get('action')}`")
                    st.markdown(f"- **Urgency:** {o.get('urgency')} | **Approved by:** `{o.get('approved_by')}`")
                    st.markdown(f"- *Rationale:* {o.get('rationale')}")
                    st.markdown("---")
            else:
                st.write("No work orders created yet.")
        except Exception:
            st.write("No work orders found.")

with col_hist2:
    with st.expander("📜 Agent Audit Trail (`agent_audit_log.jsonl`)", expanded=True):
        try:
            with open("agent_audit_log.jsonl", "r") as f:
                lines = f.readlines()
            if lines:
                for line in reversed(lines[-6:]):
                    data = json.loads(line)
                    status_icon = "🟢" if not data.get("anomalies") else ("✅" if data.get("action_taken") else "🛑")
                    st.markdown(f"{status_icon} **{data.get('timestamp')}** — `{data.get('metric_path')}` (Val: `{data.get('value')}`)")
                    if data.get("anomalies"):
                        st.caption(f"Action: {data.get('recommendation', {}).get('action')} | Executed: {data.get('action_taken')}")
                    else:
                        st.caption("Normal cycle completed.")
            else:
                st.write("Audit log is currently empty.")
        except Exception:
            st.write("No audit log found.")
