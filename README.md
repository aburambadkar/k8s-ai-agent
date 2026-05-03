# K8s AI Agent — Autonomous Kubernetes Pod Health Monitor

An AI agent that continuously monitors a Kubernetes cluster for unhealthy pods,
uses an LLM to autonomously investigate each failure, and logs a root cause
analysis with a suggested fix — all without human intervention.

---

## What This Project Demonstrates

| Concept | Where it shows up |
|---|---|
| **Agentic loop** | `main.py` — polls the cluster every N seconds indefinitely |
| **Tool use** | `tools/k8s_tools.py` — four kubectl-backed tools the LLM can call |
| **ReAct pattern** | `agent/llm_client.py` — manual Reason → Act → Observe loop |
| **State management** | `agent/state_manager.py` — skips already-diagnosed pods, prunes resolved ones |
| **OpenAI tool-calling API** | `agent/llm_client.py` + `tools/k8s_tools.py` (schema definitions) |
| **Graceful shutdown** | `main.py` — SIGINT/SIGTERM handled cleanly |
| **Simulation mode** | `--simulate` flag — run and demo without a live cluster |
| **Audit logging** | `logs/diagnoses.jsonl` — append-only structured log of every diagnosis |

---

## How the Agent Works

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     K8s AI Agent — One Cycle                           │
│                                                                         │
│  1. DETECT  ──► kubectl get pods --all-namespaces -o json               │
│                 Filter: pods unhealthy > threshold (default 2 min)      │
│                                                                         │
│  2. FILTER  ──► Check state manager                                     │
│                 Skip pods already diagnosed for the same reason         │
│                                                                         │
│  3. DIAGNOSE (for each new pod)                                         │
│     ┌───────────────────────────────────────────────────────────┐       │
│     │        ReAct Loop (Reason → Act → Observe)               │       │
│     │                                                           │       │
│     │  messages = [system_prompt, "investigate this pod"]       │       │
│     │  ┌─────────────────────────────────────────────────┐     │       │
│     │  │  LLM(messages, tools) ──► tool_call?            │     │       │
│     │  │    YES → execute tool, add result to messages   │     │       │
│     │  │    NO  → final answer (root cause + fix) ───────┼──►  │       │
│     │  └─────────────────────────────────────────────────┘     │       │
│     └───────────────────────────────────────────────────────────┘       │
│                                                                         │
│  4. LOG  ──► Append to logs/diagnoses.jsonl                             │
│             Update logs/agent_state.json (mark pod as diagnosed)        │
│                                                                         │
│  5. WAIT  ──► Sleep POLL_INTERVAL_SECONDS, then repeat                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### The ReAct Pattern Explained

Traditional code follows a fixed path. An **AI agent** decides its own path:

```
User:  "Pod 'payments-api' is in CrashLoopBackOff. Investigate."

LLM:   [thinks] "I should look at the logs first."
       → calls get_pod_logs("payments-api", "production")

Tool:  "ERROR: dial tcp db.internal:5432: connect: connection refused"

LLM:   [thinks] "Database connection error. Let me check the pod spec."
       → calls describe_pod("payments-api", "production")

Tool:  "Env: DB_HOST=db.internal, DB_PORT=5432 ..."

LLM:   [thinks] "I have enough to answer now."
       → ROOT CAUSE: Container cannot reach PostgreSQL at db.internal:5432.
         SUGGESTED FIX:
           1. Verify the 'postgres' Service exists in the production namespace.
           2. Check that DB_HOST is set to the correct service name.
           3. Confirm the database pod itself is healthy.
```

The LLM **reasons** about what to do, **acts** by calling tools, then
**observes** results — just like a human SRE would.

---

## Project Structure

```
k8s-ai-agent/
├── main.py                  # Entry point — polling loop + graceful shutdown
├── config.py                # All configuration (env-var overridable)
│
├── tools/
│   ├── __init__.py
│   └── k8s_tools.py         # kubectl wrappers + OpenAI tool JSON schemas
│                              Also contains simulation fixtures
│
├── agent/
│   ├── __init__.py
│   ├── llm_client.py        # OpenAI client + manual ReAct tool-calling loop
│   ├── state_manager.py     # Persistent state (diagnosed pods tracker)
│   └── k8s_agent.py         # Orchestrator — runs one full cycle
│
├── logs/
│   ├── agent_state.json     # [generated] which pods are already diagnosed
│   └── diagnoses.jsonl      # [generated] append-only audit log
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Quickstart

### 1. Clone & install dependencies

```bash
git clone <your-repo-url>
cd k8s-ai-agent

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure the LLM

The agent expects an **OpenAI-compatible** server (LM Studio, Ollama,
LocalAI, llama.cpp, etc.) at the configured base URL.

```bash
# Default — already points at the LAN server
LLM_BASE_URL=http://192.168.50.175:18080/v1

# Override model if the server serves a different model name
export LLM_MODEL=gemma3:4b
```

### 3. Run

```bash
# Against a real cluster (kubectl must be configured)
python main.py

# Demo / portfolio mode — no cluster needed
python main.py --simulate

# Single cycle then exit (great for testing)
python main.py --simulate --once
```

---

## Configuration Reference

All settings are read from environment variables with sensible defaults.

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://192.168.50.175:18080/v1` | OpenAI-compatible server base URL |
| `LLM_MODEL` | `gemma3:4b` | Model name served at LLM_BASE_URL |
| `LLM_API_KEY` | `not-required` | API key (most local servers ignore this) |
| `LLM_TEMPERATURE` | `0.2` | Lower = more focused answers |
| `LLM_MAX_TOOL_ROUNDS` | `8` | Max tool-call rounds per diagnosis |
| `POLL_INTERVAL_SECONDS` | `120` | How often to scan the cluster |
| `UNHEALTHY_THRESHOLD_SECONDS` | `120` | Min duration before a pod is flagged |
| `WATCHED_NAMESPACE` | *(all)* | Restrict to one namespace |
| `REDIAGNOSE_AFTER_HOURS` | `24` | Re-diagnose stale entries after N hours |
| `STATE_FILE` | `logs/agent_state.json` | Where state is persisted |
| `DIAGNOSIS_LOG_FILE` | `logs/diagnoses.jsonl` | Audit log location |
| `SIMULATE` | `false` | Use fake pods for demo/testing |

---

## Available Tools

The LLM can call these tools during diagnosis:

| Tool | Purpose |
|---|---|
| `get_pod_logs` | Last 50 lines from the pod (retries with `--previous` if empty) |
| `describe_pod` | Full `kubectl describe` output — especially the Events section |
| `get_pod_events` | Kubernetes event stream for the pod, sorted by time |
| `get_node_status` | All node statuses — used for scheduling failures |

---

## State Management

The agent tracks diagnosed pods in `logs/agent_state.json`.  A pod is
**skipped** on subsequent cycles if:
- It was already diagnosed for the **same error reason**, AND
- The diagnosis is less than `REDIAGNOSE_AFTER_HOURS` old

A pod is **re-diagnosed** if:
- Its error reason **changed** (new problem)
- It was previously resolved and has **come back**
- The diagnosis is **stale** (older than `REDIAGNOSE_AFTER_HOURS`)

This prevents the LLM from burning compute re-investigating issues that are
already tracked, while still catching new occurrences of the same pod.

---

## Detected Error States

The agent watches for these Kubernetes conditions:

| Reason | What it means |
|---|---|
| `CrashLoopBackOff` | Container keeps crashing; K8s is restarting it in back-off |
| `ImagePullBackOff` / `ErrImagePull` | Cannot pull the container image |
| `OOMKilled` | Container exceeded memory limit and was killed |
| `CreateContainerConfigError` | Missing Secret or ConfigMap referenced by the pod |
| `FailedScheduling` | Pod cannot be placed on any node |
| `Error` | Generic container exit error |
| `Evicted` | Pod evicted due to node resource pressure |

Add or remove entries from `WATCHED_REASONS` in `config.py` to tune scope.

---

## Example Output

```
╔══════════════════════════════════════════════════════════╗
║          🤖  K8s AI Agent — Pod Health Monitor           ║
╚══════════════════════════════════════════════════════════╝
  LLM endpoint : http://192.168.50.175:18080/v1
  Model        : gemma3:4b
  Mode         : SIMULATION MODE

──────────── 🔍 Cycle #1  ·  2024-03-12 08:30:00 UTC ─────────────

  Scanning cluster for unhealthy pods (SIMULATE)…

  ╭─────────────────────── Unhealthy Pods ───────────────────────╮
  │ Namespace    Pod                       Reason           Dur  │
  │ production   payments-api-7d4b9f-xkqr2 CrashLoopBackOff 28m │
  │ ml-services  ml-inference-59b7c-zzp4h  OOMKilled        10m  │
  │ default      report-worker-6fc8-mwp9t  ImagePullBackOff 45m  │
  ╰──────────────────────────────────────────────────────────────╯

  🤖 Diagnosing: production/payments-api-7d4b9f-xkqr2 (CrashLoopBackOff)
  Tools called (2 rounds): get_pod_logs → describe_pod

  ╭─ Diagnosis: payments-api-7d4b9f-xkqr2 ─────────────────────╮
  │                                                              │
  │  ROOT CAUSE: The container is failing because it cannot     │
  │  connect to PostgreSQL at db.internal:5432 on startup.     │
  │                                                              │
  │  SUGGESTED FIX:                                             │
  │  1. Verify the 'postgres' Service exists in 'production'    │
  │  2. Check DB_HOST env var — should match the service name   │
  │  3. Confirm the database pod itself is Running and Ready    │
  │  4. Check NetworkPolicy rules if mTLS is in use             │
  │                                                              │
  ╰──────────────────────────────────────────────────────────────╯
  📄 Logged to logs/diagnoses.jsonl
```

---

## Technologies Used

- **Python 3.11+**
- **openai** — OpenAI-compatible Python client (connects to the local LAN server)
- **rich** — Terminal formatting and tables
- **kubectl** — Kubernetes CLI (must be configured with cluster access)

---

## Ideas for Extension

- **Slack / Teams notifications** — send diagnosis reports to an ops channel
- **Auto-remediation** — for safe actions like restarting a Deployment
- **Web dashboard** — serve `diagnoses.jsonl` as a simple FastAPI endpoint
- **Multi-cluster support** — iterate over multiple kubeconfig contexts
- **Alert deduplication** — group pods by root cause to surface patterns
