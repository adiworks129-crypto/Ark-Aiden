"""
Ark evaluator — Milestone 6.

Turns Ark from "a thing that generates and mutates synthetic estates" into
"a thing that objectively scores how well an AI agent reasons about them."
See Ark_Evaluator_Design.md (Revision 2) for the full design this package
implements, and Ark_Architecture_and_Plan.md for how it fits the rest of
Ark.

Milestone 6.1 (this commit) builds only the foundation: the normalized
Issue layer (issues.py) that sits between the raw mutation ledger and any
scoring, the agent-output contract (schema.py), and the dynamic complexity
model (complexity.py). Matching (matcher.py), metrics/calibration, and
report assembly are later sub-milestones (6.2-6.5) — see the design doc's
Section 8 for the full plan. Nothing in this package has been wired to an
actual AI agent yet; this is entirely scoring infrastructure.

Boundary this package must never cross: everything here reads
ark.core.models / ark.mutation.ledger objects and the (future) rendering
manifest. It never invents a competing notion of ground truth, and never
imports ark.adapters.mulesoft directly (only ark.adapters.base's
technology-agnostic shapes) — see Ark_Evaluator_Design.md Section 7.
"""

from __future__ import annotations
