"""
run_trajectory_spec() / run_experiment() — Milestone 7's top-level
entry points, the "run Ark end-to-end with a real agent" pipeline.

    TrajectorySpec
        |
        v
    baseline estate (validate_ground_truth() OR generate_estate())
        |
        v
    run_trajectory()                       (ark.mutation)
        |
        v
    adapter.render()                       (ark.adapters)
        |                          \\
        |                           \\ rendered.artifacts ONLY
        |                            v
        |                     run_agent_harness()          (ark.harness)
        |                            |
        |                     raw_agent_output (dict)
        |                            |
        |             validate_rendered_estate_safe(rendered)  (ark.validation.pipeline,
        |                            |                          MuleSoftAdapter output only)
        |                     rendering_validation
        v                            |
    evaluate(transformed_estate, ledger, rendered.manifest, raw_agent_output,
             rendering_validation=rendering_validation)
        |                                                   (ark.evaluator.orchestrator)
        v
    EvaluationReport  ── (per spec) ──> analyze_reports()   (ark.evaluator.analysis)
                                              |
                                              v
                                       ExperimentAnalysis

Isolation boundary made concrete here, not just documented: the agent
(via `run_agent_harness`) is only ever handed `rendered.artifacts` — a
plain `dict[str, str]`. `result.transformed_estate`, `result.ledger`,
`rendered.manifest`, and `rendering_validation` are used exclusively in
the `evaluate(...)` call, which runs strictly AFTER the agent has already
produced its output. The agent never has, and never needs, a reference to
any of the four -- `rendering_validation` in particular is a
researcher/pipeline-side-only signal, per
ark.validation.pipeline/ark.evaluator.report's own documentation of why it
must never reach the agent or influence agent-performance metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ark.adapters.base import TargetAdapter
from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.models import GroundTruthEstate
from ark.core.validate import validate_ground_truth
from ark.evaluator.analysis import ExperimentAnalysis, analysis_to_json, analyze_reports
from ark.evaluator.orchestrator import evaluate
from ark.evaluator.report import EvaluationReport, report_to_json
from ark.experiment.spec import TrajectorySpec
from ark.generator.generator import GenerationManifest, generate_estate
from ark.harness.contract import AgentClient
from ark.harness.runner import run_agent_harness
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES
from ark.validation.pipeline import RenderingValidationSummary, validate_rendered_estate_safe


def _resolve_baseline(spec: TrajectorySpec) -> tuple[GroundTruthEstate, GenerationManifest | None]:
    if spec.generator_config is not None:
        generated = generate_estate(spec.generator_config)
        return generated.estate, generated.manifest
    return validate_ground_truth(spec.baseline_estate_path), None


@dataclass
class TrajectoryRunResult:
    """Milestone 8 addition: the same EvaluationReport run_trajectory_spec()
    always returned, PLUS the rendered artifacts the agent was actually
    shown for this one trajectory.

    Added because a consumer with a legitimate need to display "what did
    the agent actually see" (ark.ui's Artifact Viewer) had no way to get
    at rendered.artifacts at all -- run_trajectory_spec() computed it
    internally and discarded it once the harness call returned. The
    alternative -- having a caller re-render the transformed estate a
    second time just to look at it -- would either duplicate this
    module's own orchestration logic, or risk silently drifting from
    what the agent actually received if render/mutation behavior ever
    changed between the two calls. Exposing the artifacts this function
    already computes is the smaller, more honest fix.
    """

    report: EvaluationReport
    rendered_artifacts: dict[str, str]
    """Exactly rendered.artifacts, verbatim -- the same object
    run_agent_harness() was called with. Never the manifest, the ledger,
    or the transformed estate."""


def run_trajectory_spec_with_artifacts(
    spec: TrajectorySpec,
    agent_client: AgentClient,
    *,
    adapter: TargetAdapter | None = None,
) -> TrajectoryRunResult:
    """Run one full arc (see module docstring) for a single TrajectorySpec
    and return both its EvaluationReport and the rendered artifacts the
    agent was shown.

    `adapter` defaults to MuleSoftAdapter() -- Ark's only shipped adapter
    today; pass a different TargetAdapter to render through anything
    else without any other change to this function.

    This is the one real implementation behind both this function and
    run_trajectory_spec() below (which is now a thin wrapper) -- kept
    private-in-spirit-but-public-in-practice so there is exactly one
    place this orchestration can ever be edited, the same pattern
    ark.evaluator.issues's derive_issues()/derive_issue_diagnostics()
    already established for an analogous "existing function's signature
    must not change, but more of what it already computes needs to be
    exposed" situation.
    """
    resolved_adapter = adapter if adapter is not None else MuleSoftAdapter()
    baseline_estate, generation_manifest = _resolve_baseline(spec)

    result = run_trajectory(baseline_estate, PROFILES[spec.profile_name], seed=spec.seed)
    rendered = resolved_adapter.render(result.transformed_estate)

    # Isolation boundary: only rendered.artifacts crosses into the agent
    # harness. rendered.manifest, result.ledger, and result.transformed_estate
    # are used below, in evaluate(), strictly after this line.
    raw_agent_output = run_agent_harness(rendered.artifacts, agent_client)

    # Documentation validity of the rendered estate -- a property of the
    # rendering step, never shown to the agent (computed after
    # run_agent_harness(), same tier as manifest/ledger/transformed_estate
    # above) and never allowed to influence agent-performance scoring (see
    # ark.validation.pipeline / report.py's rendering_validation field for
    # why it's threaded through evaluate() as a separate, additive
    # argument instead of being folded into anything below). Only
    # MuleSoftAdapter output is HTTP-connector-validated today -- the
    # validator assumes Mule XML shapes, so a non-MuleSoft adapter (there
    # are none shipped yet) gets rendering_validation=None rather than
    # nonsensical or misleading results. validate_rendered_estate_safe()
    # additionally guarantees this step itself can never raise -- a bug in
    # the validation wiring must never abort or crash a trajectory.
    rendering_validation: RenderingValidationSummary | None = None
    if isinstance(resolved_adapter, MuleSoftAdapter):
        rendering_validation = validate_rendered_estate_safe(rendered)

    report = evaluate(
        result.transformed_estate,
        result.ledger,
        rendered.manifest,
        raw_agent_output,
        trajectory_id=spec.label,
        generation_manifest=generation_manifest,
        rendering_validation=rendering_validation,
    )
    return TrajectoryRunResult(report=report, rendered_artifacts=rendered.artifacts)


def run_trajectory_spec(
    spec: TrajectorySpec,
    agent_client: AgentClient,
    *,
    adapter: TargetAdapter | None = None,
) -> EvaluationReport:
    """Run one full arc (see module docstring) for a single TrajectorySpec
    and return its EvaluationReport.

    Signature and return type unchanged since Milestone 7 -- see
    run_trajectory_spec_with_artifacts() (Milestone 8 addition) if you
    also need the rendered artifacts the agent saw.
    """
    return run_trajectory_spec_with_artifacts(spec, agent_client, adapter=adapter).report


@dataclass
class ExperimentRunResult:
    reports: list[EvaluationReport]
    analysis: ExperimentAnalysis
    output_dir: Path | None = None
    artifacts_by_label: dict[str, dict[str, str]] = field(default_factory=dict)
    """Milestone 8 addition: spec.label -> the rendered artifacts that
    trajectory's agent was shown. Defaulted to an empty dict so this is
    purely additive -- no existing caller that only reads .reports/
    .analysis/.output_dir is affected."""


def run_experiment(
    specs: list[TrajectorySpec],
    agent_client: AgentClient,
    *,
    adapter: TargetAdapter | None = None,
    output_dir: str | Path | None = None,
) -> ExperimentRunResult:
    """Run every TrajectorySpec in `specs` through
    run_trajectory_spec_with_artifacts(), then aggregate the resulting
    EvaluationReports with ark.evaluator.analysis.analyze_reports().

    If `output_dir` is given, persists each report as
    `<output_dir>/reports/<spec.label>.json` and the aggregate analysis as
    `<output_dir>/analysis.json`, via the existing report_to_json()/
    analysis_to_json() serializers -- no new serialization format. The
    rendered artifacts each trajectory's agent saw are NOT persisted to
    `output_dir` (they're not part of any existing serialization format,
    and this function stays local-execution/in-memory for that data,
    consistent with Milestone 8's "no database, local execution only"
    scope) -- they're only available in-memory via the returned
    ExperimentRunResult.artifacts_by_label.

    A single spec's failure (agent error, malformed agent output, unknown
    profile name, ...) propagates and aborts the whole run rather than
    silently skipping it: an experiment run is meant to be a complete,
    reproducible artifact, and a partial run should surface as an
    exception, not quietly become a shorter report list.
    """
    reports: list[EvaluationReport] = []
    artifacts_by_label: dict[str, dict[str, str]] = {}
    resolved_output_dir = Path(output_dir) if output_dir is not None else None

    if resolved_output_dir is not None:
        (resolved_output_dir / "reports").mkdir(parents=True, exist_ok=True)

    for spec in specs:
        trajectory_result = run_trajectory_spec_with_artifacts(spec, agent_client, adapter=adapter)
        reports.append(trajectory_result.report)
        artifacts_by_label[spec.label] = trajectory_result.rendered_artifacts
        if resolved_output_dir is not None:
            report_path = resolved_output_dir / "reports" / f"{spec.label}.json"
            report_path.write_text(report_to_json(trajectory_result.report), encoding="utf-8")

    analysis = analyze_reports(reports)

    if resolved_output_dir is not None:
        (resolved_output_dir / "analysis.json").write_text(analysis_to_json(analysis), encoding="utf-8")

    return ExperimentRunResult(
        reports=reports, analysis=analysis, output_dir=resolved_output_dir, artifacts_by_label=artifacts_by_label
    )
