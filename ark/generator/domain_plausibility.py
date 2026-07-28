"""
Domain plausibility mapping — Feature 2 ("organized randomness").

Loads ark/generator/domain_plausibility.json: which connector/component
types are genuinely well-known, real-world patterns for which of Ark's
two supported domains (finance, retail). This is inert data, mirroring
how ark/schemas/mulesoft/http_connector.json is inert data the HTTP
connector validator reads rather than hardcoded logic — see that
schema/validator's own discipline (every entry cites a real source; if
unsure, leave it out). This module performs no mutation, estate, or
ledger logic of its own — it is read by
ark.mutation.operators.DomainComponentInjectionOperator, which is where
the actual injection happens.

Every entry in the JSON file is a genuinely well-known, defensible,
real-world enterprise-software association — not invented for this task.
See the domain-injection summary doc (written alongside this feature) for
which pairings were considered and rejected for insufficient confidence
(e.g. anything used broadly across both domains, which would make a poor
"this doesn't belong here" signal).

Pure, side-effect-free functions only, matching
ark.validation.mulesoft_http_connector's own "pure functions, no I/O
beyond a single file read" discipline.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DOMAIN_PLAUSIBILITY_PATH = Path(__file__).resolve().parent / "domain_plausibility.json"

# Deliberately duplicated (not imported) in ark/core/validate.py's
# `_VALID_DOMAINS` and ark/generator/config.py's `SUPPORTED_DOMAINS` — see
# either constant's own docstring for why (avoiding an ark.core ->
# ark.generator dependency inversion). A dedicated test cross-checks all
# three stay in agreement.
SUPPORTED_DOMAINS = ("finance", "retail")


class DomainPlausibilityError(ValueError):
    """Raised when the plausibility mapping file itself is malformed —
    never for a merely-unknown domain name (see plausible_components_for,
    which raises a plain KeyError for that instead, matching
    dict-lookup conventions used elsewhere in Ark, e.g.
    ark.mutation.registry.OPERATOR_REGISTRY)."""


def load_domain_plausibility(path: Path | str = DEFAULT_DOMAIN_PLAUSIBILITY_PATH) -> dict:
    """Read the domain plausibility mapping JSON. A plain `json.load` —
    the mapping is data, not code, and this function does no
    interpretation of it beyond parsing (same discipline as
    ark.validation.mulesoft_http_connector.load_schema)."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    domains = data.get("domains")
    if not isinstance(domains, dict) or not domains:
        raise DomainPlausibilityError(f"{path}: missing or empty top-level 'domains' object.")
    for domain_name, domain_entry in domains.items():
        components = domain_entry.get("plausible_components")
        if not isinstance(components, list) or not components:
            raise DomainPlausibilityError(
                f"{path}: domain '{domain_name}' has no non-empty 'plausible_components' list."
            )
        for component in components:
            missing = [k for k in ("key", "display_name", "justification") if k not in component]
            if missing:
                raise DomainPlausibilityError(
                    f"{path}: a component under domain '{domain_name}' is missing field(s): {missing}."
                )

    return data


def plausible_components_for(domain: str, mapping: dict | None = None) -> list[dict]:
    """Return the list of {"key", "display_name", "justification"} dicts
    genuinely plausible for `domain`. Loads the default mapping file if
    `mapping` isn't given (pass an already-loaded one to avoid re-reading
    the file on every call, e.g. once per mutation trajectory).

    Raises a plain KeyError for a domain name not present in the mapping —
    deliberately not a domain *validity* check (that's
    ark.core.validate._VALID_DOMAINS' job, at the estate/config level,
    before any of this is ever reached); this function only answers "what
    does the mapping say for this domain," nothing more.
    """
    mapping = mapping if mapping is not None else load_domain_plausibility()
    return mapping["domains"][domain]["plausible_components"]
