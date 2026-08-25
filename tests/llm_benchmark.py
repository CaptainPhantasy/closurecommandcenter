"""LLM-First API Benchmark: Test if a small model can use the TCC endpoint.

Sends tool-use prompts to a local Ollama model and checks if it
generates correct tool calls for the /api/llm/do endpoint.
"""

import time
import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
TCC_URL = "http://localhost:11527/api/llm/do"
MODEL = "qwen2.5:3b"

# The tool definition a small model would receive
TCC_TOOL = {
    "type": "function",
    "function": {
        "name": "terminal_control",
        "description": "Send commands to terminals and read their output. Use this tool to interact with terminal sessions.",
        "parameters": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "description": "What to do. One of: list, read, run, stop, start, cancel, answer",
                    "enum": [
                        "list",
                        "read",
                        "run",
                        "stop",
                        "start",
                        "cancel",
                        "answer",
                    ],
                },
                "agent": {
                    "type": "string",
                    "description": "Which terminal to act on (use the agent name)",
                },
                "input": {
                    "type": "string",
                    "description": "Command or text to send to the terminal",
                },
            },
        },
    },
}

# Test scenarios with expected behavior
TESTS = [
    {
        "name": "T1: List terminals (no context)",
        "prompt": "Show me what terminals are available.",
        "expect_action": "list",
        "expect_agent": None,
        "expect_input": None,
    },
    {
        "name": "T2: Run a command",
        "prompt": "Run 'echo hello' on the demo-shell terminal.",
        "expect_action": "run",
        "expect_agent": "demo-shell",
        "expect_input_contains": "echo hello",
    },
    {
        "name": "T3: Read output",
        "prompt": "What's the current output of demo-shell?",
        "expect_action": "read",
        "expect_agent": "demo-shell",
        "expect_input": None,
    },
    {
        "name": "T4: Stop a terminal",
        "prompt": "Stop the demo-shell terminal.",
        "expect_action": "stop",
        "expect_agent": "demo-shell",
        "expect_input": None,
    },
    {
        "name": "T5: Start a terminal",
        "prompt": "Start demo-shell.",
        "expect_action": "start",
        "expect_agent": "demo-shell",
        "expect_input": None,
    },
    {
        "name": "T6: Cancel a running process",
        "prompt": "The demo-shell seems stuck. Cancel whatever it's doing.",
        "expect_action": "cancel",
        "expect_agent": "demo-shell",
        "expect_input": None,
    },
    {
        "name": "T7: Answer a prompt",
        "prompt": "The demo-shell is asking for confirmation. Answer 'yes'.",
        "expect_action": "answer",
        "expect_agent": "demo-shell",
        "expect_input_contains": "yes",
    },
    {
        "name": "T8: Run with different command",
        "prompt": "Execute 'ls -la /tmp' in demo-shell.",
        "expect_action": "run",
        "expect_agent": "demo-shell",
        "expect_input_contains": "ls",
    },
    {
        "name": "T9: Ambiguous but should list",
        "prompt": "What terminals do I have?",
        "expect_action": "list",
        "expect_agent": None,
        "expect_input": None,
    },
    {
        "name": "T10: Check status (should read)",
        "prompt": "Is demo-shell still running? What's it showing?",
        "expect_action": "read",
        "expect_agent": "demo-shell",
        "expect_input": None,
    },
]


def call_ollama(prompt: str) -> dict | None:
    """Send a tool-use prompt to Ollama and return the tool call."""
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant with access to a terminal control tool. "
                    "When the user asks you to interact with terminals, use the terminal_control tool. "
                    "Always use the tool — do not just describe what you would do."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "tools": [TCC_TOOL],
        "stream": False,
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        # Extract tool call from response
        message = data.get("message", {})
        tool_calls = message.get("tool_calls", [])

        if tool_calls:
            func = tool_calls[0].get("function", {})
            return {
                "name": func.get("name", ""),
                "arguments": func.get("arguments", {}),
            }

        # No tool call — model responded with text instead
        return {"name": None, "arguments": {}, "text": message.get("content", "")}

    except Exception as e:
        return {"error": str(e)}


def evaluate_test(test: dict, result: dict) -> dict:
    """Score a test result."""
    if "error" in result:
        return {"pass": False, "reason": f"Error: {result['error']}"}

    if result["name"] is None:
        return {
            "pass": False,
            "reason": f"No tool call. Model said: {result.get('text', '')[:100]}",
        }

    if result["name"] != "terminal_control":
        return {"pass": False, "reason": f"Wrong tool: {result['name']}"}

    args = result.get("arguments", {})
    action = args.get("action", "").lower()
    agent = args.get("agent", "")
    input_val = args.get("input", "")

    # Check action
    if action != test["expect_action"]:
        return {
            "pass": False,
            "reason": f"Expected action '{test['expect_action']}', got '{action}'",
        }

    # Check agent (if expected)
    if test.get("expect_agent"):
        if not agent or test["expect_agent"].lower() not in agent.lower():
            return {
                "pass": False,
                "reason": f"Expected agent containing '{test['expect_agent']}', got '{agent}'",
            }

    # Check input contains (if expected)
    if test.get("expect_input_contains"):
        if (
            not input_val
            or test["expect_input_contains"].lower() not in input_val.lower()
        ):
            return {
                "pass": False,
                "reason": f"Expected input containing '{test['expect_input_contains']}', got '{input_val}'",
            }

    return {"pass": True, "reason": "Correct"}


def run_benchmark():
    """Run all tests and report results."""
    print("=" * 70)
    print(f"  LLM-FIRST API BENCHMARK — Model: {MODEL}")
    print("  Testing: Can a 3B model correctly call POST /api/llm/do?")
    print("=" * 70)
    print()

    results = []
    total_time = 0

    for i, test in enumerate(TESTS):
        print(f"  [{i + 1}/{len(TESTS)}] {test['name']}")
        print(f'       Prompt: "{test["prompt"]}"')

        start = time.time()
        result = call_ollama(test["prompt"])
        elapsed = time.time() - start
        total_time += elapsed

        eval_result = evaluate_test(test, result or {})
        results.append({**test, "result": result, "eval": eval_result, "time": elapsed})

        status = "PASS" if eval_result["pass"] else "FAIL"
        args = result.get("arguments", {}) if result else {}
        print(
            f"       Model called: action={args.get('action', 'N/A')}, agent={args.get('agent', 'N/A')}, input={args.get('input', 'N/A')}"
        )
        print(f"       [{status}] {eval_result['reason']} ({elapsed:.1f}s)")
        print()

    # Summary
    passed = sum(1 for r in results if r["eval"]["pass"])
    failed = len(results) - passed
    pct = (passed / len(results)) * 100

    print("=" * 70)
    print(f"  RESULTS: {passed}/{len(results)} passed ({pct:.0f}%)")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Avg per call: {total_time / len(results):.1f}s")
    print("=" * 70)
    print()

    if failed > 0:
        print("  FAILURES:")
        for r in results:
            if not r["eval"]["pass"]:
                print(f"    - {r['name']}: {r['eval']['reason']}")
        print()

    # Verdict
    if pct >= 90:
        print("  VERDICT: PASS — Interface is small-model friendly.")
    elif pct >= 70:
        print(
            "  VERDICT: MARGINAL — Most scenarios work, some may need simplification."
        )
    else:
        print(
            "  VERDICT: FAIL — Interface needs redesign for small model accessibility."
        )

    return results


if __name__ == "__main__":
    run_benchmark()

