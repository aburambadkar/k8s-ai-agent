"""
config.py — Central configuration for the K8s AI Agent.

All values can be overridden with environment variables so the project
works in any environment without changing source code.
"""

import os

# ─── LLM Settings ─────────────────────────────────────────────────────────────

# Base URL of an OpenAI-compatible API (LM Studio, Ollama, LocalAI, llama.cpp, etc.)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.50.175:18080/v1")

# Most local servers accept any non-empty string as the API key
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-required")

# Name of the model served at LLM_BASE_URL
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b-mxfp4")

# Higher temperature = more creative/varied responses (0.0 = deterministic, 1.0 = creative)
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.8"))

# Maximum tool-call rounds per diagnosis (prevents infinite loops)
LLM_MAX_TOOL_ROUNDS = int(os.getenv("LLM_MAX_TOOL_ROUNDS", "8"))

# ─── Polling / Detection ──────────────────────────────────────────────────────

# How often the agent wakes up to scan the cluster (seconds)
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

# A pod must be unhealthy for at least this long before we act on it.
# This prevents alerts for transient restarts during rolling deployments.
UNHEALTHY_THRESHOLD_SECONDS = int(os.getenv("UNHEALTHY_THRESHOLD_SECONDS", "60"))

# Namespace to watch — leave empty to watch all namespaces
WATCHED_NAMESPACE = os.getenv("WATCHED_NAMESPACE", "")

# How many hours before we re-diagnose a pod that was already handled
# (useful if the same pod comes back after being "fixed")
REDIAGNOSE_AFTER_HOURS = int(os.getenv("REDIAGNOSE_AFTER_HOURS", "24"))

# Kubernetes error states the agent cares about.
# Add or remove entries here to tune the detection scope.
WATCHED_REASONS = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "OOMKilled",
    "CreateContainerConfigError",
    "FailedScheduling",
    "Error",
    "Evicted",
}

# ─── State & Logging ──────────────────────────────────────────────────────────

# JSON file where the agent persists its "already diagnosed" state
STATE_FILE = os.getenv("STATE_FILE", "logs/agent_state.json")

# Append-only JSONL file — one diagnosis record per line
DIAGNOSIS_LOG_FILE = os.getenv("DIAGNOSIS_LOG_FILE", "logs/diagnoses.jsonl")

# ─── Simulation Mode ──────────────────────────────────────────────────────────

# When True, the agent uses fake unhealthy pods instead of a real cluster.
# Useful for demos, CI, and portfolio walkthroughs without a live k8s env.
SIMULATE = os.getenv("SIMULATE", "false").lower() in ("1", "true", "yes")
