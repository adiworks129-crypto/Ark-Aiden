"""
Ark's structural artifact validation.

Where `ark/schemas/` holds connector schemas as inert data, `ark/validation/`
holds the pure functions that check rendered artifact text against them
(`mulesoft_http_connector.py`), plus the pipeline wiring that runs them
automatically as part of a trajectory (`pipeline.py`, added in the session
that followed the original build-in-isolation one). Zero third-party
dependencies (stdlib `xml.etree` and `json` only), consistent with Ark's
core.
"""

from __future__ import annotations
