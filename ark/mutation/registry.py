"""
The operator registry: transformation_type string -> operator instance.

profiles.py references operators by name (string) rather than importing
classes directly, so profiles stay declarative data, not code — adding a
7th operator later means adding one class in operators.py and one line
here, not touching any profile definition.
"""

from __future__ import annotations

from ark.mutation.base import MutationOperator
from ark.mutation.operators import (
    DependencyChangeOperator,
    DocumentationDecayOperator,
    DomainComponentInjectionOperator,
    DuplicateProcessingOperator,
    LegacyVersionOperator,
    NamingDriftOperator,
    SchemaInconsistencyOperator,
)

OPERATOR_REGISTRY: dict[str, MutationOperator] = {
    op.transformation_type: op
    for op in [
        NamingDriftOperator(),
        DocumentationDecayOperator(),
        DuplicateProcessingOperator(),
        LegacyVersionOperator(),
        SchemaInconsistencyOperator(),
        DependencyChangeOperator(),
        # Operator #7 (Feature 2) — added alongside the original six, never
        # blended into them. Registering it here is what makes
        # "domain_implausible_component" show up automatically in
        # ark.evaluator.schema.ISSUE_TYPE_TAXONOMY (and therefore in the
        # agent's live-sourced prompt) — see that module's own comment.
        # It stays OUT of every existing Level 0-3 profile
        # (ark/mutation/profiles.py) and is only reachable via its own,
        # explicitly opt-in profile.
        DomainComponentInjectionOperator(),
    ]
}
