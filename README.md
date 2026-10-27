# Plant Sentinel: Autonomous Industrial Supervisory Agent

An autonomous supervisory AI system designed for process manufacturing environments. It ingests high-frequency operational telemetry from SCADA via MQTT/Sparkplug B, detects anomalies against 48-hour rolling historical baselines in InfluxDB, performs root cause diagnosis using Gemini models, and presents actionable recommendations through a human-in-the-loop approval gate before generating maintenance work orders.

Built around the ISA-95 equipment hierarchy for a chemical manufacturing plant (JK GmbH, Bristol site, Polymerization Line 3).

---

## Architecture Overview

```
                      +-----------------------------+
                      |   Ignition SCADA / PLC      |
                      |  (HiveMQ MQTT Broker :1883) |
                      +--------------+--------------+
                                     |  Sparkplug B (Protobuf)
                                     v
                      +-----------------------------+
                      |     agent1_listener.py      |
                      |   Background Live Buffer    |
                      +--------------+--------------+
                                     |
               +---------------------+---------------------+
               |                                           |
               v                                           v
+-----------------------------+             +-----------------------------+
|        InfluxDB Cloud       |             |   Streamlit Oversight UI    |
|   48h Rolling Process Hist  |             |  (Live Telemetry & Sentinel)|
+--------------+--------------+             +--------------+--------------+
               |                                           |
               +---------------------+---------------------+
                                     |
                                     v
                      +-----------------------------+
                      |   LangGraph Agent Pipeline  |
                      |   (agent4_supervised.py)    |
                      +--------------+--------------+
                                     |
                  [ Observe -> Detect -> Diagnose -> Recommend ]
                                     |
                                     v
                       +---------------------------+
                       |   Human Approval Gate     |
                       |  (Operator Review Panel)  |
                       +-------------+-------------+
                                     |
                       +-------------+-------------+
                       |                           |
                       v                           v
                 [ Approve ]                  [ Reject ]
                       |                           |
                       v                           v
         +---------------------------+      +--------------+
         |    Safety Guardrails      |      | Audit Logged |
         |   (Action & Asset Check)  |      +--------------+
         +-------------+-------------+
                       |
                       v
         +---------------------------+
         |  work_orders.json         |
         |  agent_audit_log.jsonl    |
         +---------------------------+
```

---

## Monitored Equipment & Process Points

The system monitors three primary telemetry points on Polymerization Line 3:

| Metric Path | Tag Name | Engineering Unit | Nominal Operating Baseline |
| :--- | :--- | :--- | :--- |
| `Bristol/Polymerization/Line 3/Reactor 2/Temperature` | Reactor 2 Temp | °C | ~65.0 – 75.0 °C (Safety: 95.0 °C) |
| `Bristol/Polymerization/Line 3/Reactor 2/Pressure` | Reactor 2 Pressure | psi | ~98.0 – 104.5 psi (Safety: 110.0 psi) |
| `Bristol/Polymerization/Line 3/CC-01/FlowRate` | CC-01 Coolant Flow | gpm | ~11.5 – 13.5 gpm (Safety Min: 8.0 gpm) |

Baselines are calculated dynamically from historical data stored in InfluxDB using a rolling 48-hour two-sigma window ($\mu \pm 2\sigma$).

---

## Core Modules

- **`agent1_listener.py`**: Paho MQTT client that subscribes to Sparkplug B `DDATA` and `DBIRTH` topics, decodes raw Google Protocol Buffer payloads, and maintains an in-memory live tag buffer. Automatically sends rebirth commands (`Node Control/Rebirth`) upon connect.
- **`baseline.py`**: Queries InfluxDB Cloud 3.0 via Flux to calculate mean, standard deviation, and normal operating limits for any metric path over an arbitrary lookback window.
- **`seed_baseline.py`**: Utility script to seed 48 hours of synthetic process history into InfluxDB with realistic diurnal sine oscillations and Gaussian noise.
- **`agent4_supervised.py`**: Main LangGraph state graph.
  - `observe`: Pulls current values from the live buffer.
  - `detect`: Compares readings to InfluxDB baselines.
  - `diagnose`: Synthesizes line correlation (e.g., cooling flow loss vs. reactor temperature rise) using Gemini. Includes automatic model failover (`gemini-3.5-flash-lite` -> `gemini-3.5-flash` -> `gemini-2.5-flash`) and an internal deterministic rule fallback if API quotas are exhausted.
  - `recommend`: Formulates structured advisories with action, urgency, confidence, and time-to-limit calculations.
  - `act`: Evaluates the proposal against deterministic safety guardrails and writes approved maintenance orders.
  - `report` / `log_cycle`: Writes full trajectory records to `agent_audit_log.jsonl`.
- **`guardrails.py`**: Deterministic safety checks. Verifies that actions use permitted verbs (`inspect`, `schedule`, `notify`, `check`, `verify`, `monitor`) and valid assets (`Reactor2`, `CC-01`, `P-203`), and explicitly blocks prohibited keywords (`adjust setpoint`, `disable interlock`, `bypass`).
- **`oversight_dashboard.py`**: Streamlit web dashboard. Separates high-frequency DOM refreshes (2-second live streaming telemetry tiles) from long-running LLM diagnosis calls to prevent race conditions. Features a dual-column layout with an interactive operator gate and anomaly simulation suite.
- **`agent_mcp_server.py`**: Model Context Protocol (MCP) server that exposes plant telemetry (`read_live_data`, `read_history`, `list_ignition_tags`) and work order execution (`create_work_order`) to external agent runtimes.

---

## Prerequisites

- **Python**: 3.11 or higher
- **MQTT Broker**: HiveMQ or Mosquitto running on `localhost:1883`
- **SCADA Gateway**: Ignition 8.1+ with Cirrus Link MQTT Transmission module (or simulator)
- **Database**: InfluxDB Cloud or InfluxDB OSS 2.x
- **LLM API**: Google Gemini API key

---

## Environment Setup

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key_here
INFLUX_URL=https://us-east-1-1.aws.cloud2.influxdata.com
INFLUX_TOKEN=your_influxdb_read_write_token
INFLUX_ORG=your_influx_org_id_or_name
INFLUX_BUCKET=plant_data
```

Install dependencies:

```bash
pip install streamlit langgraph paho-mqtt protobuf influxdb-client google-genai python-dotenv mcp
```
---

## Running the Application

### 1. Seed Historical Baselines
Before running the agent, populate InfluxDB with 48 hours of normal operational history:

```bash
python seed_baseline.py
```

### 2. Start the Streamlit Oversight Dashboard
Launch the operator interface:

```bash
streamlit run oversight_dashboard.py
```

Open `http://localhost:8501` in your browser.

- The left column streams live tag readings every 2 seconds from the MQTT broker.
- When a metric exceeds its InfluxDB baseline, the agent automatically diagnoses the line and populates the **Supervisor Approval Panel** on the right.
- The operator can click **Approve Action** or **Reject Action**. Approved orders are saved to `work_orders.json`.
- The **Anomaly Simulator** at the bottom allows injecting temporary test excursions (high temperature, low pressure, high flow) without touching the live PLC.

### 3. Running CLI Agents Individually
To run the automated observer cycle:

```bash
python agent1_observer.py
```

To run the full supervised loop in headless terminal mode:

```bash
python agent4_supervised.py
```

### 4. Running the Model Context Protocol (MCP) Server
To expose plant tools to Claude Desktop or any MCP-compatible agent:

```bash
python agent_mcp_server.py
```

Verify tool connectivity using the bundled test client:

```bash
python test_mcp_client.py
```

---

## Safety & Governance Design

1. **Human-in-the-Loop Interrupt Gate**: LangGraph checkpointer compiles the graph with `interrupt_before=["act"]`. The agent can never perform an action or file a ticket without explicit operator sign-off.
2. **Deterministic Code Guardrails**: Even when an operator approves a recommendation, the execution node re-validates the proposed action string against strict whitelists and blacklists in `guardrails.py`.
3. **Audit Trail**: Every execution cycle—normal operation, anomaly detection, operator decision, guardrail check, and action outcome—is appended as a JSON record to `agent_audit_log.jsonl`.
