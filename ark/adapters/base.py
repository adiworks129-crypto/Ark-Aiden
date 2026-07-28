"""
Ark's technology-agnostic adapter interface.

Every target-technology adapter (MuleSoft first; others later, per the
plan's adapter/plugin boundary in Ark_Architecture_and_Plan.md Section
1.3) implements TargetAdapter. Core Ark (ark/core/) never imports from
this module or from any adapter package — the dependency only ever runs
one way: adapters import ark.core, never the reverse.

This module intentionally knows nothing about MuleSoft, XML, DataWeave, or
any other target-specific concept. If a MuleSoft-shaped detail ever leaks
in here, that's a bug in the boundary, not a feature.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ark.core.models import GroundTruthEstate


@dataclass
class RenderedEstate:
    """The output of rendering one ground-truth estate through one adapter.

    artifacts: relative file path -> file contents, for every generated
        file (flow configs, API specs, ...). Adapters decide their own
        internal directory layout; nothing outside the adapter package
        should assume a particular structure.
    manifest: the adapter's rendering manifest — the authoritative mapping
        from artifact paths back to ground-truth entity ids, and from
        entities to the other entities they depend on. Each adapter
        defines its own manifest shape (see mulesoft/manifest.py for the
        MuleSoft adapter's), but every adapter must produce one: it's what
        lets an evaluator connect an exported file back to Ark's internal
        model, per the plan's "evaluation readiness" requirement.
    """

    artifacts: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


class TargetAdapter(ABC):
    """Abstract base for a target-technology adapter."""

    name: str

    @abstractmethod
    def render(self, estate: GroundTruthEstate) -> RenderedEstate:
        """Render a ground-truth estate into this adapter's target format."""
        raise NotImplementedError
