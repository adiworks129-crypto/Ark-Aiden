"""
Estate persistence -- Session A's "Estate Persistence Layer."

generate_estate() (Milestone 3) is deliberately pure and in-memory -- see
its own module docstring -- and, before this module, the ONLY estate that
ever existed on disk was the single hand-authored Milestone 1 file at
examples/milestone1/ground_truth.json. This module adds an OPT-IN way to
persist any GroundTruthEstate this pipeline produces -- generated or
hand-authored, mutated or not -- alongside the rendered artifacts and
mutation ledger that went with it, so a later process can reload exactly
what one trajectory saw and was scored against without re-running
generation, mutation, or rendering.

Layout written under <output_dir>/<estate_id>/:

    ground_truth.json  -- the estate, via ark.core.serialize.estate_to_dict()
                           / json.dumps(). The exact same JSON shape
                           validate_ground_truth() already reads for the
                           Milestone 1 file -- no competing schema. Reloaded
                           with validate_ground_truth() itself, not a
                           separate parser, so "this file loads with
                           existing tooling" is proven structurally, not
                           just claimed.
    mutated_estate.json -- Session G addition: the POST-mutation estate
                           (whatever a caller passes as `mutated_estate`),
                           in the exact same JSON shape as ground_truth.json
                           -- same estate_to_dict()/json.dumps() path, same
                           validate_ground_truth() reload path, no second
                           schema. Optional and independently nullable, same
                           discipline as ledger/generation_manifest below:
                           a caller that doesn't pass `mutated_estate` gets
                           no file written at all (not an empty/null
                           placeholder), so an estate saved before this
                           field existed, or a plain unmutated baseline
                           snapshot, simply has no mutated_estate.json --
                           load_estate() reloads that as `None`, and a UI
                           consumer is expected to show "not available for
                           this estate" rather than treat it as an error.
                           ground_truth.json's own format/content is
                           completely unaffected by this addition -- this
                           is a new sibling file, never a rewrite of the
                           existing one.
    rendered/           -- one file per adapter.render() artifact, written
                           at the exact same relative path
                           RenderedEstate.artifacts already uses (e.g.
                           rendered/OrderAPI/src/main/mule/OrderAPI.xml) --
                           the same in-memory dict[str, str], just written
                           to disk instead of held as strings. Directory
                           structure created on demand; no new artifact
                           layout invented.
    manifest.json       -- {"ledger": ..., "generation_manifest": ...}.
                           "ledger" is the trajectory's MutationLedger (via
                           the already-existing
                           ark.mutation.ledger.ledger_to_dict()) --
                           "which operators were applied," one record per
                           realized mutation. "generation_manifest" is the
                           estate's GenerationManifest (seed/
                           generator_version/schema_version/config) when
                           the estate came from generate_estate(), or null
                           for a hand-authored estate (e.g. Milestone 1),
                           which has none. Both are optional and
                           independently nullable -- a save_estate() call
                           with neither still writes a valid (all-null)
                           manifest.json.

This module is agnostic to which estate a caller hands it -- baseline or
transformed, save_estate() just serializes whatever GroundTruthEstate it's
given. ark.experiment.runner's save_estates flag specifically passes the
BASELINE (pre-mutation) estate, not the transformed one, on purpose: a
ground_truth.json holding the already-mutated estate would make it
impossible to later show a meaningful "before vs. after mutation" diff
against rendered/ (both sides would already describe the same, mutated,
state) -- see TrajectoryRunResult.baseline_estate's docstring in
ark/experiment/runner.py for the full reasoning. A consumer that wants
"before" text for a specific rendered file re-renders this saved estate
through the same adapter (e.g. MuleSoftAdapter().render(loaded.estate))
and looks up the matching relative path -- no second copy of rendered/
needs to be persisted for that to work.

Deliberately NOT saved here: a ComplexityProfile. It is already computed
and already serialized as part of EvaluationReport (see
ark.evaluator.report.report_to_json()) for every trajectory this is used
from (ark.experiment.runner's save_estates flag). Recomputing or
duplicating it here would mean a second, potentially-divergent copy of a
value the evaluator -- explicitly out of scope for this module -- already
owns.

Naming note: `estate_id` here is the on-disk directory name (the caller's
choice of a stable key, e.g. a trajectory label), independent of whatever
the estate object's own `.estate_id` attribute happens to be. The two are
allowed to differ; ground_truth.json always preserves the estate's real
`.estate_id` field untouched.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from ark.core.models import GroundTruthEstate
from ark.core.serialize import estate_to_dict
from ark.core.validate import validate_ground_truth
from ark.generator.generator import GenerationManifest, generation_manifest_from_dict
from ark.mutation.ledger import MutationLedger, ledger_from_dict, ledger_to_dict

GROUND_TRUTH_FILENAME = "ground_truth.json"
MUTATED_ESTATE_FILENAME = "mutated_estate.json"
MANIFEST_FILENAME = "manifest.json"
RENDERED_DIRNAME = "rendered"


@dataclass
class LoadedEstate:
    """Everything load_estate() reconstructs for one estate_id -- mirrors
    save_estate()'s parameters exactly, so a round trip is:
    save_estate(estate, rendered_artifacts, ..., ledger=X,
    generation_manifest=Y, mutated_estate=Z) -> load_estate(...) ->
    LoadedEstate(estate, rendered_artifacts, ledger=X,
    generation_manifest=Y, mutated_estate=Z).

    A distinct type from ark.generator.generator.GeneratedEstate on
    purpose: GeneratedEstate is "what generate_estate() hands back before
    any mutation or rendering has happened" (estate + GenerationManifest
    only) and that shape is explicitly out of scope to change this
    session. LoadedEstate is "everything worth keeping about one
    trajectory's estate after mutation and rendering did happen" -- a
    superset that GeneratedEstate has no way to represent without
    growing fields generate_estate() itself could never populate.
    """

    estate: GroundTruthEstate
    rendered_artifacts: dict[str, str]
    ledger: MutationLedger | None = None
    generation_manifest: GenerationManifest | None = None
    mutated_estate: GroundTruthEstate | None = None
    """Session G addition: the post-mutation estate, reloaded from
    mutated_estate.json -- None if that file wasn't written (either
    save_estate() was called without a `mutated_estate`, or this estate
    was saved before this field existed). `estate` above is always the
    baseline (see that field's own history in save_estate()'s docstring
    and ark.experiment.runner.TrajectoryRunResult.baseline_estate) --
    this field is the only place a saved estate's post-mutation state
    lives structurally (as opposed to only as rendered/ text)."""


def _estate_dir(output_dir: str | Path, estate_id: str) -> Path:
    return Path(output_dir) / estate_id


def save_estate(
    estate: GroundTruthEstate,
    rendered_artifacts: dict[str, str],
    output_dir: str | Path,
    estate_id: str,
    *,
    ledger: MutationLedger | None = None,
    generation_manifest: GenerationManifest | None = None,
    mutated_estate: GroundTruthEstate | None = None,
) -> Path:
    """Write `estate`, `rendered_artifacts`, and optionally `ledger`/
    `generation_manifest`/`mutated_estate` to <output_dir>/<estate_id>/
    (see module docstring for the exact layout). Returns the estate
    directory path.

    `mutated_estate` (Session G addition) is written to
    mutated_estate.json in the exact same shape as `estate`'s own
    ground_truth.json -- and ONLY if given: omitting it (the default)
    writes no mutated_estate.json at all, so an existing caller that
    never passes it (e.g. every call site before this session) produces
    byte-identical output to before. This function does not care whether
    `estate` is the baseline or the mutated estate, or what
    `mutated_estate` "means" relative to it -- it just serializes
    whatever two GroundTruthEstate objects it's given to two sibling
    files; ark.experiment.runner's save_estates flag is what decides to
    pass the baseline as `estate` and the transformed estate as
    `mutated_estate` (see that call site).

    Signature note: the original spec for this function named its first
    parameter `estate: GeneratedEstate`. Checked against the real type --
    ark.generator.generator.GeneratedEstate only ever holds an estate plus
    a GenerationManifest; it has no rendered-artifacts or ledger field,
    and never will (it's constructed before mutation or rendering ever
    run). Bundling those into it would mean either changing
    GeneratedEstate's shape (risking generate_estate() callers that
    pattern-match its two fields) or leaving this function unable to
    persist the two things ("rendered artifacts" and "mutation manifest")
    the spec explicitly asks for. Explicit parameters instead, each typed
    for what it actually is, avoids both problems and keeps every
    argument's meaning unambiguous at the call site.

    Idempotent/overwriting: calling this twice with the same
    (output_dir, estate_id) overwrites what was there -- no merge, no
    versioning. A caller that wants history should vary estate_id (e.g.
    include the seed, as ark.experiment.runner's per-trajectory label
    already does).
    """
    estate_dir = _estate_dir(output_dir, estate_id)
    rendered_dir = estate_dir / RENDERED_DIRNAME
    rendered_dir.mkdir(parents=True, exist_ok=True)

    (estate_dir / GROUND_TRUTH_FILENAME).write_text(
        json.dumps(estate_to_dict(estate), indent=2), encoding="utf-8"
    )

    if mutated_estate is not None:
        (estate_dir / MUTATED_ESTATE_FILENAME).write_text(
            json.dumps(estate_to_dict(mutated_estate), indent=2), encoding="utf-8"
        )

    for relative_path, content in rendered_artifacts.items():
        artifact_path = rendered_dir / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")

    manifest_payload = {
        "ledger": ledger_to_dict(ledger) if ledger is not None else None,
        "generation_manifest": (
            None if generation_manifest is None else _generation_manifest_to_dict(generation_manifest)
        ),
    }
    (estate_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")

    return estate_dir


def load_estate(estate_dir: str | Path) -> LoadedEstate:
    """Reconstruct everything save_estate() wrote to `estate_dir`.

    ground_truth.json is reloaded through validate_ground_truth() itself --
    the same structural + referential validation any other Ark ground-truth
    file goes through, not a shortcut parser -- which is also the concrete
    proof that save_estate()'s output really is loadable by existing
    tooling, not just shaped to look like it.

    rendered/ is walked recursively; every file found becomes one
    dict[str, str] entry, keyed by its path relative to rendered/ (POSIX
    separators, matching how RenderedEstate.artifacts keys already look).
    An estate saved with no rendered artifacts at all (an empty dict) will
    have no rendered/ directory to walk -- treated as zero artifacts, not
    an error.

    manifest.json's two entries are each independently optional: a null
    (or missing) "ledger"/"generation_manifest" reloads as None, exactly
    mirroring whatever save_estate() was given.

    mutated_estate.json (Session G addition) is reloaded the same way as
    ground_truth.json -- through validate_ground_truth() itself, same
    reasoning -- but only if the file exists; a missing mutated_estate.json
    (an estate saved before this field existed, or saved without one on
    purpose) reloads as `None`, not an error.
    """
    estate_dir = Path(estate_dir)
    estate = validate_ground_truth(estate_dir / GROUND_TRUTH_FILENAME)

    mutated_estate: GroundTruthEstate | None = None
    mutated_estate_path = estate_dir / MUTATED_ESTATE_FILENAME
    if mutated_estate_path.is_file():
        mutated_estate = validate_ground_truth(mutated_estate_path)

    rendered_dir = estate_dir / RENDERED_DIRNAME
    rendered_artifacts: dict[str, str] = {}
    if rendered_dir.is_dir():
        for file_path in sorted(rendered_dir.rglob("*")):
            if file_path.is_file():
                relative_path = file_path.relative_to(rendered_dir).as_posix()
                rendered_artifacts[relative_path] = file_path.read_text(encoding="utf-8")

    ledger: MutationLedger | None = None
    generation_manifest: GenerationManifest | None = None
    manifest_path = estate_dir / MANIFEST_FILENAME
    if manifest_path.is_file():
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_payload.get("ledger") is not None:
            ledger = ledger_from_dict(manifest_payload["ledger"])
        if manifest_payload.get("generation_manifest") is not None:
            generation_manifest = generation_manifest_from_dict(manifest_payload["generation_manifest"])

    return LoadedEstate(
        estate=estate,
        rendered_artifacts=rendered_artifacts,
        ledger=ledger,
        generation_manifest=generation_manifest,
        mutated_estate=mutated_estate,
    )


def _generation_manifest_to_dict(generation_manifest: GenerationManifest) -> dict:
    return dataclasses.asdict(generation_manifest)
