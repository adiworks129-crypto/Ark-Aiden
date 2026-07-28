"""
GeminiAgentClient -- a real, Google Gemini-SDK-backed `AgentClient`.

See this directory's `__init__.py` for why this file lives outside `ark/`
entirely: Ark's core has zero third-party dependencies, and the agent
being evaluated (including the code that calls a specific vendor's API on
its behalf) is conceptually external to that core.

The `google.genai` package is imported lazily, inside `__init__`, and only
when this class actually needs to construct its own underlying SDK client
(i.e. when the caller doesn't already supply one via `client=`). This
means:
- Importing this module never requires `google-genai` to be installed.
- Passing a pre-built or fake/mock `client=` object (matching the small
  slice of the SDK's interface this class actually uses) lets this
  wrapper's own logic -- building the request, extracting the response
  text -- be unit-tested with no real dependency and no network call at
  all, the same pattern `integrations/anthropic_agent_client.py` and
  `tests/test_milestone7.py`/`tests/test_milestone8.py` already use.
- Only actually calling `GeminiAgentClient()` with no `client=` and no
  `google-genai` installed raises a clear `ImportError` explaining the
  extra to install, at the point of use, not at import time.
- The API key is read from the `GEMINI_API_KEY` environment variable (or
  passed explicitly via `api_key=`) -- never hardcoded in this file or
  anywhere else in the repo.
"""

from __future__ import annotations

import os
from typing import Any

from ark.harness.contract import AgentClient

DEFAULT_MODEL = "gemini-3.1-flash-lite"


class GeminiAgentClient(AgentClient):
    """`AgentClient` backed by a real call to Google's Gemini API.

    Requires the optional `google-genai` package (`pip install -e ".[llm]"`)
    unless a pre-built `client` is supplied directly.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        client: Any | None = None,
    ):
        """
        model: passed straight through to every `generate_content()` call.
        api_key: passed straight through to the SDK's `genai.Client(...)`
            constructor if `client` isn't supplied. If omitted, reads the
            `GEMINI_API_KEY` environment variable directly (the SDK's own
            default env var, `GOOGLE_API_KEY`, is not used here so this
            class has one explicit, documented name).
        client: an already-constructed object exposing
            `.models.generate_content(model=..., contents=...)` the same
            way `google.genai.Client` does -- supply this in tests to
            avoid both the real SDK dependency and any network call.
        """
        self._model = model
        self._client = client if client is not None else self._build_default_client(api_key)

    @property
    def model(self) -> str:
        """The concrete model string this client actually calls -- read-only,
        purely informational (e.g. for a UI to display "what really ran"
        rather than a hardcoded label). Never affects `generate()`."""
        return self._model

    @staticmethod
    def _build_default_client(api_key: str | None) -> Any:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "GeminiAgentClient needs the 'google-genai' package to construct its "
                "own client. Install it with `pip install -e \".[llm]\"` (or `pip "
                "install google-genai`), or construct GeminiAgentClient(client=...) "
                "with your own pre-built client object instead."
            ) from exc
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not resolved_key:
            raise ValueError(
                "GeminiAgentClient has no API key. Set the GEMINI_API_KEY environment "
                "variable, or pass api_key=... explicitly."
            )
        return genai.Client(api_key=resolved_key)

    def generate(self, prompt: str) -> str:
        response = self._client.models.generate_content(model=self._model, contents=prompt)
        # The SDK exposes a convenience `.text` property that already
        # concatenates every text part of the first candidate -- the
        # direct equivalent of joining text blocks in the Anthropic
        # integration, for a plain single-turn text prompt with no tools.
        return response.text or ""
