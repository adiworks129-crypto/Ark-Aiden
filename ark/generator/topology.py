"""
Builds the abstract dependency graph for a generated estate — which
applications exist (by layer + assigned business noun) and which depend
on which — *before* any ground-truth dataclasses are constructed.
generator.py turns this plan into real Application/API/Flow/Step objects;
this module only ever deals in layers, nouns, and app "keys" (internal
indices, not final ground-truth ids).

Topology rule (topology_style="layered", the only style Milestone 3
implements): strictly feed-forward, three fixed layers.

    experience --calls--> process --calls--> system

Experience apps may only call process apps; process apps may only call
system apps; system apps call nothing further (they're leaves). This is
what rules out the "every API connects to every other API" anti-pattern
regardless of dependency_density's value — there is no cross-layer or
same-layer edge to draw in the first place, so density can only ever
control fan-out *within* one already-narrow layer transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ark.generator.config import GeneratorConfig
from ark.generator.seeds import decide, draw_nouns, sample_subset
from ark.generator.vocabulary import VOCABULARY

LAYER_EXPERIENCE = "experience"
LAYER_PROCESS = "process"
LAYER_SYSTEM = "system"


@dataclass
class AppSpec:
    key: str
    """Internal stable key (e.g. 'process-0'), used only to wire up edges
    before real ground-truth ids exist."""
    layer: str
    noun: str
    has_secondary_flow: bool = False
    shares_subflow_across_flows: bool = False


@dataclass
class TopologyPlan:
    apps: list[AppSpec] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    """(source app key, target app key): source depends on / calls target."""


def build_topology(config: GeneratorConfig, rng) -> TopologyPlan:
    # topology_style is validated in GeneratorConfig.__post_init__; this
    # assertion exists so a future second style can't be added to
    # SUPPORTED_TOPOLOGY_STYLES without also being handled here.
    if config.topology_style != "layered":
        raise NotImplementedError(f"topology_style '{config.topology_style}' is not implemented.")

    experience_nouns = draw_nouns(rng, VOCABULARY, config.num_experience_apis)
    process_nouns = draw_nouns(rng, VOCABULARY, config.num_process_apis)
    system_nouns = draw_nouns(rng, VOCABULARY, config.num_system_apis)

    apps: list[AppSpec] = []
    apps.extend(AppSpec(key=f"experience-{i}", layer=LAYER_EXPERIENCE, noun=n) for i, n in enumerate(experience_nouns))
    apps.extend(AppSpec(key=f"process-{i}", layer=LAYER_PROCESS, noun=n) for i, n in enumerate(process_nouns))
    apps.extend(AppSpec(key=f"system-{i}", layer=LAYER_SYSTEM, noun=n) for i, n in enumerate(system_nouns))

    process_keys = [a.key for a in apps if a.layer == LAYER_PROCESS]
    system_keys = [a.key for a in apps if a.layer == LAYER_SYSTEM]

    edges: list[tuple[str, str]] = []
    for app in apps:
        if app.layer == LAYER_EXPERIENCE:
            targets = sample_subset(rng, process_keys, config.dependency_density)
        elif app.layer == LAYER_PROCESS:
            targets = sample_subset(rng, system_keys, config.dependency_density)
        else:
            targets = []
        edges.extend((app.key, target) for target in targets)

    # Secondary (scheduled) flow + in-app sharing decisions. Experience
    # apps never get a secondary flow in Milestone 3 (see the Milestone 3
    # note in Ark_Architecture_and_Plan.md for why).
    for app in apps:
        if app.layer in (LAYER_PROCESS, LAYER_SYSTEM):
            app.has_secondary_flow = decide(rng, config.scheduled_job_ratio)
            if app.has_secondary_flow:
                app.shares_subflow_across_flows = decide(rng, config.shared_component_frequency)

    return TopologyPlan(apps=apps, edges=edges)
