"""Tests for the HTTP surface, driven by a scripted fake client.

No network. The same `route()` serves both the Lambda handler and the FastAPI
wrapper, so testing it once covers both deployments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from api import handler as api
from api.handler import ALLOWED_MODELS, DEFAULT_MODEL, Request, handler, route
from investigator import tools
from investigator.agent import HAIKU, SONNET
from simulator.config import SimConfig
from simulator.generate import generate

KEY = "test-demo-key"


# --- a scripted stand-in for anthropic.Anthropic --------------------------


@dataclass
class FakeUsage:
    input_tokens: int = 120
    output_tokens: int = 40
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeToolUse:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str = "tool_use"
    usage: FakeUsage = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.usage is None:
            self.usage = FakeUsage()


class _Messages:
    def __init__(self, turns):
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(dict(kwargs))
        return self._turns.pop(0)


class FakeClient:
    def __init__(self, turns):
        self.messages = _Messages(turns)


def _submit(**payload: Any) -> FakeToolUse:
    base = {
        "category": "duplicate",
        "rx_number": None,
        "evidence": [],
        "confidence": 0.8,
        "draft_reply": "We have looked into this for you.",
    }
    base.update(payload)
    return FakeToolUse(name="submit_diagnosis", input=base, id="tu_submit")


# --- environment ----------------------------------------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A generated dataset plus a neutral ticket set, wired into the API's env."""
    root = tmp_path_factory.mktemp("apidata")
    data = root / "data"
    evals = root / "evals"
    evals.mkdir()
    result = generate(SimConfig.small(), data)
    # A handwritten ticket file, not build_tickets(): the API only reads this file,
    # and the real selector needs a full-scale world to find 10 of each fault.
    patients = [rx.patient_id for rx in result.world.prescriptions[:3]]
    (evals / "tickets.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "ticket_id": f"T{index:03d}",
                    "text": f"Something looks off with my prescription {index}.",
                    "patient_id": patient,
                    "ground_truth": {"category": category, "rx_number": None},
                },
                sort_keys=True,
            )
            for index, (patient, category) in enumerate(
                zip(patients, ("duplicate", "dropped", "no_issue_found"), strict=True), start=1
            )
        )
        + "\n",
        encoding="utf-8",
    )
    # A minimal report per version, in the shape run_eval writes.
    for name in ("report_v1.json", "report_v2.json", "report.json"):
        (evals / name).write_text(
            json.dumps(
                {
                    "generated_at": "2026-09-29T00:00:00+00:00",
                    "stopped_early": None,
                    "runs": [
                        {
                            "label": "haiku+tools",
                            "model": HAIKU,
                            "tools_enabled": True,
                            "tickets": 48,
                            "category_accuracy": 0.979,
                            "rx_accuracy": 1.0,
                            "rx_scored": 40,
                            "cost_per_ticket_usd": 0.0124,
                            "avg_tool_calls": 4.1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    return {
        "data": data,
        "evals": evals,
        "patient": result.world.prescriptions[0].patient_id,
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch, world):
    monkeypatch.setenv("DEMO_KEY", KEY)
    monkeypatch.setenv("RXSYNC_DATA_DIR", str(world["data"]))
    monkeypatch.setenv("RXSYNC_EVALS_DIR", str(world["evals"]))
    tools.clear_cache()
    tools.set_data_dir(world["data"])
    yield
    tools.clear_cache()
    tools.set_data_dir(tools.DEFAULT_DATA_DIR)


def _get(path: str, key: str | None = KEY) -> Any:
    headers = {"x-demo-key": key} if key is not None else {}
    return route(Request("GET", path, headers))


def _post(path: str, payload: dict[str, Any], key: str | None = KEY, client=None) -> Any:
    headers = {"x-demo-key": key} if key is not None else {}
    return route(Request("POST", path, headers, json.dumps(payload)), client=client)


# --- auth -----------------------------------------------------------------


def test_a_missing_key_is_401():
    response = _get("/patients", key=None)
    assert response.status == 401
    assert "x-demo-key" in response.body["error"]


def test_a_wrong_key_is_401():
    assert _get("/patients", key="not-the-key").status == 401


def test_an_empty_key_header_is_401():
    """An empty header must never match, even against an empty secret."""
    assert _get("/patients", key="").status == 401


def test_every_route_requires_the_key():
    for method, path in (
        ("GET", "/patients"),
        ("GET", "/examples"),
        ("GET", "/report"),
        ("POST", "/investigate"),
    ):
        response = route(Request(method, path, {}, "{}"))
        assert response.status == 401, f"{method} {path} was not protected"


def test_an_unconfigured_server_secret_is_a_500_not_an_open_door(monkeypatch):
    monkeypatch.delenv("DEMO_KEY", raising=False)
    response = _get("/patients", key="anything")
    assert response.status == 500
    assert "DEMO_KEY" in response.body["error"]


def test_preflight_is_answered_without_a_key():
    """Browsers do not send custom headers on OPTIONS, so auth must come after."""
    response = route(Request("OPTIONS", "/investigate", {}))
    assert response.status == 204


# --- read routes ----------------------------------------------------------


def test_patients_returns_sorted_unique_ids():
    body = _get("/patients").body
    assert body["patients"] == sorted(set(body["patients"]))
    assert all(p.startswith("PT") for p in body["patients"])


def test_examples_come_from_the_current_ticket_set(world):
    body = _get("/examples").body
    lines = (world["evals"] / "tickets.jsonl").read_text().splitlines()
    assert len(body["examples"]) == len(lines)
    first = body["examples"][0]
    assert set(first) == {"ticket_id", "patient_id", "text", "expected_category"}


def test_every_example_patient_is_in_the_patient_list():
    patients = set(_get("/patients").body["patients"])
    assert {e["patient_id"] for e in _get("/examples").body["examples"]} <= patients


def test_report_covers_the_three_versions():
    versions = _get("/report").body["versions"]
    assert [v["version"] for v in versions] == ["v1", "v2", "v2.1"]
    for version in versions:
        assert version["note"]
        assert version["runs"]
        run = version["runs"][0]
        for field in ("label", "category_accuracy", "rx_accuracy", "cost_per_ticket_usd"):
            assert field in run


def test_a_missing_report_file_is_skipped_not_fatal(world):
    (world["evals"] / "report_v2.json").unlink()
    versions = _get("/report").body["versions"]
    assert [v["version"] for v in versions] == ["v1", "v2.1"]


def test_health_lists_the_allowed_models():
    body = _get("/health").body
    assert body["ok"] is True
    assert body["models"] == list(ALLOWED_MODELS)


def test_an_unknown_route_is_404():
    assert _get("/nope").status == 404


# --- investigate ----------------------------------------------------------


def test_investigate_returns_diagnosis_evidence_trace_cost_and_latency(world):
    client = FakeClient([FakeResponse([_submit(category="dropped", confidence=0.7)])])
    response = _post(
        "/investigate",
        {"patient_id": world["patient"], "ticket_text": "something is off"},
        client=client,
    )
    assert response.status == 200
    body = response.body
    assert body["diagnosis"]["category"] == "dropped"
    assert body["diagnosis"]["confidence"] == 0.7
    assert "evidence" in body["diagnosis"]
    assert "draft_reply" in body["diagnosis"]
    assert body["trace"]["tool_calls"]
    assert body["cost_usd"] > 0
    assert body["latency_s"] >= 0
    assert body["model"] == DEFAULT_MODEL


def test_the_trace_carries_each_tool_call_with_its_result(world):
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": world["patient"]})]),
            FakeResponse([_submit()]),
        ]
    )
    body = _post(
        "/investigate",
        {"patient_id": world["patient"], "ticket_text": "check please"},
        client=client,
    ).body
    calls = body["trace"]["tool_calls"]
    assert [c["name"] for c in calls] == ["get_patient_view", "submit_diagnosis"]
    assert calls[0]["input"] == {"patient_id": world["patient"]}
    assert calls[0]["output"], "the real tool ran and its result is in the trace"
    assert calls[0]["is_error"] is False


def test_the_model_defaults_to_haiku(world):
    client = FakeClient([FakeResponse([_submit()])])
    body = _post(
        "/investigate", {"patient_id": world["patient"], "ticket_text": "hi"}, client=client
    ).body
    assert body["model"] == HAIKU


def test_sonnet_is_accepted(world):
    client = FakeClient([FakeResponse([_submit()])])
    body = _post(
        "/investigate",
        {"patient_id": world["patient"], "ticket_text": "hi", "model": SONNET},
        client=client,
    ).body
    assert body["model"] == SONNET


@pytest.mark.parametrize("model", ["claude-opus-5", "gpt-4", "haiku", "", "   ", 5])
def test_any_other_model_is_rejected(world, model):
    # No client is passed on purpose: a rejected model must be refused before any
    # Anthropic client exists, and the conftest guard fails the test if one is built.
    payload = {"patient_id": world["patient"], "ticket_text": "hi", "model": model}
    response = _post("/investigate", payload)
    assert response.status == 400
    assert "model must be one of" in response.body["error"]


def test_a_null_model_means_the_default(world):
    client = FakeClient([FakeResponse([_submit()])])
    payload = {"patient_id": world["patient"], "ticket_text": "hi", "model": None}
    assert _post("/investigate", payload, client=client).body["model"] == HAIKU


@pytest.mark.parametrize(
    ("payload", "missing"),
    [({"ticket_text": "hi"}, "patient_id"), ({"patient_id": "PT00001"}, "ticket_text")],
)
def test_missing_fields_are_400(payload, missing):
    response = _post("/investigate", payload)
    assert response.status == 400
    assert missing in response.body["error"]


def test_a_blank_ticket_is_400(world):
    response = _post("/investigate", {"patient_id": world["patient"], "ticket_text": "   "})
    assert response.status == 400


def test_malformed_json_is_400():
    response = route(Request("POST", "/investigate", {"x-demo-key": KEY}, "{not json"))
    assert response.status == 400
    assert "valid JSON" in response.body["error"]


def test_an_unknown_patient_still_returns_a_diagnosis(world):
    """The tool reports the error; the agent decides. Not an API-level failure."""
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": "PT99999"})]),
            FakeResponse([_submit(category="no_issue_found", confidence=0.1)]),
        ]
    )
    response = _post(
        "/investigate", {"patient_id": "PT99999", "ticket_text": "help"}, client=client
    )
    assert response.status == 200
    assert response.body["trace"]["tool_calls"][0]["is_error"] is True


# --- CORS and the Lambda adapter -----------------------------------------


def test_the_lambda_handler_shapes_a_function_url_response():
    event = {
        "version": "2.0",
        "rawPath": "/patients",
        "headers": {"X-Demo-Key": KEY, "Origin": "http://localhost:5173"},
        "requestContext": {"http": {"method": "GET", "path": "/patients"}},
    }
    response = handler(event)
    assert response["statusCode"] == 200
    assert response["headers"]["content-type"] == "application/json"
    assert json.loads(response["body"])["patients"]


def test_the_lambda_handler_leaves_cors_to_the_function_url():
    # The function URL adds CORS headers itself; a second copy from the function
    # is not merged, and browsers reject a doubled Access-Control-Allow-Origin.
    event = {
        "requestContext": {"http": {"method": "GET", "path": "/health"}},
        "headers": {"x-demo-key": KEY, "origin": "http://localhost:5173"},
    }
    headers = handler(event)["headers"]
    assert not any(name.lower().startswith("access-control-") for name in headers)


# --- secrets from Secrets Manager ------------------------------------------


class FakeSecrets:
    """Stands in for a boto3 secretsmanager client."""

    def __init__(self, values: dict[str, str | None]):
        self.values = values
        self.asked: list[str] = []

    def get_secret_value(self, SecretId: str) -> dict[str, str]:  # boto3's keyword name
        self.asked.append(SecretId)
        value = self.values.get(SecretId)
        if value is None:
            # What an empty secret raises before its first put-secret-value.
            raise LookupError("ResourceNotFoundException")
        return {"SecretString": value}


@pytest.fixture
def secret_arns(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY_SECRET_ARN", "arn:anthropic")
    monkeypatch.setenv("DEMO_KEY_SECRET_ARN", "arn:demo")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_KEY", raising=False)


def test_secrets_fill_their_env_vars(secret_arns):
    import os

    fake = FakeSecrets({"arn:anthropic": "sk-test", "arn:demo": "demo-test"})
    assert api.load_secrets(fake) == []
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
    assert os.environ["DEMO_KEY"] == "demo-test"


def test_an_empty_secret_is_reported_missing_and_the_api_stays_closed(secret_arns, capsys):
    fake = FakeSecrets({"arn:anthropic": "sk-test", "arn:demo": None})
    assert api.load_secrets(fake) == ["DEMO_KEY"]
    # With no DEMO_KEY the API refuses everything rather than opening up.
    assert route(Request("GET", "/health", {"x-demo-key": ""})).status == 500
    # The log names the secret, never a value.
    assert "sk-test" not in capsys.readouterr().out


def test_a_loaded_secret_is_not_fetched_again(secret_arns):
    fake = FakeSecrets({"arn:anthropic": "sk-test", "arn:demo": None})
    api.load_secrets(fake)
    fake.values["arn:demo"] = "set-later"
    assert api.load_secrets(fake) == []
    # The second pass only asked for the secret that was still missing.
    assert fake.asked == ["arn:anthropic", "arn:demo", "arn:demo"]


def test_without_secret_arns_nothing_is_fetched(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY_SECRET_ARN", raising=False)
    monkeypatch.delenv("DEMO_KEY_SECRET_ARN", raising=False)
    fake = FakeSecrets({})
    assert api.load_secrets(fake) == []
    assert fake.asked == []


def test_the_lambda_handler_is_case_insensitive_about_headers():
    event = {
        "requestContext": {"http": {"method": "GET", "path": "/health"}},
        "headers": {"X-DEMO-KEY": KEY},
    }
    assert handler(event)["statusCode"] == 200


def test_the_lambda_handler_decodes_a_base64_body(world):
    import base64

    body = json.dumps({"patient_id": world["patient"], "ticket_text": ""})
    event = {
        "requestContext": {"http": {"method": "POST", "path": "/investigate"}},
        "headers": {"x-demo-key": KEY},
        "body": base64.b64encode(body.encode()).decode(),
        "isBase64Encoded": True,
    }
    # Decoded far enough to be validated: a blank ticket is a 400, not a 500.
    assert handler(event)["statusCode"] == 400


def test_the_lambda_handler_returns_401_without_a_key():
    event = {"requestContext": {"http": {"method": "GET", "path": "/patients"}}, "headers": {}}
    assert handler(event)["statusCode"] == 401
