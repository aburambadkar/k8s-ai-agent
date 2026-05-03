"""
tools/k8s_tools.py — Kubernetes tool implementations + OpenAI tool schemas.

This module has two responsibilities:
  1. TOOL FUNCTIONS  — plain Python functions that run kubectl commands and
                       return strings.  The agent calls these when the LLM
                       asks for a tool.
  2. TOOL SCHEMAS    — JSON descriptions of each tool in the format the
                       OpenAI chat-completions API expects.  The LLM reads
                       these schemas to know *what tools exist* and *how to
                       call them*.

Simulation mode: when config.SIMULATE is True every function returns
realistic-looking fake data so the agent can be demoed without a live cluster.
"""

import json
import subprocess
from datetime import datetime, timezone

import config

# ─── kubectl helper ───────────────────────────────────────────────────────────

def _run_kubectl(args):
    """Run a kubectl command and return combined stdout/stderr as a string."""
    try:
        result = subprocess.run(
            ["kubectl"] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout or result.stderr
        return output.strip() if output.strip() else "(no output)"
    except FileNotFoundError:
        return "Error: kubectl not found. Is it installed and in your PATH?"
    except subprocess.TimeoutExpired:
        return "Error: kubectl command timed out after 30 seconds."


def _seconds_since(timestamp_str):
    """Return seconds elapsed since a Kubernetes ISO-8601 timestamp."""
    dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).total_seconds()


# ─── Simulation data ──────────────────────────────────────────────────────────

_SIMULATED_PODS = [
    {
        "pod": "payments-api-7d4b9f-xkqr2",
        "namespace": "production",
        "reason": "CrashLoopBackOff",
        "container": "payments-api",
        "restart_count": 14,
        "duration_minutes": 28.4,
    },
    {
        "pod": "ml-inference-59b7c-zzp4h",
        "namespace": "ml-services",
        "reason": "OOMKilled",
        "container": "inference-server",
        "restart_count": 3,
        "duration_minutes": 10.1,
    },
    {
        "pod": "report-worker-6fc8-mwp9t",
        "namespace": "default",
        "reason": "ImagePullBackOff",
        "container": "report-worker",
        "restart_count": 0,
        "duration_minutes": 45.7,
    },
]

_SIMULATED_LOGS = {
    "CrashLoopBackOff": """\
2024-03-12T08:14:33Z INFO  Starting payments-api v2.3.1
2024-03-12T08:14:33Z INFO  Connecting to PostgreSQL at db.internal:5432 ...
2024-03-12T08:14:34Z ERROR dial tcp db.internal:5432: connect: connection refused
2024-03-12T08:14:34Z FATAL Failed to initialise database connection pool (attempt 1/3)
2024-03-12T08:14:35Z FATAL Failed to initialise database connection pool (attempt 2/3)
2024-03-12T08:14:36Z FATAL Failed to initialise database connection pool (attempt 3/3)
2024-03-12T08:14:36Z ERROR Unrecoverable startup error — exiting with code 1
""",
    "OOMKilled": """\
2024-03-12T09:01:11Z INFO  Model loaded: bert-large-uncased (336M params)
2024-03-12T09:01:12Z INFO  Warming up inference cache ...
2024-03-12T09:02:44Z INFO  Batch inference started: 8192 samples
2024-03-12T09:03:01Z WARNING Memory pressure detected: 3.8 GiB / 4.0 GiB used
2024-03-12T09:03:02Z WARNING Memory pressure detected: 3.98 GiB / 4.0 GiB used
[container killed by OOM killer — no further log output]
""",
    "ImagePullBackOff": """\
(no logs — container never started because the image could not be pulled)
""",
}

_SIMULATED_DESCRIBE = {
    "CrashLoopBackOff": """\
Name:         payments-api-7d4b9f-xkqr2
Namespace:    production
Node:         worker-node-1/10.0.1.12
Status:       Running
Containers:
  payments-api:
    Image:         payments-api:v2.3.1
    State:         Waiting
      Reason:      CrashLoopBackOff
    Last State:    Terminated
      Reason:      Error
      Exit Code:   1
    Ready:         False
    Restart Count: 14
Events:
  Warning  BackOff    2m    kubelet  Back-off restarting failed container payments-api
  Warning  Failed     3m    kubelet  Error: failed to create containerd task: ...
  Normal   Pulling    28m   kubelet  Pulling image "payments-api:v2.3.1"
  Normal   Pulled     28m   kubelet  Successfully pulled image
""",
    "OOMKilled": """\
Name:         ml-inference-59b7c-zzp4h
Namespace:    ml-services
Node:         worker-node-3/10.0.1.14
Status:       Running
Containers:
  inference-server:
    Image:         ml-inference:latest
    Limits:
      memory:    4Gi
    State:       Waiting
      Reason:    CrashLoopBackOff
    Last State:  Terminated
      Reason:    OOMKilled
      Exit Code: 137
    Ready:       False
    Restart Count: 3
Events:
  Warning  OOMKilling   10m   kubelet  Memory cgroup out of memory: Kill process 12345 (python3)
  Warning  BackOff      9m    kubelet  Back-off restarting failed container
""",
    "ImagePullBackOff": """\
Name:         report-worker-6fc8-mwp9t
Namespace:    default
Node:         worker-node-2/10.0.1.13
Status:       Pending
Containers:
  report-worker:
    Image:         registry.internal/report-worker:v1.9.0
    State:         Waiting
      Reason:      ImagePullBackOff
    Ready:         False
    Restart Count: 0
Events:
  Warning  Failed     45m   kubelet  Failed to pull image "registry.internal/report-worker:v1.9.0": \
rpc error: code = Unknown desc = failed to pull and unpack image: failed to resolve reference \
"registry.internal/report-worker:v1.9.0": unexpected status code 401 Unauthorized
  Warning  BackOff    44m   kubelet  Back-off pulling image "registry.internal/report-worker:v1.9.0"
""",
}

_SIMULATED_EVENTS = {
    "CrashLoopBackOff": """\
LAST SEEN   TYPE      REASON    OBJECT                              MESSAGE
2m          Warning   BackOff   Pod/payments-api-7d4b9f-xkqr2      Back-off restarting failed container
""",
    "OOMKilled": """\
LAST SEEN   TYPE      REASON       OBJECT                        MESSAGE
10m         Warning   OOMKilling   Pod/ml-inference-59b7c-zzp4h  Memory cgroup out of memory
""",
    "ImagePullBackOff": """\
LAST SEEN   TYPE      REASON    OBJECT                          MESSAGE
44m         Warning   BackOff   Pod/report-worker-6fc8-mwp9t   Back-off pulling image — 401 Unauthorized
""",
}

# ─── Tool functions ───────────────────────────────────────────────────────────

def get_unhealthy_pods(namespace=""):
    """
    Return a JSON list of pods that have been in an error state longer than
    UNHEALTHY_THRESHOLD_SECONDS.  Returns a friendly message when all pods
    are healthy.
    """
    if config.SIMULATE:
        return json.dumps(_SIMULATED_PODS, indent=2)

    args = ["get", "pods", "-o", "json"]
    args += ["--all-namespaces"] if not namespace else ["-n", namespace]

    raw = _run_kubectl(args)
    if raw.startswith("Error"):
        return raw

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"Error parsing kubectl output: {exc}\nRaw: {raw[:500]}"

    unhealthy = _parse_unhealthy_pods(data)
    if not unhealthy:
        return "✅ All pods are healthy"
    return json.dumps(unhealthy, indent=2)


def _parse_unhealthy_pods(data):
    """Walk a pod-list JSON blob and return entries that exceed the threshold."""
    results: list[dict] = []
    threshold = config.UNHEALTHY_THRESHOLD_SECONDS

    for pod in data.get("items", []):
        meta = pod["metadata"]
        status = pod["status"]
        name = meta["name"]
        namespace = meta["namespace"]
        phase = status.get("phase", "Unknown")

        # Phase-level failures (Failed / Unknown)
        if phase in ("Failed", "Unknown"):
            age = _seconds_since(meta["creationTimestamp"])
            if age >= threshold:
                results.append({
                    "pod": name,
                    "namespace": namespace,
                    "reason": phase,
                    "duration_minutes": round(age / 60, 1),
                })
            continue

        # Pending pods (stuck scheduling)
        if phase == "Pending":
            age = _seconds_since(meta["creationTimestamp"])
            if age >= threshold:
                reason = "Pending"
                for cond in status.get("conditions", []):
                    if cond.get("reason") in config.WATCHED_REASONS:
                        reason = cond["reason"]
                        break
                results.append({
                    "pod": name,
                    "namespace": namespace,
                    "reason": reason,
                    "duration_minutes": round(age / 60, 1),
                })
            continue

        # Container-level states (CrashLoopBackOff, OOMKilled, etc.)
        for cs in status.get("containerStatuses", []):
            waiting = cs.get("state", {}).get("waiting", {})
            reason = waiting.get("reason", "")

            if reason not in config.WATCHED_REASONS:
                continue

            # Use the lastTransitionTime of the False condition for accuracy
            stuck_since = None
            for cond in status.get("conditions", []):
                if cond.get("status") == "False" and cond.get("lastTransitionTime"):
                    stuck_since = cond["lastTransitionTime"]
                    break

            duration = (
                _seconds_since(stuck_since)
                if stuck_since
                else _seconds_since(meta["creationTimestamp"])
            )

            if duration >= threshold:
                results.append({
                    "pod": name,
                    "namespace": namespace,
                    "reason": reason,
                    "container": cs.get("name", ""),
                    "restart_count": cs.get("restartCount", 0),
                    "duration_minutes": round(duration / 60, 1),
                })

    return results


def get_pod_logs(pod_name, namespace="default"):
    """
    Fetch the last 50 log lines from a pod.
    Automatically retries with --previous if the current container has no logs
    (common right after a CrashLoopBackOff restart).
    """
    if config.SIMULATE:
        # Find the matching simulated pod to pick the right log fixture
        pod_info = next((p for p in _SIMULATED_PODS if p["pod"] == pod_name), None)
        reason = pod_info["reason"] if pod_info else "CrashLoopBackOff"
        return _SIMULATED_LOGS.get(reason, "(no simulated logs for this error type)")

    logs = _run_kubectl(["logs", "--tail=50", "-n", namespace, pod_name])
    if not logs.strip() or logs.startswith("Error"):
        logs = _run_kubectl(
            ["logs", "--tail=50", "--previous", "-n", namespace, pod_name]
        )
    return logs


def describe_pod(pod_name, namespace="default"):
    """
    Run `kubectl describe pod` for a pod.  The Events section at the bottom
    is the most valuable part — it explains *why* something went wrong.
    """
    if config.SIMULATE:
        pod_info = next((p for p in _SIMULATED_PODS if p["pod"] == pod_name), None)
        reason = pod_info["reason"] if pod_info else "CrashLoopBackOff"
        return _SIMULATED_DESCRIBE.get(reason, "(no simulated describe for this pod)")

    return _run_kubectl(["describe", "pod", "-n", namespace, pod_name])


def get_pod_events(pod_name, namespace="default"):
    """
    Fetch Kubernetes events for a specific pod.
    More targeted than describe — just the event stream.
    """
    if config.SIMULATE:
        pod_info = next((p for p in _SIMULATED_PODS if p["pod"] == pod_name), None)
        reason = pod_info["reason"] if pod_info else "CrashLoopBackOff"
        return _SIMULATED_EVENTS.get(reason, "(no simulated events)")

    return _run_kubectl(
        [
            "get", "events",
            "-n", namespace,
            "--field-selector", f"involvedObject.name={pod_name}",
            "--sort-by=.lastTimestamp",
        ]
    )


def get_node_status():
    """
    Return a summary of all cluster nodes with their status and resource
    pressure.  Useful when diagnosing FailedScheduling issues.
    """
    if config.SIMULATE:
        return """\
NAME             STATUS   ROLES    AGE   VERSION   INTERNAL-IP   CPU    MEMORY
worker-node-1    Ready    <none>   45d   v1.29.0   10.0.1.12     4/4    6.8/8Gi
worker-node-2    Ready    <none>   45d   v1.29.0   10.0.1.13     4/4    7.6/8Gi (MemoryPressure)
worker-node-3    Ready    <none>   45d   v1.29.0   10.0.1.14     4/4    7.9/8Gi (MemoryPressure)
"""

    return _run_kubectl(["get", "nodes", "-o", "wide"])


# ─── Tool registry ────────────────────────────────────────────────────────────

# Maps tool name → Python function.  The LLM client uses this to dispatch calls.
TOOL_REGISTRY: dict[str, callable] = {
    "get_pod_logs": get_pod_logs,
    "describe_pod": describe_pod,
    "get_pod_events": get_pod_events,
    "get_node_status": get_node_status,
}

# OpenAI function-calling schemas — sent to the LLM so it knows what tools
# are available and how to invoke them.
TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": (
                "Fetch the last 50 log lines from a Kubernetes pod. "
                "Use this first when investigating any pod failure — the logs "
                "usually reveal the exact error message causing the crash."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {
                        "type": "string",
                        "description": "The name of the pod (e.g. 'payments-api-7d4b9f-xkqr2')",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "The Kubernetes namespace the pod lives in",
                        "default": "default",
                    },
                },
                "required": ["pod_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_pod",
            "description": (
                "Run kubectl describe on a pod to see its full spec, "
                "resource limits, environment variables, volume mounts, "
                "and the Events section. The Events section is critical "
                "for diagnosing ImagePullBackOff and FailedScheduling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Name of the pod"},
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace",
                        "default": "default",
                    },
                },
                "required": ["pod_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_events",
            "description": (
                "Fetch the Kubernetes event stream for a specific pod. "
                "More focused than describe — shows Warning/Normal events "
                "sorted by time, which is useful for scheduling failures "
                "and image pull errors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string", "description": "Name of the pod"},
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace",
                        "default": "default",
                    },
                },
                "required": ["pod_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_status",
            "description": (
                "Get the status of all cluster nodes including resource "
                "pressure flags (MemoryPressure, DiskPressure). "
                "Call this when a pod is stuck in Pending / FailedScheduling."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
