"""
Wires ark.validation.mulesoft_http_connector into the trajectory pipeline
as a standing, automatic step -- the follow-up flagged by the prior
renderer-fix session ("wiring the validator into the pipeline... is a
separate, later task"). This IS that later task.

This module does not modify `ark/validation/mulesoft_http_connector.py` or
`ark/schemas/mulesoft/http_connector.json` at all -- it only calls the
existing, unmodified `validate_http_connector_xml()` and packages its
output into a shape ark.evaluator.report.EvaluationReport can carry as a
new, additive, sibling field (`rendering_validation`) alongside
`agent_performance` -- never folded into it. See RenderingValidationSummary
below and report.py's own field docstring for why: documentation validity
is a property of the rendered estate (the generation/rendering step), not
of the agent being evaluated, so it must never influence category_f1,
entity_localization_accuracy, brier_score, ece, or any other agent-
performance metric.

Granularity decision (checked directly against
ark/adapters/mulesoft/renderer.py + adapter.py, not assumed):
MuleSoftAdapter renders exactly ONE combined XML file per Application
(render_application_xml's own module docstring: "One combined XML file per
application", enforced by adapter.py's `xml_path = f"{app.name}/.../
{app.name}.xml"` -- one path per app, not per flow). `_render_http_connector_
configs()` always emits every listener-config/request-config global element
into that SAME file, alongside every flow that references it via
config-ref -- including ApiCallStep targets that resolve to a *different*
Application's API (the http:request element and the http:request-config it
points at are both still rendered into the CALLING app's own file; see
renderer.py's `_render_step` ApiCallStep branch and
`_render_http_connector_configs`, both scoped to one `app` parameter at a
time, never to the estate as a whole). So global config and usage never
split across files for this adapter today -- per-file validation (one call
to `validate_http_connector_xml` per ".xml" artifact) is correct and
sufficient; no cross-file concatenation is needed. If a future adapter or
renderer change ever DOES split them across files, this module's per-file
assumption would need revisiting -- flagged here explicitly rather than
silently assumed to keep holding forever.

Only ".xml" artifacts are validated -- the MuleSoft adapter's other
artifact kind (per-API ".yaml" metadata files, from `render_api_yaml`) is
not Mule XML at all and has no HTTP-connector elements to check.

Non-blocking-failure decision: a validation *content* issue (e.g. a
dangling config-ref) never raises -- `validate_http_connector_xml` already
converts that into a `ValidationIssue` itself, not an exception. But an
unanticipated *pipeline*-side failure (a bug in this wiring, not in the
XML) must also never abort or crash a trajectory -- the agent still gets
scored normally against the ground-truth ledger regardless of whether the
rendered estate is documentation-valid. `validate_rendered_estate_safe()`
is the function callers in ark.experiment should use for exactly that
guarantee; it degrades any unexpected exception into a
`RenderingValidationSummary.validation_error` string instead of letting it
propagate. Validation failures are surfaced as data for a human to review,
never a hard stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ark.adapters.base import RenderedEstate
from ark.validation.mulesoft_http_connector import ValidationIssue, validate_http_connector_xml

RENDERING_VALIDATION_SCHEMA_VERSION = "0.1.0"


def _issue_to_dict(issue: ValidationIssue) -> dict:
    return {"element": issue.element, "attribute": issue.attribute, "message": issue.message}


@dataclass
class RenderingValidationSummary:
    """Additive, sibling-to-agent-performance validation output attached to
    an EvaluationReport (see report.py's `rendering_validation` field). This
    describes a property of the RENDERED ESTATE (did the adapter produce
    documentation-valid output for the connectors this validator covers),
    never a property of the agent being evaluated. Per the structural
    agent/ground-truth boundary, an agent is never shown this -- it is a
    researcher/pipeline-side output only, the same tier as the ground-truth
    ledger and the rendering manifest.

    Issues are stored as plain dicts (element/attribute/message), not
    `ValidationIssue` objects -- matching how `manifest`/`raw_agent_output`
    are already treated elsewhere in report.py as simple, stable passthrough
    shapes rather than typed objects report.py has to know how to rebuild
    field-by-field if the validator's own dataclass ever changes shape.
    """

    schema_version: str
    validator_name: str
    is_valid: bool
    total_issues: int
    issues_by_artifact: dict[str, list[dict]] = field(default_factory=dict)
    validation_error: str | None = None
    """Set only if running validation itself failed unexpectedly (a bug in
    this wiring or an unreadable artifact) -- NOT a content validation
    issue (those show up in issues_by_artifact/total_issues instead, with
    is_valid=False and validation_error left None). A non-None value here
    means "validation could not run," a strictly different, rarer case
    than "validation ran and found real issues." Always paired with
    is_valid=False, since a validator that couldn't run has not confirmed
    validity."""


def validate_rendered_estate(rendered: RenderedEstate) -> RenderingValidationSummary:
    """Run the existing, unmodified HTTP connector validator against every
    ".xml" artifact in `rendered.artifacts`.

    Never raises for malformed XML *content* -- `validate_http_connector_
    xml()` already converts that into a `ValidationIssue` itself (see its
    own not-well-formed-XML handling). This function can still raise for a
    genuinely unexpected internal error (e.g. `rendered.artifacts` isn't
    the dict shape expected); callers needing an unconditional
    non-blocking guarantee should call `validate_rendered_estate_safe()`
    instead, which this pipeline's runner integration always uses.
    """
    issues_by_artifact: dict[str, list[dict]] = {}
    total_issues = 0

    for path, content in rendered.artifacts.items():
        if not path.endswith(".xml"):
            continue
        result = validate_http_connector_xml(content)
        if result.issues:
            issues_by_artifact[path] = [_issue_to_dict(issue) for issue in result.issues]
            total_issues += len(result.issues)

    return RenderingValidationSummary(
        schema_version=RENDERING_VALIDATION_SCHEMA_VERSION,
        validator_name="mulesoft_http_connector",
        is_valid=total_issues == 0,
        total_issues=total_issues,
        issues_by_artifact=issues_by_artifact,
    )


def validate_rendered_estate_safe(rendered: RenderedEstate) -> RenderingValidationSummary:
    """Same as `validate_rendered_estate()`, but guarantees it never raises
    -- this is what `ark.experiment.runner` calls, so a bug in this wiring
    can never abort or crash a trajectory (see this module's own
    non-blocking-failure decision above). An unexpected internal error is
    captured into `validation_error` instead of propagating."""
    try:
        return validate_rendered_estate(rendered)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: ANY
        # validation-side failure must degrade to data, never crash a
        # trajectory (see this module's non-blocking-failure decision).
        return RenderingValidationSummary(
            schema_version=RENDERING_VALIDATION_SCHEMA_VERSION,
            validator_name="mulesoft_http_connector",
            is_valid=False,
            total_issues=0,
            issues_by_artifact={},
            validation_error=f"{type(exc).__name__}: {exc}",
        )
