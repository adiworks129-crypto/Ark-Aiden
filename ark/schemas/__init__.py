"""
Ark's connector schemas — data only.

Each subpackage under `ark/schemas/` holds JSON files describing a real,
documented technology-connector structure (e.g. `mulesoft/http_connector.json`
for the MuleSoft HTTP Connector), each field cited to a real vendor
documentation page. These files contain no logic of their own — they are
read by `ark/validation/` at validation time, never hardcoded into
`ark.generator`, `ark.mutation`, or `ark.adapters` — so a schema stays
inspectable and swappable independently of the code that eventually
consumes it.
"""

from __future__ import annotations
