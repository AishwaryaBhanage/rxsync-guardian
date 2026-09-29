"""The investigation loop: a ticket in, a cited diagnosis and a full trace out.

The agent gets the four read-only tools from `tools.py` plus one terminator,
`submit_diagnosis`. It may make at most `max_tool_calls` investigative calls; once
that budget is gone the next turn is offered only `submit_diagnosis`, so a run
always ends in a decision rather than wandering.

Model ids and prices come from the Claude API reference, not from memory:

* `claude-haiku-4-5` — $1.00 / $5.00 per MTok (the default here)
* `claude-sonnet-5` — $2.00 / $10.00 per MTok

Neither `thinking` nor `output_config.effort` is sent. Haiku 4.5 rejects `effort`
and only takes the older `budget_tokens` thinking form, while Sonnet 5 wants
`adaptive` — omitting both is the one configuration that is valid on either model.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from investigator.tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

# Canonical ids from the Claude API reference. The dated spelling is accepted as an
# alias because it is what the model catalogue publishes for the same model.
HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"
DEFAULT_MODEL = HAIKU

_MODEL_ALIASES = {"claude-haiku-4-5-20251001": HAIKU}

# USD per million tokens: (input, output).
PRICING: dict[str, tuple[float, float]] = {
    HAIKU: (1.00, 5.00),
    SONNET: (2.00, 10.00),
}

# Cache multipliers on the input rate, per the reference.
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.1

MAX_TOOL_CALLS = 8
MAX_TOKENS = 16_000

CATEGORIES = (
    "duplicate",
    "dropped",
    "stale_status",
    "phantom_schedule",
    "no_issue_found",
)

SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_diagnosis",
    "description": (
        "Finish the investigation. Call this exactly once, when you have gathered "
        "enough evidence from the other tools to explain what happened — or when "
        "you have concluded that nothing is wrong."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": list(CATEGORIES),
                "description": (
                    "duplicate: the same prescription is listed more than once. "
                    "dropped: a real prescription is missing from the app. "
                    "stale_status: the app shows an out-of-date status. "
                    "phantom_schedule: an automatic refill never happened. "
                    "no_issue_found: the records and the app agree."
                ),
            },
            "rx_number": {
                "type": ["string", "null"],
                "description": (
                    "The prescription this is about, exactly as a tool returned it. "
                    "Use null if no single prescription is implicated or if no tool "
                    "gave you one. Never invent or guess an rx number."
                ),
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Record ids and rx numbers you actually saw in tool results, "
                    "and which support this conclusion."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How sure you are, 0 to 1. Use a low value when the evidence is "
                    "thin; do not overstate."
                ),
            },
            "draft_reply": {
                "type": "string",
                "description": (
                    "A short, friendly reply for a human colleague to approve "
                    "before it reaches the patient. Plain language, no medical "
                    "advice of any kind."
                ),
            },
        },
        "required": ["category", "rx_number", "evidence", "confidence", "draft_reply"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You investigate complaints about a pharmacy app on behalf of a \
support team. A patient says something looks wrong; your job is to work out what \
actually happened and draft a reply for a human colleague to approve.

You have tools for two different things, and the difference matters:
- get_patient_view shows what the app displayed to the patient. It can be wrong.
- get_pharmacy_records shows what the pharmacy's own records say. Treat it as the
  truth and compare the two.
Use get_rx_history to see what really happened to one prescription and when, and
get_pharmacy_speed to judge whether a wait is normal for that pharmacy.

Rules you must follow:
- Cite only evidence that a tool actually returned to you. Every entry in
  `evidence` must be a record id or rx number that appeared in a tool result in
  this conversation.
- Never guess an rx_number. If no tool gave you one, pass null.
- If the evidence does not settle the question, say so: give a low confidence and
  explain the uncertainty rather than inventing a cause. A careful "I am not sure"
  is a better answer than a confident wrong one.
- The draft reply goes to a patient once a human approves it. Keep it short and
  friendly, say what you found in plain words, and give no medical advice — never
  suggest starting, stopping, changing or delaying any medicine. If the patient
  needs clinical guidance, say a pharmacist will follow up.
- A tool may return {"error": ...}. Read it and adjust; it is not a crash.

Finish by calling submit_diagnosis exactly once."""


class _MessagesClient(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Client(Protocol):
    @property
    def messages(self) -> _MessagesClient: ...


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation, with what went in and what came back."""

    name: str
    input: dict[str, Any]
    output: Any
    is_error: bool = False


@dataclass(frozen=True)
class Diagnosis:
    category: str
    rx_number: str | None
    evidence: list[str]
    confidence: float
    draft_reply: str


@dataclass(frozen=True)
class Trace:
    model: str
    api_calls: int
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    latency_s: float
    stop_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass(frozen=True)
class Investigation:
    diagnosis: Diagnosis | None
    trace: Trace
    # Ids the model cited that never appeared in a tool result. Not silently
    # dropped: an eval wants to know the agent over-claimed.
    unverified_evidence: list[str] = field(default_factory=list)
    incomplete_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": asdict(self.diagnosis) if self.diagnosis else None,
            "trace": asdict(self.trace),
            "unverified_evidence": self.unverified_evidence,
            "incomplete_reason": self.incomplete_reason,
        }


def resolve_model(model: str) -> str:
    """Canonical id for a supported model, raising on anything else."""
    resolved = _MODEL_ALIASES.get(model, model)
    if resolved not in PRICING:
        supported = ", ".join(sorted(PRICING))
        raise ValueError(f"unsupported model {model!r}; supported: {supported}")
    return resolved


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Dollar cost of one run, from the reference's per-MTok rates."""
    input_rate, output_rate = PRICING[resolve_model(model)]
    dollars = (
        input_tokens * input_rate
        + output_tokens * output_rate
        + cache_write_tokens * input_rate * _CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * input_rate * _CACHE_READ_MULTIPLIER
    ) / 1_000_000
    return round(dollars, 6)


def default_client() -> _Client:
    """A real client, with ANTHROPIC_API_KEY pulled from `.env` if present."""
    import anthropic
    from dotenv import load_dotenv

    load_dotenv()  # does not override a variable already in the environment
    return anthropic.Anthropic()


def investigate(
    ticket_text: str,
    patient_id: str,
    model: str = DEFAULT_MODEL,
    *,
    client: _Client | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
) -> Investigation:
    """Investigate one ticket. Pass `client` to inject a fake in tests."""
    model = resolve_model(model)
    if client is None:
        client = default_client()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _opening_message(ticket_text, patient_id, max_tool_calls)}
    ]
    calls: list[ToolCall] = []
    seen_ids: set[str] = set()
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    api_calls = 0
    stop_reason: str | None = None
    diagnosis: Diagnosis | None = None
    unverified: list[str] = []
    incomplete: str | None = None

    started = time.perf_counter()
    # One iteration per API round trip. The +2 leaves room for the forced-submit
    # turn and its response after the investigative budget is spent.
    for _ in range(max_tool_calls + 2):
        investigative_used = sum(1 for call in calls if call.name != SUBMIT_TOOL["name"])
        budget_left = max(0, max_tool_calls - investigative_used)
        offered = [*TOOL_SCHEMAS, SUBMIT_TOOL] if budget_left else [SUBMIT_TOOL]

        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=offered,
            messages=messages,
        )
        api_calls += 1
        _accumulate(totals, getattr(response, "usage", None))
        stop_reason = getattr(response, "stop_reason", None)

        if stop_reason == "refusal":
            incomplete = "the model declined to answer"
            break

        blocks = list(response.content)
        requests = [block for block in blocks if getattr(block, "type", None) == "tool_use"]
        if not requests:
            incomplete = "the model stopped without calling submit_diagnosis"
            break

        messages.append({"role": "assistant", "content": blocks})

        results: list[dict[str, Any]] = []
        submitted = None
        for block in requests:
            arguments = dict(block.input)
            if block.name == SUBMIT_TOOL["name"]:
                submitted, problem = _parse_diagnosis(arguments)
                calls.append(ToolCall(block.name, arguments, problem or "accepted", bool(problem)))
                results.append(_result_block(block.id, problem or "accepted", bool(problem)))
                if problem:
                    submitted = None
                continue

            output, failed = _run_tool(block.name, arguments)
            calls.append(ToolCall(block.name, arguments, output, failed))
            _collect_ids(output, seen_ids)
            results.append(_result_block(block.id, output, failed))

        # Every tool_result for one assistant turn goes back in a single user
        # message; splitting them teaches the model to stop calling in parallel.
        messages.append({"role": "user", "content": results})

        if submitted is not None:
            diagnosis = submitted
            unverified = [item for item in submitted.evidence if item not in seen_ids]
            break

        if budget_left and not _has_budget_after(calls, max_tool_calls):
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You have used your tool budget. Call submit_diagnosis now "
                        "with whatever you have established, using a low confidence "
                        "if the evidence is thin."
                    ),
                }
            )
    else:
        incomplete = "ran out of turns before submitting a diagnosis"

    latency = time.perf_counter() - started
    if diagnosis is None and incomplete is None:
        incomplete = "no diagnosis was submitted"

    trace = Trace(
        model=model,
        api_calls=api_calls,
        tool_calls=calls,
        input_tokens=totals["input"],
        output_tokens=totals["output"],
        cache_read_tokens=totals["cache_read"],
        cache_write_tokens=totals["cache_write"],
        cost_usd=cost_usd(
            model, totals["input"], totals["output"], totals["cache_read"], totals["cache_write"]
        ),
        latency_s=round(latency, 3),
        stop_reason=stop_reason,
    )
    return Investigation(
        diagnosis=diagnosis,
        trace=trace,
        unverified_evidence=unverified,
        incomplete_reason=incomplete,
    )


# --- internals ------------------------------------------------------------


def _opening_message(ticket_text: str, patient_id: str, budget: int) -> str:
    return (
        f"Support ticket from patient {patient_id}:\n\n{ticket_text}\n\n"
        f"Investigate this. You have at most {budget} tool calls before you must "
        f"submit a diagnosis."
    )


def _run_tool(name: str, arguments: dict[str, Any]) -> tuple[Any, bool]:
    """Run one of the investigative tools. Unknown tool names are a tool error."""
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"error": f"no such tool {name!r}"}, True
    try:
        output = function(**arguments)
    except TypeError as exc:  # wrong or missing arguments
        return {"error": f"bad arguments for {name}: {exc}"}, True
    failed = isinstance(output, dict) and "error" in output
    return output, failed


def _result_block(tool_use_id: str, output: Any, is_error: bool) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(output, default=str),
        "is_error": is_error,
    }


def _parse_diagnosis(arguments: dict[str, Any]) -> tuple[Diagnosis | None, str | None]:
    """Validate a submitted diagnosis, returning a message to feed back on failure."""
    category = arguments.get("category")
    if category not in CATEGORIES:
        return None, f"error: category must be one of {', '.join(CATEGORIES)}"

    confidence = arguments.get("confidence")
    if not isinstance(confidence, int | float) or not 0 <= float(confidence) <= 1:
        return None, "error: confidence must be a number between 0 and 1"

    reply = arguments.get("draft_reply")
    if not isinstance(reply, str) or not reply.strip():
        return None, "error: draft_reply must be a non-empty string"

    evidence = arguments.get("evidence") or []
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        return None, "error: evidence must be a list of strings"

    rx_number = arguments.get("rx_number")
    if rx_number is not None and not isinstance(rx_number, str):
        return None, "error: rx_number must be a string or null"

    return (
        Diagnosis(
            category=category,
            rx_number=rx_number,
            evidence=list(evidence),
            confidence=float(confidence),
            draft_reply=reply.strip(),
        ),
        None,
    )


def _collect_ids(output: Any, seen: set[str]) -> None:
    """Remember every record id and rx number a tool handed back."""
    if isinstance(output, dict):
        for key, value in output.items():
            if key in {"record_id", "rx_number"} and isinstance(value, str):
                seen.add(value)
            else:
                _collect_ids(value, seen)
    elif isinstance(output, list):
        for item in output:
            _collect_ids(item, seen)


def _has_budget_after(calls: list[ToolCall], max_tool_calls: int) -> bool:
    used = sum(1 for call in calls if call.name != SUBMIT_TOOL["name"])
    return used < max_tool_calls


def _accumulate(totals: dict[str, int], usage: Any) -> None:
    if usage is None:
        return
    totals["input"] += getattr(usage, "input_tokens", 0) or 0
    totals["output"] += getattr(usage, "output_tokens", 0) or 0
    totals["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    totals["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
