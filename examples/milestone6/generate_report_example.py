"""
Milestone 6.4 worked example: the full EvaluationReport produced by
ark.evaluator.orchestrator.evaluate() for the exact same trajectory and
agent output used in Milestone 6.3's example
(generate_example_report.py) -- so the two examples are directly
comparable: 6.3's report is the metrics/calibration/explanation detail;
6.4's report_example.json is the complete, reproducible, serializable
artifact those pieces get assembled into.

Run: PYTHONPATH=. python3 examples/milestone6/generate_report_example.py > examples/milestone6/report_example.json
"""

from __future__ import annotations

import json

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.validate import validate_ground_truth
from ark.evaluator.orchestrator import evaluate
from ark.evaluator.report import report_to_dict
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

# Identical to generate_example_report.py's AGENT_OUTPUT (Milestone 6.3),
# reused here on purpose so the two committed examples describe the same
# evaluation from two different levels of detail.
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
            "artifact_reference": "order-processing-process/src/main/resources/api-order-processing-process-v1.yaml",
            "entity_reference": "Order Processing API v2",
            "issue_type": "naming_drift",
            "explanation": "This API's name looks like it may have drifted from an earlier convention.",
            "confidence": 0.55,
        },
        {
            "artifact_reference": "inventory-system/src/main/mule/inventory-system.xml",
            "entity_reference": "reference to 'log-request-sub-flow'",
            "issue_type": "documentation_decay",
            "explanation": "This step seems to be missing documentation about what it references.",
            "confidence": 0.6,
        },
        {
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

    report = evaluate(
        result.transformed_estate,
        result.ledger,
        rendered.manifest,
        AGENT_OUTPUT,
        # Pinned so this committed example is reproducible byte-for-byte,
        # unlike a real evaluation run (see EvaluationMetadata.generated_at's
        # docstring -- this is the one field real runs won't agree on).
        generated_at="2026-01-01T00:00:00+00:00",
    )

    print(json.dumps(report_to_dict(report), indent=2))


if __name__ == "__main__":
    main()
