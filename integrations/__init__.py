"""
Concrete, vendor-specific `AgentClient` implementations — Milestone 7.

Deliberately OUTSIDE the `ark` package. Ark's own core (`ark/core`,
`ark/generator`, `ark/mutation`, `ark/adapters`, `ark/evaluator`,
`ark/harness`, `ark/experiment`) has zero third-party dependencies and
must stay that way -- the framework itself should never require any one
AI vendor's SDK to be installed just to generate estates, mutate them, or
grade an evaluation. The agent being evaluated is conceptually an
EXTERNAL party to Ark, and the code that actually talks to a specific
vendor's model belongs on that same, external side of the boundary, not
folded into Ark's core as if Ark endorsed or depended on one particular
provider.

Everything in this directory:
- implements `ark.harness.contract.AgentClient` (the one interface
  `ark/experiment/runner.py` depends on), so it's a drop-in replacement
  for `ark.harness.scripted_client.ScriptedAgentClient` wherever a real
  agent is wanted instead of a scripted double;
- imports its vendor SDK lazily / guarded, so simply having this
  directory on disk never forces that SDK to be installed;
- is never imported by anything under `ark/` -- the dependency only ever
  runs one way (integrations depend on ark.harness's contract; ark never
  depends on integrations), the same directional-boundary discipline
  `ark/adapters` already established for rendering targets.

Install the extra needed for a given client (e.g. `pip install -e
".[llm]"` for `anthropic_agent_client.py`) only if you actually want to
run a real evaluation against that vendor's model. The rest of Ark, and
its entire test suite, has no dependency on any of this.
"""

from __future__ import annotations
