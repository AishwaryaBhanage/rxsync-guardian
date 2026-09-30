"""Guards that apply to every test.

The suite must pass with no Anthropic credentials, exactly as it does in CI. On a
laptop with a real key in `.env`, a test that forgot to inject a fake client used
to make a real, billed API call and pass anyway. These fixtures make that
impossible: no key in the environment, `.env` never loaded, and constructing a
real client fails the test on the spot.
"""

from __future__ import annotations

import anthropic
import dotenv
import pytest


def _refuse_real_client(*args, **kwargs):
    # pytest.fail raises a BaseException, so a broad `except Exception` in the
    # code under test cannot swallow it.
    pytest.fail(
        "a test constructed a real Anthropic client; pass a fake client instead",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _no_real_anthropic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # default_client() imports load_dotenv at call time, so patching the module
    # attribute is enough to keep .env out.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(anthropic, "Anthropic", _refuse_real_client)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _refuse_real_client)
