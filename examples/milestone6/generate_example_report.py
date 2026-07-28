"""
Milestone 6.3 worked example: a full evaluator pass, end to end, against a
small synthetic estate -- baseline -> mutation -> render -> (illustrative,
hand-crafted) agent output -> parse -> resolve -> match -> metrics ->
calibration -> explanation signals -> complexity -> a printed report.

The agent output below is hand-written, not from a real model, and is
deliberately a MIX of outcomes so every metric this milestone introduces
has something real to show:

- two correct findings (naming_drift on the Order Status Experience API,
  documentation_decay on a Customer step)
- one finding with the right issue_type but pointed at an entity that was
  never actually mutated (a "wrong entity" claim)
- one finding pointed at the exact right entity but naming the wrong
  issue_type (a "wrong category" claim)
- one pure hallucination (a real artifact and entity, but nothing was
  ever mutated there)
- one real issue (duplicate_processing) the agent never mentions at all

Run: PYTHONPATH=. python3 examples/milestone6/generate_example_report.py
"""

from __future__ import annotations

import json

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.validate import validate_ground_truth
from ark.evaluator.calibration import compute_calibration
from ark.evaluator.complexity import build_trajectory_performance_record, compute_trajectory_complexity
from ark.evaluator.explanation import extract_explanation_signals_for_matches
from ark.evaluator.issues import derive_issues
from ark.evaluator.matcher import match_findings
from ark.evaluator.metrics import compute_category_metrics, compute_category_metrics_by_type, compute_entity_localization_metrics
from ark.evaluator.parser import parse_and_resolve_findings
from ark.evaluator.schema import parse_agent_output
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

AGENT_OUTPUT = {
    "findings": [
        {
            "artifact_reference": "order-status-experience/src/main/resources/api-order-status-experience-v1.yaml",
            "entity_reference": "OrderStatusE",
            "issue_type": "naming_drift",
            "explanation": "In api-order-status-experience-v1.yaml the API's title is now 'OrderStatusE', "
                            "an abbreviated form that no longer matches the rest of the estate's naming convention.",
            "confidence": 0.9,
        },
        {
            "artifact_reference": "customer-system/src/main/mule/customer-system.xml",
            "entity_reference": "Build Customer Response",
            "issue_type": "documentation_decay",
            "explanation": "The Build Customer Response transform step has no description, so its purpose "
                            "isn't documented in the flow.",
            "confidence": 0.75,
        },
        {
            # Wrong entity: real artifact, but this entity was never mutated.
            "artifact_reference": "order-processing-process/src/main/resources/api-order-processing-process-v1.yaml",
            "entity_reference": "Order Processing API v2",
            "issue_type": "naming_drift",
            "explanation": "This API's name looks like it may have drifted from an earlier convention.",
            "confidence": 0.55,
        },
        {
            # Wrong category: right entity (a real dependency_change issue), wrong issue_type claimed.
            "artifact_reference": "inventory-system/src/main/mule/inventory-system.xml",
            "entity_reference": "reference to 'log-request-sub-flow'",
            "issue_type": "documentation_decay",
            "explanation": "This step seems to be missing documentation about what it references.",
            "confidence": 0.6,
        },
        {
            # Pure hallucination: real artifact/entity, nothing actually mutated here.
            "artifact_reference": "order-processing-process/src/main/mule/order-processing-process.xml",
            "entity_reference": "Build Processing Result",
            "issue_type": "schema_inconsistency",
            "explanation": "The output schema fields look inconsistent with related components.",
            "confidence": 0.95,
        },
    ]
}


def main() -> None:
    baseline = validate_ground_truth("examples/milestone1/ground_truth.json")
    result = run_trajectory(baseline, PROFILES["level_2_structural"], seed=7)
    rendered = MuleSoftAdapter().render(result.transformed_estate)
    issues = derive_issues(result.ledger)

    agent_output = parse_agent_output(AGENT_OUTPUT)
    resolved = parse_and_resolve_findings(agent_output.findings, rendered.manifest)
    matches = match_findings(resolved, issues)

    category_metrics = compute_category_metrics(matches, issues)
    category_by_type = compute_category_metrics_by_type(matches, issues)
    entity_metrics = compute_entity_localization_metrics(matches, issues)
    calibration = compute_calibration(matches, issues)
    explanation_signals = extract_explanation_signals_for_matches(matches, issues)
    complexity_profile = compute_trajectory_complexity(result.transformed_estate, result.ledger)
    performance_record = build_trajectory_performance_record(
        trajectory_id=f"{result.ledger.baseline_estate_id}-seed{result.ledger.trajectory_seed}",
        complexity_profile=complexity_profile,
        category_metrics=category_metrics,
        category_metrics_by_type=category_by_type,
        entity_metrics=entity_metrics,
        calibration_result=calibration,
    )

    report = {
        "trajectory_id": performance_record.trajectory_id,
        "profile_name": result.ledger.profile_name,
        "real_issues": len(issues),
        "agent_findings": len(matches),
        "per_finding": [
            {
                "finding_id": m.finding_id,
                "matched_issue_id": m.matched_issue_id,
                "category_correct": m.category_correct,
                "entity_correct": m.entity_correct,
                "artifact_reference_correct": m.artifact_reference_correct,
                "confidence": m.confidence,
            }
            for m in matches
        ],
        "category_metrics": category_metrics.__dict__,
        "category_metrics_by_type": {k: v.__dict__ for k, v in category_by_type.items()},
        "entity_localization_metrics": entity_metrics.__dict__,
        "calibration": {
            "sample_size": calibration.sample_size,
            "brier_score": calibration.brier_score,
            "ece": calibration.ece,
        },
        "explanation_signals": [s.__dict__ for s in explanation_signals],
        "complexity_score": complexity_profile.complexity_score,
        "mutation_count": complexity_profile.mutation_count,
        "agent_accuracy": performance_record.agent_accuracy,
        "calibration_error": performance_record.calibration_error,
    }

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
