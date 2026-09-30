"""One handler, two deployments.

`route()` is framework-agnostic: it takes a plain `Request` and returns a plain
`Response`. `handler()` adapts it to an AWS Lambda function URL (payload format
2.0) and `api/local.py` adapts it to FastAPI, so the routing, auth and validation
are written once and tested once.

No investigation logic lives here. This module reads files, checks a header and
calls `investigator.agent.investigate`.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from investigator import tools
from investigator.agent import HAIKU, SONNET, investigate

# Only these two, and haiku unless asked otherwise — the eval only covers these.
ALLOWED_MODELS = (HAIKU, SONNET)
DEFAULT_MODEL = HAIKU

DEMO_KEY_HEADER = "x-demo-key"
DEFAULT_ALLOWED_ORIGIN = "http://localhost:5173"

# Which report file holds which version of the eval.
REPORT_FILES = {
    "v1": "report_v1.json",
    "v2": "report_v2.json",
    "v2.1": "report.json",
}
REPORT_NOTES = {
    "v1": "Per-category wording: the complaint paraphrased the label.",
    "v2": "Neutral wording shared by every category.",
    "v2.1": "Neutral wording, tools compute refill dates, stricter draft-reply rules.",
}


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    # Lower-cased keys: header names are case-insensitive over the wire.
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass(frozen=True)
class Response:
    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


def data_dir() -> Path:
    return Path(os.environ.get("RXSYNC_DATA_DIR", "data"))


def evals_dir() -> Path:
    return Path(os.environ.get("RXSYNC_EVALS_DIR", "evals"))


def allowed_origin() -> str:
    return os.environ.get("ALLOWED_ORIGIN", DEFAULT_ALLOWED_ORIGIN)


def route(request: Request, *, client: Any = None) -> Response:
    """Dispatch one request. `client` is injected by tests; production passes None."""
    # Preflight must be answered before auth: browsers do not send custom headers
    # on an OPTIONS request, so requiring the key here would block every call.
    if request.method == "OPTIONS":
        return Response(204, None)

    unauthorized = _check_key(request)
    if unauthorized is not None:
        return unauthorized

    path = request.path.rstrip("/") or "/"
    if request.method == "GET" and path == "/examples":
        return Response(200, {"examples": _examples()})
    if request.method == "GET" and path == "/patients":
        return Response(200, {"patients": _patients()})
    if request.method == "GET" and path == "/report":
        return Response(200, {"versions": _report()})
    if request.method == "GET" and path in ("/", "/health"):
        return Response(200, {"ok": True, "models": list(ALLOWED_MODELS)})
    if request.method == "POST" and path == "/investigate":
        return _investigate(request, client)
    return Response(404, {"error": f"no route for {request.method} {path}"})


# --- auth -----------------------------------------------------------------


def _check_key(request: Request) -> Response | None:
    expected = os.environ.get("DEMO_KEY")
    if not expected:
        # A missing server secret is a configuration fault, not a client error.
        # Guarding it explicitly also stops an empty header matching an empty key.
        return Response(500, {"error": "DEMO_KEY is not configured on the server"})
    supplied = request.headers.get(DEMO_KEY_HEADER, "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        return Response(401, {"error": f"missing or invalid {DEMO_KEY_HEADER} header"})
    return None


# --- routes ---------------------------------------------------------------


def _investigate(request: Request, client: Any) -> Response:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        return Response(400, {"error": f"body is not valid JSON: {exc}"})
    if not isinstance(payload, dict):
        return Response(400, {"error": "body must be a JSON object"})

    patient_id = str(payload.get("patient_id") or "").strip()
    ticket_text = str(payload.get("ticket_text") or "").strip()
    model = str(payload.get("model") or DEFAULT_MODEL).strip()

    if not patient_id:
        return Response(400, {"error": "patient_id is required"})
    if not ticket_text:
        return Response(400, {"error": "ticket_text is required"})
    if model not in ALLOWED_MODELS:
        return Response(
            400,
            {"error": f"model must be one of {', '.join(ALLOWED_MODELS)}", "got": model},
        )

    tools.set_data_dir(data_dir())
    result = investigate(ticket_text, patient_id, model, client=client)
    body = result.to_dict()
    # Surfaced at the top level too, because the UI shows them on their own.
    body["cost_usd"] = result.trace.cost_usd
    body["latency_s"] = result.trace.latency_s
    body["model"] = result.trace.model
    return Response(200, body)


def _examples() -> list[dict[str, Any]]:
    """The current ticket set, as prompts a support agent can click."""
    path = evals_dir() / "tickets.jsonl"
    if not path.exists():
        return []
    examples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        ticket = json.loads(line)
        examples.append(
            {
                "ticket_id": ticket["ticket_id"],
                "patient_id": ticket["patient_id"],
                "text": ticket["text"],
                # Handy for a demo, and harmless: the agent never sees this.
                "expected_category": ticket["ground_truth"]["category"],
            }
        )
    return examples


def _patients() -> list[str]:
    return sorted({example["patient_id"] for example in _examples()})


def _report() -> list[dict[str, Any]]:
    versions = []
    for version, filename in REPORT_FILES.items():
        path = evals_dir() / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        versions.append(
            {
                "version": version,
                "note": REPORT_NOTES[version],
                "generated_at": payload.get("generated_at"),
                "runs": [
                    {
                        "label": run["label"],
                        "model": run["model"],
                        "tools_enabled": run["tools_enabled"],
                        "tickets": run["tickets"],
                        "category_accuracy": run["category_accuracy"],
                        "rx_accuracy": run["rx_accuracy"],
                        "rx_scored": run["rx_scored"],
                        "cost_per_ticket_usd": run["cost_per_ticket_usd"],
                        "avg_tool_calls": run["avg_tool_calls"],
                    }
                    for run in payload.get("runs", [])
                ],
            }
        )
    return versions


# --- AWS Lambda function URL ---------------------------------------------


def cors_headers(origin: str | None) -> dict[str, str]:
    """Echo the allowed origin only when the request actually came from it."""
    permitted = allowed_origin()
    if origin != permitted:
        return {}
    return {
        "access-control-allow-origin": permitted,
        "access-control-allow-headers": f"content-type, {DEMO_KEY_HEADER}",
        "access-control-allow-methods": "GET, POST, OPTIONS",
        "access-control-max-age": "600",
        "vary": "origin",
    }


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """AWS Lambda function-URL entry point (payload format 2.0)."""
    request = _request_from_event(event)
    response = route(request)
    headers = {"content-type": "application/json", **response.headers}
    headers.update(cors_headers(request.headers.get("origin")))
    return {
        "statusCode": response.status,
        "headers": headers,
        "body": "" if response.body is None else json.dumps(response.body, default=str),
    }


def _request_from_event(event: dict[str, Any]) -> Request:
    http = (event.get("requestContext") or {}).get("http") or {}
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    body = event.get("body")
    if body and event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8")
    return Request(
        method=str(http.get("method") or event.get("httpMethod") or "GET").upper(),
        path=str(http.get("path") or event.get("rawPath") or "/"),
        headers=headers,
        body=body,
    )
