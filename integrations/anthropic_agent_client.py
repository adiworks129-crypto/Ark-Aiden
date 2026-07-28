"""
AnthropicAgentClient -- a real, Anthropic-SDK-backed `AgentClient`.

See this directory's `__init__.py` for why this file lives outside `ark/`
entirely: Ark's core has zero third-party dependencies, and the agent
being evaluated (including the code that calls a specific vendor's API on
its behalf) is conceptually external to that core.

The `anthropic` package is imported lazily, inside `__init__`, and only
when this class actually needs to construct its own underlying SDK client
(i.e. when the caller doesn't already supply one via `client=`). This
means:
- Importing this module never requires `anthropic` to be installed.
- Passing a pre-built or fake/mock `client=` object (matching the small
  slice of the SDK's interface this class actually uses) lets this
  wrapper's own logic -- building the request, extracting the response
  text -- be unit-tested with no real dependency and no network call at
  all. That's how `tests/test_milestone7.py` exercises this class.
- Only actually calling `AnthropicAgentClient()` with no `client=` and no
  `anthropic` installed raises a clear `ImportError` explaining the extra
  to install, at the point of use, not at import time.
"""

from __future__ import annotations

from typing import Any

from ark.harness.contract import AgentClient

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 8192


class AnthropicAgentClient(AgentClient):
    """`AgentClient` backed by a real call to Anthropic's Messages API.

    Requires the optional `anthropic` package (`pip install -e ".[llm]"`)
    unless a pre-built `client` is supplied directly.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
        client: Any | None = None,
    ):
        """
        model / max_tokens: passed straight through to every
            `messages.create()` call.
        api_key: passed straight through to the SDK's `Anthropic(...)`
            constructor if `client` isn't supplied. If omitted, the SDK's
            own default behavior applies (reads `ANTHROPIC_API_KEY` from
            the environment).
        client: an already-constructed object exposing
            `.messages.create(...)` the same way `anthropic.Anthropic`
            does -- supply this in tests to avoid both the real SDK
            dependency and any network call.
        """
        self._model = model
        self._max_tokens = max_tokens
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
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "AnthropicAgentClient needs the 'anthropic' package to construct its "
                "own client. Install it with `pip install -e \".[llm]\"` (or `pip "
                "install anthropic`), or construct AnthropicAgentClient(client=...) "
                "with your own pre-built client object instead."
            ) from exc
        return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def generate(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # The SDK returns a list of content blocks; a plain single-turn
        # text prompt with no tools produces exactly one text block. Join
        # defensively rather than assuming index [0] in case a future SDK
        # version or model response ever splits text across blocks.
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
