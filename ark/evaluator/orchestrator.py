"""
Evaluation pipeline orchestration — Milestone 6.4.

evaluate() is the single entry point for a full Ark evaluation pass. It
introduces no new computation of its own — it only sequences the
already-built 6.1-6.3 modules in the order Ark_Evaluator_Design.md's data-
flow diagram specifies, and threads their outputs into
report.assemble_report(). Every actual scoring decision (what counts as a
match, a true positive, a calibration error, ...) still lives exactly
where it always has.

    mutation_ledger
        |
        v
    issues.derive_issues() / derive_issue_diagnostics()
        |
    agent_output (raw dict)
        |
        v
    schema.parse_agent_output()
        |
        v
    parser.parse_and_resolve_findings()   (needs rendered_manifest)
        |
        v
    matcher.match_findings()              (needs the Issues above)
        |
        v
    metrics.* / calibration.* / explanation.* / complexity.*
        |
        v
    report.assemble_report() -> EvaluationReport

Isolation: this function's four required parameters are exactly the four
things the report generator is allowed to see (Ark_Architecture_and_Plan.md's
evaluator boundary) — a transformed estate, its mutation ledger, an
already-rendered manifest, and the agent's raw output. It never calls into
any adapter itself (callers render the estate however they like, with
whichever adapter, before calling evaluate() — this is what keeps this
module technology-independent: it consumes a manifest dict, never an
adapter object).

`rendering_validation` (added when the HTTP connector validator was wired
into the pipeline) is a purely optional, additive keyword-only parameter
threaded straight through to report.assemble_report() -- it does not
change the four required parameters above, and this function still never
runs any validator or imports ark.validation itself; the caller (see
ark.experiment.runner) computes it before calling evaluate(), exactly like
it already computes rendered_manifest before calling evaluate().
"""

from __future__ import annotations

from typing import Any

from ark.core.models import GroundTruthEstate
from ark.evaluator.calibration import DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE, compute_calibration
from ark.evaluator.complexity import ComplexityWeights, compute_trajectory_complexity
from ark.evaluator.explanation import extract_explanation_signals_for_matches
from ark.evaluator.issues import derive_issue_diagnostics, derive_issues
from ark.evaluator.matcher import match_findings
from ark.evaluator.metrics import (
    compute_category_metrics,
    compute_category_metrics_by_type,
    compute_entity_localization_metrics,
)
from ark.evaluator.parser import parse_and_resolve_findings
from ark.evaluator.report import DEFAULT_OVERCONFIDENCE_THRESHOLD, EvaluationReport, assemble_report
from ark.evaluator.schema import parse_agent_output
from ark.mutation.ledger import MutationLedger


def evaluate(
    transformed_estate: GroundTruthEstate,
    mutation_ledger: MutationLedger,
    rendered_manifest: dict,
    agent_output: dict,
    *,
    trajectory_id: str | None = None,
    generation_manifest: Any | None = None,
    complexity_weights: ComplexityWeights | None = None,
    calibration_min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE,
    overconfidence_threshold: float = DEFAULT_OVERCONFIDENCE_THRESHOLD,
    generated_at: str | None = None,
    rendering_validation: Any | None = None,
) -> EvaluationReport:
    """Run the full evaluation pipeline and return one EvaluationReport.

    Required:
        transformed_estate: the (post-mutation) GroundTruthEstate the
            agent's rendered artifacts were derived from.
        mutation_ledger: the MutationLedger produced alongside it
            (ark.mutation.engine.run_trajectory's TransformationResult.ledger).
        rendered_manifest: the RenderedEstate.manifest dict from whichever
            adapter rendered transformed_estate (e.g. MuleSoftAdapter) —
            evaluate() never renders anything itself.
        agent_output: the agent's raw output, exactly as produced (a dict
            matching schema.py's required contract) — parsed and validated
            inside this function, not before.

    Optional:
        trajectory_id: defaults to f"{baseline_estate_id}-seed{trajectory_seed}"
            if not given.
        generation_manifest: the generator's GenerationManifest, if
            transformed_estate came from ark.generator — enables
            EvaluationMetadata.generator_version. None (the default) for
            hand-authored ground truth, which has no such provenance.
        complexity_weights / calibration_min_sample_size /
            overconfidence_threshold: pass-throughs to
            compute_trajectory_complexity / compute_calibration /
            report.assemble_report — every one already has a documented
            default in its own module; overriding them here never changes
            what those defaults mean, only which run uses something else.
        generated_at: override for EvaluationMetadata.generated_at,
            primarily for tests that need two reports to compare equal in
            full (including the timestamp field).
        rendering_validation: an already-computed
            ark.validation.pipeline.RenderingValidationSummary (or None),
            threaded straight through to
            EvaluationReport.rendering_validation with zero interpretation
            -- evaluate() does not import ark.validation and does not run
            any validator itself, exactly the same "caller renders/
            validates however it likes, before calling evaluate()"
            discipline this module's docstring already establishes for
            rendering (typed as `Any` rather than the concrete
            RenderingValidationSummary type for the same reason
            generation_manifest is typed `Any` above: keeping this module
            technology-independent, with zero import-time coupling to any
            one adapter's or validator's concrete type).
    """
    resolved_trajectory_id = (
        trajectory_id or f"{mutation_ledger.baseline_estate_id}-seed{mutation_ledger.trajectory_seed}"
    )

    issues = derive_issues(mutation_ledger)
    issue_diagnostics = derive_issue_diagnostics(mutation_ledger)

    parsed_agent_output = parse_agent_output(agent_output)
    resolved_findings = parse_and_resolve_findings(parsed_agent_output.findings, rendered_manifest)
    matches = match_findings(resolved_findings, issues)

    category_metrics = compute_category_metrics(matches, issues)
    category_metrics_by_type = compute_category_metrics_by_type(matches, issues)
    entity_metrics = compute_entity_localization_metrics(matches, issues)
    calibration_result = compute_calibration(
        matches, issues, min_sample_size_for_ece=calibration_min_sample_size
    )
    explanation_signals = extract_explanation_signals_for_matches(matches, issues)
    complexity_profile = compute_trajectory_complexity(
        transformed_estate, mutation_ledger, weights=complexity_weights
    )

    return assemble_report(
        transformed_estate=transformed_estate,
        ledger=mutation_ledger,
        manifest=rendered_manifest,
        raw_agent_output=agent_output,
        issues=issues,
        issue_diagnostics=issue_diagnostics,
        matches=matches,
        category_metrics=category_metrics,
        category_metrics_by_type=category_metrics_by_type,
        entity_metrics=entity_metrics,
        calibration_result=calibration_result,
        explanation_signals=explanation_signals,
        complexity_profile=complexity_profile,
        trajectory_id=resolved_trajectory_id,
        generation_manifest=generation_manifest,
        overconfidence_threshold=overconfidence_threshold,
        generated_at=generated_at,
        rendering_validation=rendering_validation,
    )
