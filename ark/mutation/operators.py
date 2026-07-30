"""
Ark's Milestone 4 mutation operators.

Every operator here follows the same invariant, deliberately: the estate
it returns must still pass ark.core.validate.validate_estate_object() (no
dangling references, no duplicate ids). "Structural complexity" and
"legacy conflict" are simulated through duplication, repointed-but-valid
references, and content drift — never through a broken estate, since a
referentially-invalid estate couldn't even be exported through the
Milestone 2 adapter for an agent to evaluate. See
Ark_Architecture_and_Plan.md's Milestone 4 section for the full rationale.

Each operator's docstring states what it changes, what its severity
parameter controls, and what it deliberately does NOT do (to keep
operators independent rather than overlapping).
"""

from __future__ import annotations

import dataclasses
import re

from ark.core.models import (
    API,
    ApiCallStep,
    ConnectorStep,
    Flow,
    FlowRefStep,
    GroundTruthEstate,
    LoggerStep,
    TransformStep,
)
from ark.generator.domain_plausibility import SUPPORTED_DOMAINS, plausible_components_for
from ark.mutation.base import (
    MutationOperator,
    MutationRecordDraft,
    clone_estate,
    find_application,
    find_flow,
    find_step,
)


def _entity_dict(entity) -> dict:
    return dataclasses.asdict(entity)


# ---------------------------------------------------------------------------
# 1. Naming drift — renames Application/API/Flow display names, never ids.
# ---------------------------------------------------------------------------

_DRIFT_CAUSES = [
    "a team reorganization",
    "an unrelated rebranding effort",
    "inconsistent onboarding of new engineers",
    "a rushed migration that skipped a naming review",
]


def _split_name(name: str) -> tuple[list[str], str]:
    """Entity names in Ark aren't all the same convention — Flow/Application
    names are kebab-case ('get-customer-main-flow') but API names are
    'Title Case With Spaces' ('Customer System API'). Splitting only on
    '-' would silently no-op on API names. This detects whichever
    delimiter is actually present (or falls back to treating the whole
    string as one token) so every style below works on any entity kind."""
    if "-" in name:
        return name.split("-"), "-"
    if " " in name:
        return name.split(" "), " "
    return [name], ""


def _style_kebab_to_camel(name: str) -> str:
    parts, _ = _split_name(name)
    if len(parts) < 2:
        return f"{name}Camel"
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _style_add_legacy_suffix(name: str, rng) -> str:
    suffix = rng.choice(["_v2_final", "_old", "_deprecated", "-copy2"])
    return f"{name}{suffix}"  # a plain append always changes something, regardless of format


def _style_case_shift(name: str) -> str:
    parts, delim = _split_name(name)
    parts[0] = parts[0].upper()
    return delim.join(parts) if delim else parts[0]


def _style_abbreviate(name: str) -> str:
    parts, delim = _split_name(name)
    if len(parts) < 2:
        return name[: max(3, len(name) // 2)]
    parts[-1] = parts[-1][:4]
    return delim.join(parts)


_STYLES = [_style_kebab_to_camel, _style_add_legacy_suffix, _style_case_shift, _style_abbreviate]


class NamingDriftOperator(MutationOperator):
    """Renames one Application, API, or Flow's display `.name` field using
    an inconsistent convention. Never touches `.id` fields — identity is
    always preserved, so this can never break a reference. Severity
    controls how many drift styles compound (1 style at low severity, up
    to 3 at high severity), not which single thing changes."""

    transformation_type = "naming_drift"
    description = (
        "A component's display name uses a naming convention that's inconsistent with similar "
        "components elsewhere in the project (mixed casing, an abbreviation, or an unexpected "
        "'legacy'-style suffix), even though it otherwise behaves normally."
    )

    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        candidates: list[dict] = []
        for app in estate.applications:
            candidates.append({"entity_type": "Application", "entity_id": app.id, "app_id": app.id})
            for api in app.apis:
                candidates.append({"entity_type": "API", "entity_id": api.id, "app_id": app.id})
            for flow in app.flows:
                candidates.append({"entity_type": "Flow", "entity_id": flow.id, "app_id": app.id})
        return sorted(candidates, key=lambda c: c["entity_id"])

    def apply(self, estate, target, severity, rng, mutation_ordinal):
        new_estate = clone_estate(estate)
        app = find_application(new_estate, target["app_id"])

        if target["entity_type"] == "Application":
            entity = app
        elif target["entity_type"] == "API":
            entity = next(a for a in app.apis if a.id == target["entity_id"])
        else:
            entity = find_flow(app, target["entity_id"])

        old_name = entity.name
        num_styles = 1 + round(severity * 2)  # severity 0.0 -> 1 style, 1.0 -> 3 styles
        new_name = old_name
        styles_used = []
        for _ in range(max(1, min(3, num_styles))):
            style = rng.choice(_STYLES)
            new_name = style(new_name, rng) if style is _style_add_legacy_suffix else style(new_name)
            styles_used.append(style.__name__)
        if new_name == old_name:
            # Safety net: every style function is designed to always change
            # something, but if a future style is added that can no-op on
            # some name shape, guarantee a genuine change rather than
            # silently recording a mutation that didn't mutate anything.
            new_name = _style_add_legacy_suffix(new_name, rng)
            styles_used.append("_style_add_legacy_suffix (forced, no-op guard)")
        entity.name = new_name

        cause = rng.choice(_DRIFT_CAUSES)
        draft = MutationRecordDraft(
            transformation_type=self.transformation_type,
            affected_entity_ids=[target["entity_id"]],
            original_state={target["entity_id"]: {"name": old_name}},
            transformed_state={target["entity_id"]: {"name": new_name}},
            rationale=(
                f"Simulates naming convention drift on {target['entity_type']} "
                f"'{target['entity_id']}' (styles applied: {styles_used}), as if caused by {cause}."
            ),
        )
        return new_estate, draft


# ---------------------------------------------------------------------------
# 2. Documentation decay — degrades TransformStep/ApiCallStep .description.
# ---------------------------------------------------------------------------

class DocumentationDecayOperator(MutationOperator):
    """Degrades the human-readable `.description` field of a TransformStep
    or ApiCallStep. Never removes the field (that would fail structural
    validation, which requires it) — only its content quality. Severity
    controls how far it decays: truncate (low) -> generic placeholder
    (medium) -> empty string (high)."""

    transformation_type = "documentation_decay"
    description = (
        "A step's description is missing, truncated, or replaced with vague, generic placeholder "
        "text that doesn't actually explain what the step does."
    )

    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        candidates: list[dict] = []
        for app in estate.applications:
            for flow in app.flows:
                for step in flow.steps:
                    if isinstance(step, (TransformStep, ApiCallStep)) and step.description:
                        candidates.append(
                            {"entity_id": step.id, "app_id": app.id, "flow_id": flow.id}
                        )
        return sorted(candidates, key=lambda c: c["entity_id"])

    def apply(self, estate, target, severity, rng, mutation_ordinal):
        new_estate = clone_estate(estate)
        app = find_application(new_estate, target["app_id"])
        flow = find_flow(app, target["flow_id"])
        step = find_step(flow, target["entity_id"])

        old_description = step.description
        if severity < 0.34:
            words = old_description.split()
            keep = max(1, round(len(words) * 0.4))
            new_description = " ".join(words[:keep])
            decay_style = "truncated"
        elif severity < 0.67:
            new_description = "TODO: document this step."
            decay_style = "generic placeholder"
        else:
            new_description = ""
            decay_style = "removed entirely"

        if new_description == old_description:
            # No-op guard: this step may already be sitting at exactly this
            # decayed shape from an earlier mutation earlier in the same
            # trajectory (e.g. already truncated to one word, or already
            # the generic placeholder). Escalate to the next stage down
            # rather than silently recording a "mutation" that didn't
            # change anything.
            if decay_style == "truncated":
                new_description = "TODO: document this step."
                decay_style = "generic placeholder (escalated: truncation was already a no-op)"
            elif new_description != "":
                new_description = ""
                decay_style = "removed entirely (escalated: placeholder was already a no-op)"
            else:
                new_description = "N/A"
                decay_style = "replaced with 'N/A' (escalated fallback)"
        step.description = new_description

        draft = MutationRecordDraft(
            transformation_type=self.transformation_type,
            affected_entity_ids=[target["entity_id"]],
            original_state={target["entity_id"]: {"description": old_description}},
            transformed_state={target["entity_id"]: {"description": new_description}},
            rationale=(
                f"Simulates incomplete enterprise documentation: description on "
                f"'{target['entity_id']}' was {decay_style}."
            ),
        )
        return new_estate, draft


# ---------------------------------------------------------------------------
# 3. Duplicate processing — clones a shared sub-flow, rewires one caller.
# ---------------------------------------------------------------------------

class DuplicateProcessingOperator(MutationOperator):
    """Finds an existing FlowRefStep pointing at a sub-flow, creates a
    near-duplicate of that sub-flow, and rewires that ONE caller to use
    the duplicate instead — leaving any other callers still pointing at
    the original. This is the "redundant processing path" pattern: two
    flows now do almost the same thing, and only some callers were
    switched. Severity controls whether the duplicate is an exact clone
    (low) or has already started to diverge in content (high) — see
    _drift_duplicate_content below."""

    transformation_type = "duplicate_processing"
    description = (
        "Two flows do nearly the same thing, and only some callers were switched to use the newer "
        "one -- a redundant processing path that's only partially been adopted."
    )

    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        candidates: list[dict] = []
        for app in estate.applications:
            flow_types = {f.id: f.flow_type for f in app.flows}
            for flow in app.flows:
                for step in flow.steps:
                    if isinstance(step, FlowRefStep) and flow_types.get(step.target_flow_id) == "sub_flow":
                        candidates.append(
                            {
                                "app_id": app.id,
                                "flow_id": flow.id,
                                "step_id": step.id,
                                "target_flow_id": step.target_flow_id,
                            }
                        )
        return sorted(candidates, key=lambda c: c["step_id"])

    def apply(self, estate, target, severity, rng, mutation_ordinal):
        new_estate = clone_estate(estate)
        app = find_application(new_estate, target["app_id"])
        caller_flow = find_flow(app, target["flow_id"])
        step = find_step(caller_flow, target["step_id"])
        original_flow = find_flow(app, target["target_flow_id"])

        new_flow_id = f"{original_flow.id}-dup{mutation_ordinal}"
        new_flow_name = f"{original_flow.name}-copy"
        # Cloned steps must get NEW unique ids — dataclasses.replace() with
        # no overrides would keep the original step ids, which would
        # collide with the source flow's steps (every id in an estate must
        # be globally unique). Any target_flow_id/target_api_id a cloned
        # step points at is left as-is: reusing the same downstream
        # target is valid and realistic (the duplicate doesn't need its
        # own frozen copy of everything it calls too).
        new_steps = [
            dataclasses.replace(s, id=f"{s.id}-dup{mutation_ordinal}-{idx}")
            for idx, s in enumerate(original_flow.steps)
        ]
        if severity >= 0.5:
            new_steps = [_drift_duplicate_content(s) for s in new_steps]
        new_flow = Flow(
            id=new_flow_id, name=new_flow_name, flow_type="sub_flow", trigger=None, steps=new_steps
        )
        app.flows.append(new_flow)

        old_target = step.target_flow_id
        step.target_flow_id = new_flow_id

        draft = MutationRecordDraft(
            transformation_type=self.transformation_type,
            affected_entity_ids=[new_flow_id, target["step_id"]],
            original_state={new_flow_id: None, target["step_id"]: {"target_flow_id": old_target}},
            transformed_state={
                new_flow_id: _entity_dict(new_flow),
                target["step_id"]: {"target_flow_id": new_flow_id},
            },
            rationale=(
                f"Simulates redundant processing paths: '{original_flow.id}' was duplicated as "
                f"'{new_flow_id}', and caller step '{target['step_id']}' was switched to use the "
                f"duplicate while any other callers keep using the original."
            ),
        )
        return new_estate, draft


def _drift_duplicate_content(step):
    """A duplicated flow that's 'already started to diverge' — used only
    at higher duplicate_processing severity. Nudges one visible field so
    the duplicate isn't byte-identical to its source."""
    if isinstance(step, LoggerStep):
        return dataclasses.replace(step, message=f"{step.message} (updated)")
    if isinstance(step, TransformStep):
        return dataclasses.replace(step, description=f"{step.description} (updated copy)")
    return step


# ---------------------------------------------------------------------------
# 4. Legacy version introduction — adds an older sibling API + frozen flow.
# ---------------------------------------------------------------------------

class LegacyVersionOperator(MutationOperator):
    """Adds a second, "legacy" API to the same Application as an existing
    one, with its own frozen copy of the entry flow's implementation (an
    earlier revision — at higher severity, missing the most recently
    added step, simulating a version that never got a later feature).
    This never removes or changes the original API/flow — it only adds a
    sibling, so the estate is always still fully valid."""

    transformation_type = "legacy_version_introduction"
    description = (
        "An older, still-present sibling version of an API or flow exists alongside the current "
        "one, implementing an earlier revision of the same functionality (e.g. missing a feature "
        "the newer version already has)."
    )

    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        candidates = []
        for app in estate.applications:
            for api in app.apis:
                candidates.append({"app_id": app.id, "api_id": api.id})
        return sorted(candidates, key=lambda c: c["api_id"])

    def apply(self, estate, target, severity, rng, mutation_ordinal):
        new_estate = clone_estate(estate)
        app = find_application(new_estate, target["app_id"])
        api = next(a for a in app.apis if a.id == target["api_id"])
        entry_flow = find_flow(app, api.entry_flow_id)

        # Same id-collision reasoning as DuplicateProcessingOperator above:
        # cloned steps need new unique ids.
        legacy_steps = [
            dataclasses.replace(s, id=f"{s.id}-legacy{mutation_ordinal}-{idx}")
            for idx, s in enumerate(entry_flow.steps)
        ]
        if severity >= 0.5 and len(legacy_steps) > 1:
            legacy_steps = legacy_steps[:-1]  # legacy version predates the most recent step

        legacy_flow_id = f"{entry_flow.id}-legacy{mutation_ordinal}"
        legacy_flow = Flow(
            id=legacy_flow_id,
            name=f"{entry_flow.name}-legacy",
            flow_type="flow",
            trigger=dataclasses.replace(entry_flow.trigger) if entry_flow.trigger else None,
            steps=legacy_steps,
        )
        app.flows.append(legacy_flow)

        legacy_api_id = f"{api.id}-legacy{mutation_ordinal}"
        legacy_api = API(
            id=legacy_api_id,
            name=f"{api.name} (Legacy)",
            version=f"{api.version}-legacy",
            entry_flow_id=legacy_flow_id,
        )
        app.apis.append(legacy_api)

        draft = MutationRecordDraft(
            transformation_type=self.transformation_type,
            affected_entity_ids=[legacy_api_id, legacy_flow_id],
            original_state={legacy_api_id: None, legacy_flow_id: None},
            transformed_state={
                legacy_api_id: _entity_dict(legacy_api),
                legacy_flow_id: _entity_dict(legacy_flow),
            },
            rationale=(
                f"Simulates a legacy API version left running after '{api.id}' was superseded: "
                f"'{legacy_api_id}' still points at a frozen implementation ('{legacy_flow_id}'), "
                f"per real MuleSoft estates where deprecated versions stay live in API Manager."
            ),
        )
        return new_estate, draft


# ---------------------------------------------------------------------------
# 5. Schema inconsistency — drifts a field-naming convention in DataWeave.
# ---------------------------------------------------------------------------

_FIELD_STYLES = ["camelCase", "snake_case", "PascalCase", "abbreviated"]


def _restyle_field(field_name: str, style: str) -> str:
    if style == "snake_case":
        return re.sub(r"(?<!^)(?=[A-Z])", "_", field_name).lower()
    if style == "PascalCase":
        return field_name[:1].upper() + field_name[1:]
    if style == "abbreviated":
        return field_name[:3]
    return field_name  # camelCase is the generator's default; treated as a no-op target


class SchemaInconsistencyOperator(MutationOperator):
    """Finds a TransformStep whose DataWeave script defines a "<word>Id"
    style field, and rewrites that field name to a different naming
    convention (snake_case, PascalCase, or an abbreviation). This
    simulates a real, common integration bug class: two components meant
    to speak the same contract have silently diverged in field-naming
    convention. Deliberately scoped to field-name text within existing
    DataWeave content — Ark has no first-class "shared schema" entity yet
    (see the Milestone 1 note on deferred concepts), so this operator
    works at the level ground truth actually models today."""

    transformation_type = "schema_inconsistency"
    description = (
        "A field name used in one component's data mapping doesn't match the naming convention "
        "used for the same concept elsewhere, as if two components meant to share a contract have "
        "silently diverged."
    )

    _FIELD_PATTERN = re.compile(r"(\w+)Id:")

    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        candidates = []
        for app in estate.applications:
            for flow in app.flows:
                for step in flow.steps:
                    if isinstance(step, TransformStep) and self._FIELD_PATTERN.search(step.dataweave):
                        candidates.append({"app_id": app.id, "flow_id": flow.id, "step_id": step.id})
        return sorted(candidates, key=lambda c: c["step_id"])

    def apply(self, estate, target, severity, rng, mutation_ordinal):
        new_estate = clone_estate(estate)
        app = find_application(new_estate, target["app_id"])
        flow = find_flow(app, target["flow_id"])
        step = find_step(flow, target["step_id"])

        match = self._FIELD_PATTERN.search(step.dataweave)
        old_field = match.group(0)[:-1]  # strip trailing ':'
        style = rng.choice(_FIELD_STYLES)
        new_field = _restyle_field(old_field, style)

        if new_field == old_field:
            # No-op guard: the chosen style can coincide with the field's
            # current form (e.g. re-picking 'snake_case' on a field this
            # same operator already snake_cased earlier in the
            # trajectory). Deterministically try the remaining styles
            # (no extra rng draws, so this stays reproducible) until one
            # actually changes it; if literally none do, force a visible
            # change rather than record a no-op.
            for fallback_style in _FIELD_STYLES:
                candidate = _restyle_field(old_field, fallback_style)
                if candidate != old_field:
                    new_field = candidate
                    style = fallback_style
                    break
            else:
                new_field = f"{old_field}Alt"
                style = "forced-alt-suffix"

        old_dataweave = step.dataweave
        new_dataweave = step.dataweave.replace(f"{old_field}:", f"{new_field}:", 1)
        step.dataweave = new_dataweave

        draft = MutationRecordDraft(
            transformation_type=self.transformation_type,
            affected_entity_ids=[target["step_id"]],
            original_state={target["step_id"]: {"dataweave": old_dataweave}},
            transformed_state={target["step_id"]: {"dataweave": new_dataweave}},
            rationale=(
                f"Simulates schema/contract inconsistency: field '{old_field}' in "
                f"'{target['step_id']}' was renamed to '{new_field}' ({style} convention), "
                f"diverging from what related components expect."
            ),
        )
        return new_estate, draft


# ---------------------------------------------------------------------------
# 6. Dependency change — repoints a step at a different, still-valid target.
# ---------------------------------------------------------------------------

class DependencyChangeOperator(MutationOperator):
    """Repoints an existing ApiCallStep or FlowRefStep at a DIFFERENT but
    still-valid target — an ApiCallStep may be repointed at any other API
    in the estate (excluding APIs owned by its own Application, since a
    real network call target is normally a different app); a FlowRefStep
    may be repointed at any other flow in the SAME Application (the
    intra-app constraint is preserved, never violated). Simulates "the
    team switched which downstream component this step calls" — a
    realistic dependency evolution, not a broken reference."""

    transformation_type = "dependency_change"
    description = (
        "A step calls a different downstream API or flow than the rest of the project's pattern "
        "would suggest -- a plausible, but perhaps unintended, dependency switch."
    )

    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        all_api_ids_by_app = {app.id: {a.id for a in app.apis} for app in estate.applications}
        all_api_ids = sorted({aid for ids in all_api_ids_by_app.values() for aid in ids})

        candidates: list[dict] = []
        for app in estate.applications:
            flow_ids_in_app = sorted(f.id for f in app.flows)
            for flow in app.flows:
                for step in flow.steps:
                    if isinstance(step, ApiCallStep):
                        alternatives = [
                            aid for aid in all_api_ids
                            if aid not in all_api_ids_by_app[app.id] and aid != step.target_api_id
                        ]
                        if alternatives:
                            candidates.append(
                                {
                                    "kind": "api-call",
                                    "app_id": app.id,
                                    "flow_id": flow.id,
                                    "step_id": step.id,
                                    "alternatives": alternatives,
                                }
                            )
                    elif isinstance(step, FlowRefStep):
                        alternatives = [
                            fid for fid in flow_ids_in_app
                            if fid != step.target_flow_id and fid != flow.id
                        ]
                        if alternatives:
                            candidates.append(
                                {
                                    "kind": "flow-ref",
                                    "app_id": app.id,
                                    "flow_id": flow.id,
                                    "step_id": step.id,
                                    "alternatives": alternatives,
                                }
                            )
        return sorted(candidates, key=lambda c: c["step_id"])

    def apply(self, estate, target, severity, rng, mutation_ordinal):
        new_estate = clone_estate(estate)
        app = find_application(new_estate, target["app_id"])
        flow = find_flow(app, target["flow_id"])
        step = find_step(flow, target["step_id"])

        new_target = rng.choice(target["alternatives"])

        if target["kind"] == "api-call":
            old_target = step.target_api_id
            step.target_api_id = new_target
            field_name = "target_api_id"
        else:
            old_target = step.target_flow_id
            step.target_flow_id = new_target
            field_name = "target_flow_id"

        draft = MutationRecordDraft(
            transformation_type=self.transformation_type,
            affected_entity_ids=[target["step_id"]],
            original_state={target["step_id"]: {field_name: old_target}},
            transformed_state={target["step_id"]: {field_name: new_target}},
            rationale=(
                f"Simulates a dependency change: '{target['step_id']}' now calls "
                f"'{new_target}' instead of '{old_target}', as if the team switched "
                f"downstream providers while keeping the estate structurally valid."
            ),
        )
        return new_estate, draft


# ---------------------------------------------------------------------------
# 7. Domain-conditioned component injection ("organized randomness") — adds
#    one component realistic on its own terms but implausible for the
#    estate's assigned domain. Feature 2 — operator #7, added alongside the
#    original six, never blended into them (see registry.py/profiles.py).
# ---------------------------------------------------------------------------

_LOW_SEVERITY_MAX = 0.34
_MEDIUM_SEVERITY_MAX = 0.67
"""Same three-band severity discipline as DocumentationDecayOperator above
(and report.py's descriptive severity buckets) -- not a new convention."""


def _domain_injection_description(display_name: str, own_domain: str, severity: float) -> str:
    """How much the injected step's own description signals its own
    oddity -- severity meaningfully changes the OBSERVABLE content (not
    just a cosmetic label), same discipline every other operator's
    severity parameter follows. Low severity reads as a completely
    ordinary integration note; high severity all but states the
    domain mismatch outright."""
    if severity < _LOW_SEVERITY_MAX:
        return f"Integrates with {display_name} to support downstream processing."
    if severity < _MEDIUM_SEVERITY_MAX:
        return (
            f"Recently added integration with {display_name}, introduced during a "
            f"vendor evaluation last quarter."
        )
    return (
        f"Integrates with {display_name} -- an unusual choice for a {own_domain} "
        f"platform, apparently carried over from a prior vendor relationship or "
        f"acquisition rather than a deliberate fit for this estate."
    )


class DomainComponentInjectionOperator(MutationOperator):
    """Injects one new ConnectorStep into an existing Flow, naming a
    component that is realistic and well-documented ON ITS OWN TERMS (see
    ark/generator/domain_plausibility.json) but drawn from the domain
    *other than* the estate's own assigned `GroundTruthEstate.domain` —
    "organized randomness," not arbitrary noise: every injected component
    is a genuine, citable enterprise-software pattern, just one that
    belongs to a different business domain than this estate.

    Deliberately distinct from every other operator here: it is the only
    one whose precondition depends on estate-level metadata
    (`estate.domain`) rather than purely structural properties of the
    ground truth, and the only one whose "issue" is a *category-level*
    mismatch (this component doesn't belong in THIS KIND of estate at
    all) rather than a drift/duplication/staleness within an otherwise
    normal component. Never folds into or overlaps with any of the six
    existing operators (see registry.py/profiles.py for why this is kept
    strictly opt-in, not part of the Level 0-3 progression).

    Severity controls how strongly the injected step's own description
    signals the mismatch (see _domain_injection_description) — it never
    changes which domain the component is drawn from (always "the other
    one" of exactly two supported domains) or whether the injection
    happens at all.
    """

    transformation_type = "domain_implausible_component"
    description = (
        "A component that is realistic and well-formed on its own, but doesn't fit the estate's "
        "business domain (e.g. a retail point-of-sale connector appearing in a finance system)."
    )

    def find_candidates(self, estate: GroundTruthEstate) -> list[dict]:
        if estate.domain not in SUPPORTED_DOMAINS:
            # No domain assigned (None) -- or, defensively, some future
            # value SUPPORTED_DOMAINS doesn't yet cover -- means there is
            # no "home" domain to be implausible relative to. Returning no
            # candidates (never raising) matches every other operator's
            # "preconditions not met anywhere -> empty list" contract, so
            # the engine simply treats this operator as ineligible for
            # such an estate (see ark/mutation/base.py's
            # MutationOperator.find_candidates docstring).
            return []

        candidates: list[dict] = []
        for app in estate.applications:
            for flow in app.flows:
                candidates.append({"app_id": app.id, "flow_id": flow.id})
        return sorted(candidates, key=lambda c: c["flow_id"])

    def apply(self, estate, target, severity, rng, mutation_ordinal):
        new_estate = clone_estate(estate)
        app = find_application(new_estate, target["app_id"])
        flow = find_flow(app, target["flow_id"])

        own_domain = new_estate.domain
        # Exactly two supported domains today (see SUPPORTED_DOMAINS) --
        # "the other one" is unambiguous. A future third domain would need
        # an explicit choice of which foreign domain to draw from here,
        # not just "the other one"; flagged rather than silently assumed
        # to generalize (matches this operator's own class docstring).
        foreign_domain = next(d for d in SUPPORTED_DOMAINS if d != own_domain)

        components = plausible_components_for(foreign_domain)
        component = rng.choice(components)

        new_step_id = f"{flow.id}-domain-inject{mutation_ordinal}"
        description = _domain_injection_description(component["display_name"], own_domain, severity)
        new_step = ConnectorStep(
            id=new_step_id,
            name=f"Integrate with {component['display_name']}",
            description=description,
            connector_type=component["key"],
        )
        flow.steps.append(new_step)

        draft = MutationRecordDraft(
            transformation_type=self.transformation_type,
            affected_entity_ids=[new_step_id],
            original_state={new_step_id: None},
            transformed_state={new_step_id: _entity_dict(new_step)},
            rationale=(
                f"Simulates organized randomness (Feature 2): injected a "
                f"'{component['display_name']}' component ('{new_step_id}') into Flow "
                f"'{flow.id}' — a genuine, well-established {foreign_domain} integration "
                f"pattern ({component['justification']}) that has no structural place in "
                f"this {own_domain}-domain estate."
            ),
        )
        return new_estate, draft
