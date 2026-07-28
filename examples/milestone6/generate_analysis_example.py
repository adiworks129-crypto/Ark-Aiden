"""
Milestone 6.5 worked example generator.

Builds a small batch of EvaluationReports across all four difficulty
profiles (level_0_clean .. level_3_legacy) and several seeds each, using a
SIMULATED agent (not a real model -- same spirit as
generate_report_example.py's hand-written agent, just parameterized so its
accuracy and confidence are deliberately, synthetically complexity-
dependent) so the resulting analysis_example.json has real signal in it:
performance should visibly degrade at higher levels, and the mock agent is
deliberately overconfident at higher levels too, so calibration drift is
visible.

This script is allowed to read the ledger/issues/manifest directly to
construct its findings -- it is standing in for "a real agent's output,"
exactly like generate_report_example.py's hand-authored AGENT_OUTPUT does.
It is not part of ark's evaluator itself and never violates the agent-
isolation boundary in any real evaluation (a real evaluate() call still
only ever receives an agent's independently-produced raw output dict).

Run: PYTHONPATH=. python3 examples/milestone6/generate_analysis_example.py > examples/milestone6/analysis_example.json
"""

from __future__ import annotations

import json
import random

from ark.adapters.mulesoft.adapter import MuleSoftAdapter
from ark.core.validate import validate_ground_truth
from ark.evaluator.analysis import analysis_to_dict, analyze_reports
from ark.evaluator.issues import derive_issues
from ark.evaluator.orchestrator import evaluate
from ark.mutation.engine import run_trajectory
from ark.mutation.profiles import PROFILES

# Per-profile-level mock agent behavior: (correct_detection_probability,
# hallucination_count, confidence_when_correct, confidence_when_wrong).
# Deliberately monotonic with level: a weaker, more overconfident agent at
# higher complexity, to give the example something real to show.
#
# Note on level 0 (the clean baseline): it has zero real Issues by
# definition, so metrics.py's recall (and therefore category_f1 and
# entity_localization_accuracy) is structurally None for every clean
# trajectory regardless of what the agent does -- see metrics.py's
# ClassificationMetrics.recall docstring ("None when true_positives +
# false_negatives == 0 -- no real issues to find at all"). This is
# existing, frozen Milestone 6.3 behavior, not something analysis.py
# works around. To still give calibration_ece a real (non-None) baseline
# value in this worked example, level 0's mock agent hallucinates enough
# findings (>= calibration.py's DEFAULT_MIN_SAMPLE_SIZE_FOR_ECE) with
# varied confidence for its per-report ECE to be computable.
LEVEL_BEHAVIOR = {
    0: (1.0, 6, 0.6, 0.6),
    1: (0.85, 0, 0.8, 0.75),
    2: (0.55, 1, 0.85, 0.85),
    3: (0.35, 2, 0.9, 0.92),
}

SEEDS_PER_PROFILE = 4


def _entity_label(manifest: dict, entity_id: str) -> tuple[str, str]:
    entry = manifest["entity_index"][entity_id]
    return entry["artifact_path"], entry.get("name") or entity_id


def _build_agent_output(rng: random.Random, manifest: dict, issues: list, level: int) -> dict:
    p_correct, hallucination_count, conf_correct, conf_wrong = LEVEL_BEHAVIOR[level]
    findings = []

    for issue in issues:
        entity_id = issue.affected_entity_ids[0]
        artifact_path, entity_name = _entity_label(manifest, entity_id)
        if rng.random() < p_correct:
            findings.append(
                {
                    "artifact_reference": artifact_path,
                    "entity_reference": entity_name,
                    "issue_type": issue.issue_type,
                    "explanation": f"{entity_name} shows a real '{issue.issue_type}' change in {artifact_path}.",
                    "confidence": conf_correct,
                }
            )
        else:
            # A wrong-category claim on the right entity, or a miss
            # entirely -- alternate so both failure modes show up.
            if rng.random() < 0.5:
                wrong_type = "schema_inconsistency" if issue.issue_type != "schema_inconsistency" else "naming_drift"
                findings.append(
                    {
                        "artifact_reference": artifact_path,
                        "entity_reference": entity_name,
                        "issue_type": wrong_type,
                        "explanation": f"{entity_name} looks inconsistent in {artifact_path}.",
                        "confidence": conf_wrong,
                    }
                )
            # else: a silent miss (no finding at all).

    all_entities = list(manifest["entity_index"].items())
    affected_ids = {eid for issue in issues for eid in issue.affected_entity_ids}
    unaffected = [(eid, entry) for eid, entry in all_entities if eid not in affected_ids]
    rng.shuffle(unaffected)
    for entity_id, entry in unaffected[:hallucination_count]:
        findings.append(
            {
                "artifact_reference": entry["artifact_path"],
                "entity_reference": entry.get("name") or entity_id,
                "issue_type": "naming_drift",
                "explanation": f"{entry.get('name') or entity_id} appears to have drifted from convention.",
                # Varied (not uniform) confidence so a clean-baseline
                # report with several hallucinations still populates more
                # than one ECE bin -- see the LEVEL_BEHAVIOR note above.
                "confidence": round(rng.uniform(0.3, 0.95), 2),
            }
        )

    return {"findings": findings}


def main() -> None:
    baseline = validate_ground_truth("examples/milestone1/ground_truth.json")
    reports = []

    for profile_name, profile in PROFILES.items():
        for seed in range(1, SEEDS_PER_PROFILE + 1):
            rng = random.Random(1000 * profile.level + seed)
            result = run_trajectory(baseline, profile, seed=seed)
            rendered = MuleSoftAdapter().render(result.transformed_estate)
            issues = derive_issues(result.ledger)
            agent_output = _build_agent_output(rng, rendered.manifest, issues, profile.level)

            report = evaluate(
                result.transformed_estate,
                result.ledger,
                rendered.manifest,
                agent_output,
                trajectory_id=f"{profile_name}-seed{seed}",
                generated_at="2026-01-01T00:00:00+00:00",
            )
            reports.append(report)

    analysis = analyze_reports(reports, generated_at="2026-01-01T00:00:00+00:00")
    print(json.dumps(analysis_to_dict(analysis), indent=2))


if __name__ == "__main__":
    main()
