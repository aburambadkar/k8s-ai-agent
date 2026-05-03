"""
agent/__init__.py - All agent logic in one file: state tracking, LLM diagnosis, and the run cycle.

Structure:
  1. Setup          — LLM client, console, module-level state
  2. State helpers  — load/save/check/update the diagnosed-pods tracker
  3. LLM helpers    — the ReAct tool-calling loop
  4. Display        — rich terminal output
  5. run_cycle()    — the main function called by main.py on every poll
"""

import json
import os
from datetime import datetime, timezone, timedelta

from openai import OpenAI, APIConnectionError, APITimeoutError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

import config
from tools.k8s_tools import get_unhealthy_pods, TOOL_DEFINITIONS, TOOL_REGISTRY


# ─── Setup ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are an expert Kubernetes Site Reliability Engineer (SRE).
Your job is to investigate unhealthy pods, determine the root cause of the
failure, and recommend a concrete fix.

Guidelines:
- Always start by fetching the pod logs — they contain the actual error.
- If the logs don't explain the failure, call describe_pod to inspect events.
- For scheduling failures (FailedScheduling / Pending), call get_node_status.
- Be concise and practical. Operators need actionable advice, not theory.
- End your response with two clearly labelled sections:
    ROOT CAUSE: <one or two sentences>
    SUGGESTED FIX: <numbered steps>
- Do NOT keep reasoning in a loop. Once you have enough information, answer.
"""

console = Console()

# OpenAI-compatible client pointing at the LAN server
_llm_client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)

# Module-level state dict — loaded from disk on the first run_cycle() call
_state = None

# Tracks how many cycles have run since the agent started
_cycle = 0


# ─── State helpers ────────────────────────────────────────────────────────────
# The agent keeps a JSON file that records every pod it has already diagnosed.
# This prevents re-running the LLM on the same pod every polling cycle.

def _load_state():
    """Read state from disk. Returns a fresh empty state if the file is missing."""
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass  # corrupt file — start fresh
    return {"diagnosed": {}, "last_updated": None}


def _save_state():
    """Write the current state to disk. Uses a temp file to avoid partial writes."""
    state_dir = os.path.dirname(config.STATE_FILE)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    tmp = config.STATE_FILE + ".tmp"
    _state["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(tmp, "w") as f:
        json.dump(_state, f, indent=2)
    os.replace(tmp, config.STATE_FILE)


def _should_diagnose(pod, namespace, reason):
    """
    Return True if this pod needs a fresh diagnosis.

    Skip if we already diagnosed it for the same reason and it hasn't timed out.
    Re-diagnose if the error reason changed, it was resolved and came back,
    or the existing diagnosis is older than REDIAGNOSE_AFTER_HOURS.
    """
    key = f"{namespace}/{pod}"
    entry = _state["diagnosed"].get(key)

    if entry is None:
        return True  # never seen before

    if entry["reason"] != reason:
        return True  # different error — something new happened

    if entry["status"] == "resolved":
        return True  # was fixed but came back

    diagnosed_at = datetime.fromisoformat(entry["diagnosed_at"])
    age = datetime.now(timezone.utc) - diagnosed_at
    if age > timedelta(hours=config.REDIAGNOSE_AFTER_HOURS):
        return True  # stale entry

    return False  # already handled, skip


def _mark_diagnosed(pod, namespace, reason):
    """Record that a pod has been diagnosed so we skip it on future cycles."""
    key = f"{namespace}/{pod}"
    _state["diagnosed"][key] = {
        "pod": pod,
        "namespace": namespace,
        "reason": reason,
        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
    }
    _save_state()


def _prune_resolved(current_keys):
    """
    Mark pods as resolved if they no longer appear in the unhealthy list.
    Called once per cycle after fetching the current unhealthy pods.
    """
    resolved = []
    for key, entry in _state["diagnosed"].items():
        if entry["status"] == "active" and key not in current_keys:
            entry["status"] = "resolved"
            resolved.append(key)
    if resolved:
        _save_state()
    return resolved


def _active_count():
    """How many pods are currently in active (unresolved) diagnosed state."""
    return sum(1 for e in _state["diagnosed"].values() if e["status"] == "active")


# ─── LLM helpers ──────────────────────────────────────────────────────────────

def _call_llm(messages):
    """Make one chat-completions request. Returns the message object or None on error."""
    try:
        response = _llm_client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=config.LLM_TEMPERATURE,
        )
        return response.choices[0].message
    except (APIConnectionError, APITimeoutError) as exc:
        print(f"  ⚠️  LLM connection error: {exc}")
        return None
    except Exception as exc:
        print(f"  ⚠️  Unexpected LLM error: {exc}")
        return None


def _execute_tool(name, args):
    """Look up and run a tool by name. Returns the result as a string."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return f"Error: unknown tool '{name}'"
    try:
        return str(fn(**args))
    except TypeError as exc:
        return f"Error calling {name}({args}): {exc}"


def _diagnose_pod(pod_name, namespace, reason):
    """
    The ReAct loop: Reason → Act → Observe until the LLM produces a final answer.

    How it works:
      1. We send the pod problem to the LLM along with the available tool schemas
      2. The LLM reasons about what information it needs and calls a tool
         (e.g. get_pod_logs)
      3. We run that tool and feed the result back into the conversation
      4. The LLM reasons over the new information and either calls another tool
         or produces a final ROOT CAUSE / SUGGESTED FIX answer
      5. Repeat until a final answer is reached or max rounds hit
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Pod '{pod_name}' in namespace '{namespace}' has been in state "
            f"'{reason}' for an extended period. Please investigate and "
            f"provide the root cause and a suggested fix."
        )},
    ]

    tool_call_log = []

    for round_num in range(1, config.LLM_MAX_TOOL_ROUNDS + 1):
        response = _call_llm(messages)

        if response is None:
            return {
                "diagnosis": "⚠️  LLM call failed — check connectivity to the LAN server.",
                "tool_calls": tool_call_log,
                "rounds": round_num,
            }

        # LLM wants to call a tool
        if response.tool_calls:
            # Add the assistant's decision to the conversation history
            # (built manually — model_dump() includes fields llama.cpp rejects)
            messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response.tool_calls
                ],
            })

            for tc in response.tool_calls:
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments or "{}")
                tool_result = _execute_tool(tool_name, tool_args)

                # Keep a log of every tool call for display and audit
                tool_call_log.append({
                    "round": round_num,
                    "tool": tool_name,
                    "args": tool_args,
                    "result": tool_result,
                })

                # Feed the tool output back so the LLM can reason over it
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

            continue  # next round — let the LLM reason over the new info

        # LLM gave a final text answer
        if response.content:
            return {
                "diagnosis": response.content.strip(),
                "tool_calls": tool_call_log,
                "rounds": round_num,
            }

        return {
            "diagnosis": "⚠️  LLM returned an empty response.",
            "tool_calls": tool_call_log,
            "rounds": round_num,
        }

    return {
        "diagnosis": f"⚠️  Agent hit the {config.LLM_MAX_TOOL_ROUNDS}-round limit without a final answer.",
        "tool_calls": tool_call_log,
        "rounds": config.LLM_MAX_TOOL_ROUNDS,
    }


# ─── Display helpers ──────────────────────────────────────────────────────────

def _print_unhealthy_table(pods):
    """Print a summary table of all currently unhealthy pods."""
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title="[bold]Unhealthy Pods[/bold]",
        title_style="bold red",
    )
    table.add_column("Namespace", style="cyan",    no_wrap=True)
    table.add_column("Pod",       style="white",   no_wrap=True)
    table.add_column("Reason",    style="red bold")
    table.add_column("Duration",  style="yellow",  justify="right")
    table.add_column("Restarts",  style="magenta", justify="right")
    table.add_column("Status",    style="green")

    for p in pods:
        is_new = _should_diagnose(p["pod"], p["namespace"], p["reason"])
        status_text = Text("NEW ✦", style="bold green") if is_new else Text("skip ✓", style="dim")
        table.add_row(
            p["namespace"],
            p["pod"],
            p["reason"],
            f"{p.get('duration_minutes', '?')}m",
            str(p.get("restart_count", "-")),
            status_text,
        )

    console.print(table)


def _append_log(entry):
    """Append one diagnosis record to the JSONL audit log."""
    log_dir = os.path.dirname(config.DIAGNOSIS_LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    with open(config.DIAGNOSIS_LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _diagnose_and_log(pod_info):
    """Run the LLM diagnosis for one pod, print the result, and write to the log."""
    pod    = pod_info["pod"]
    ns     = pod_info["namespace"]
    reason = pod_info["reason"]

    console.print(
        f"\n  [bold yellow]🤖 Diagnosing:[/bold yellow] "
        f"[cyan]{ns}/{pod}[/cyan]  "
        f"[red]({reason})[/red]"
    )

    result     = _diagnose_pod(pod, ns, reason)
    diagnosis  = result["diagnosis"]
    tool_calls = result["tool_calls"]
    rounds     = result["rounds"]

    # Print the evidence the LLM gathered before reaching its conclusion
    if tool_calls:
        tools_used = [f"[bold]{tc['tool']}[/bold]" for tc in tool_calls]
        console.print(
            f"  [dim]Tools called ({rounds} round(s)): "
            + " → ".join(tools_used) + "[/dim]"
        )

        evidence_lines = []
        for tc in tool_calls:
            header = f"[bold cyan]▶ {tc['tool']}[/bold cyan]"
            args_str = ", ".join(f"{k}={v!r}" for k, v in tc["args"].items())
            if args_str:
                header += f"[dim]({args_str})[/dim]"
            evidence_lines.append(header)
            output = tc["result"].strip()
            if len(output) > 1200:
                output = output[:1200] + "\n[dim]… (truncated — full output in diagnoses.jsonl)[/dim]"
            evidence_lines.append(output)
            evidence_lines.append("")

        console.print(
            Panel(
                "\n".join(evidence_lines).strip(),
                title=f"[bold]Evidence Gathered: {pod}[/bold]",
                subtitle=f"[dim]{ns}[/dim]",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Print the LLM's final diagnosis
    console.print(
        Panel(
            diagnosis,
            title=f"[bold]Diagnosis: {pod}[/bold]",
            subtitle=f"[dim]{ns}[/dim]",
            border_style="yellow",
            padding=(1, 2),
        )
    )

    # Write to the audit log
    _append_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cycle": _cycle,
        "pod": pod,
        "namespace": ns,
        "reason": reason,
        "duration_minutes": pod_info.get("duration_minutes"),
        "restart_count": pod_info.get("restart_count"),
        "tool_calls": tool_calls,
        "llm_rounds": rounds,
        "diagnosis": diagnosis,
    })

    _mark_diagnosed(pod, ns, reason)
    console.print(f"  [dim]📄 Logged to {config.DIAGNOSIS_LOG_FILE}[/dim]")


# ─── Main cycle ───────────────────────────────────────────────────────────────

def run_cycle():
    """
    One full detection + diagnosis pass. Called by main.py on every poll interval.

    Steps:
      1. Fetch all currently unhealthy pods from the cluster (or simulator)
      2. Prune pods that have recovered since last cycle
      3. For each pod not already in state: diagnose, log, mark as handled
      4. Print a cycle summary
    """
    global _state, _cycle

    # Load state from disk on the very first call
    if _state is None:
        _state = _load_state()

    _cycle += 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    console.rule(
        f"[bold cyan]🔍 Cycle #{_cycle}  ·  {now}[/bold cyan]",
        style="cyan",
    )

    mode_label = "[yellow](SIMULATE)[/yellow]" if config.SIMULATE else ""
    console.print(f"  Scanning cluster for unhealthy pods {mode_label}…")

    raw = get_unhealthy_pods(config.WATCHED_NAMESPACE)

    if "✅" in raw:
        console.print("  [green]✅ All pods are healthy — nothing to do.[/green]\n")
        return

    try:
        unhealthy_pods = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  [red]⚠️  Could not parse pod list: {raw}[/red]\n")
        return

    current_keys = {f"{p['namespace']}/{p['pod']}" for p in unhealthy_pods}

    resolved = _prune_resolved(current_keys)
    if resolved:
        console.print(
            f"  [green]✔ {len(resolved)} pod(s) resolved since last cycle:[/green] "
            + ", ".join(resolved)
        )

    _print_unhealthy_table(unhealthy_pods)

    new_count     = 0
    skipped_count = 0

    for pod_info in unhealthy_pods:
        if not _should_diagnose(pod_info["pod"], pod_info["namespace"], pod_info["reason"]):
            skipped_count += 1
            continue
        new_count += 1
        _diagnose_and_log(pod_info)

    console.print(
        f"\n  [dim]Cycle #{_cycle} complete · "
        f"{new_count} diagnosed · "
        f"{skipped_count} skipped (already handled) · "
        f"{_active_count()} total active issues[/dim]\n"
    )
