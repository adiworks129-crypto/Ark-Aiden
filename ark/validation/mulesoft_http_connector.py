"""
HTTP Connector structural validator — checks a rendered MuleSoft artifact's
XML text against `ark/schemas/mulesoft/http_connector.json`.

Scope, deliberately narrow (see the Feature 1 task this was built for):
only the HTTP connector, only structural checks (required attributes
present, no unknown/invented attributes or authentication schemes,
config-ref names resolve to the RIGHT KIND of global element declared
somewhere in the same XML text) -- no attempt to validate DataWeave
bodies, flow logic, or any non-HTTP element. Every rule enforced here
traces back to one `elements` entry in the schema file, which itself
cites a real docs.mulesoft.com page.

Pure functions only: `validate_http_connector_xml()` takes a string and
returns a `ValidationResult`, with no I/O, no estate/manifest access, and
no side effects. This module itself is still not modified by, and knows
nothing about, the pipeline -- it is called (unmodified) from
`ark/validation/pipeline.py`, which is what `ark/experiment/runner.py` uses
to run validation automatically as a standing trajectory step. See
`pipeline.py` for that wiring and the granularity/non-blocking-failure
decisions behind it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "mulesoft" / "http_connector.json"

_XMLNS_DECLARATION_RE = re.compile(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"')


@dataclass
class ValidationIssue:
    """One structural problem found in the validated XML, always pointing
    at the specific element/attribute and the schema rule it violates --
    never a bare "invalid" with no actionable detail."""

    element: str
    """The qualified tag this issue is about, e.g. "http:listener" or
    "http:request-config" -- always one of the schema's own element
    names, so an issue is always traceable back to a specific,
    documented rule."""
    message: str
    attribute: str | None = None


@dataclass
class ValidationResult:
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def __bool__(self) -> bool:  # pragma: no cover - trivial convenience
        return self.is_valid


def load_schema(schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> dict:
    """Read the HTTP connector schema JSON. A plain `json.load` -- the
    schema is data, not code, and this function does no interpretation of
    it beyond parsing."""
    with open(schema_path, encoding="utf-8") as handle:
        return json.load(handle)


def _namespace_uri_to_prefix(xml_text: str) -> dict[str, str]:
    """Map each declared namespace URI -> the prefix the SOURCE TEXT itself
    used for it (e.g. "http://www.mulesoft.org/schema/mule/http" -> "http").
    ElementTree normalizes every qualified tag to "{uri}local" regardless
    of the prefix originally written; this recovers the human-readable
    "prefix:local" form the schema file's element names use, by reading
    the raw `xmlns:prefix="uri"` declarations directly out of the text
    (ElementTree does not expose the declared prefixes itself)."""
    return {uri: prefix for prefix, uri in _XMLNS_DECLARATION_RE.findall(xml_text)}


def _qualified_tag(tag: str, ns_to_prefix: dict[str, str]) -> str:
    """"{uri}local" -> "prefix:local" using the source text's own prefix
    for that uri; a tag with no namespace (Mule's default/core elements,
    e.g. <flow>, <logger>) is returned as-is."""
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    prefix = ns_to_prefix.get(uri)
    return f"{prefix}:{local}" if prefix else tag


def validate_http_connector_xml(
    xml_text: str, schema: dict | None = None
) -> ValidationResult:
    """Validate every HTTP-connector element found anywhere in `xml_text`
    against `schema` (loads the default HTTP connector schema if not
    given). Non-HTTP elements (`<flow>`, `<logger>`, `<ee:transform>`,
    etc.) are walked over but never flagged -- this validator's scope is
    exactly the elements named in the schema's `"elements"` map, nothing
    wider.

    Checks performed per matched element:
    - every `required_attributes` entry is present
    - every attribute present is in `required_attributes` OR
      `optional_attributes` (an attribute not documented for this element
      is flagged, not silently allowed)
    - every `required_children` qualified tag appears at least once among
      direct children
    - for `required_children_choice` (currently only `http:authentication`):
      exactly one of the listed schemes is present as a direct child --
      zero or more-than-one are both flagged

    Additionally, across the whole document: every `http:listener`'s
    `config-ref` must name an `http:listener-config` declared somewhere in
    `xml_text`, and every `http:request`'s `config-ref` must name an
    `http:request-config` -- pointing at the other kind (or at nothing at
    all) is flagged, enforcing that these are two distinct configuration
    elements, not interchangeable, per the schema's own
    `config_ref_target`.
    """
    schema = schema if schema is not None else load_schema()
    elements_schema: dict = schema["elements"]
    issues: list[ValidationIssue] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return ValidationResult(
            is_valid=False,
            issues=[ValidationIssue(element="(document)", message=f"Not well-formed XML: {exc}")],
        )

    ns_to_prefix = _namespace_uri_to_prefix(xml_text)

    listener_config_names: set[str] = set()
    request_config_names: set[str] = set()
    all_elements: list[tuple[str, ET.Element]] = []

    for elem in root.iter():
        qtag = _qualified_tag(elem.tag, ns_to_prefix)
        all_elements.append((qtag, elem))
        if qtag == "http:listener-config" and "name" in elem.attrib:
            listener_config_names.add(elem.attrib["name"])
        elif qtag == "http:request-config" and "name" in elem.attrib:
            request_config_names.add(elem.attrib["name"])

    for qtag, elem in all_elements:
        rule = elements_schema.get(qtag)
        if rule is None:
            continue  # out of this schema's scope, not an error

        child_tags = {_qualified_tag(child.tag, ns_to_prefix) for child in elem}

        _check_attributes(qtag, elem, rule, issues)
        _check_required_children(qtag, rule, child_tags, issues)
        _check_children_choice(qtag, rule, child_tags, issues)

        if qtag == "http:listener":
            _check_config_ref(qtag, elem, "http:listener-config", listener_config_names, request_config_names, issues)
        elif qtag == "http:request":
            _check_config_ref(qtag, elem, "http:request-config", request_config_names, listener_config_names, issues)

    return ValidationResult(is_valid=len(issues) == 0, issues=issues)


def _check_attributes(qtag: str, elem: ET.Element, rule: dict, issues: list[ValidationIssue]) -> None:
    required = set(rule.get("required_attributes", []))
    optional = set(rule.get("optional_attributes", []))
    allowed = required | optional

    for attr_name in required:
        if attr_name not in elem.attrib:
            issues.append(
                ValidationIssue(
                    element=qtag, attribute=attr_name,
                    message=f"Missing required attribute '{attr_name}' on <{qtag}>.",
                )
            )

    for attr_name in elem.attrib:
        if attr_name not in allowed:
            issues.append(
                ValidationIssue(
                    element=qtag, attribute=attr_name,
                    message=(
                        f"Unknown attribute '{attr_name}' on <{qtag}> -- not in the documented "
                        f"schema for this element (see ark/schemas/mulesoft/http_connector.json)."
                    ),
                )
            )


def _check_required_children(qtag: str, rule: dict, child_tags: set[str], issues: list[ValidationIssue]) -> None:
    for required_child in rule.get("required_children", []):
        if required_child not in child_tags:
            issues.append(
                ValidationIssue(
                    element=qtag,
                    message=f"<{qtag}> is missing its required child <{required_child}>.",
                )
            )


def _check_children_choice(qtag: str, rule: dict, child_tags: set[str], issues: list[ValidationIssue]) -> None:
    choice = rule.get("required_children_choice")
    if not choice:
        return
    present = [candidate for candidate in choice if candidate in child_tags]
    if len(present) == 0:
        issues.append(
            ValidationIssue(
                element=qtag,
                message=(
                    f"<{qtag}> declares no authentication scheme -- expected exactly one of "
                    f"{sorted(choice)}."
                ),
            )
        )
    elif len(present) > 1:
        issues.append(
            ValidationIssue(
                element=qtag,
                message=f"<{qtag}> declares more than one authentication scheme: {sorted(present)}.",
            )
        )


def _check_config_ref(
    qtag: str,
    elem: ET.Element,
    expected_config_kind: str,
    matching_names: set[str],
    other_kind_names: set[str],
    issues: list[ValidationIssue],
) -> None:
    config_ref = elem.attrib.get("config-ref")
    if not config_ref:
        return  # already flagged as a missing required attribute above
    if config_ref in matching_names:
        return
    if config_ref in other_kind_names:
        wrong_kind = "http:request-config" if expected_config_kind == "http:listener-config" else "http:listener-config"
        issues.append(
            ValidationIssue(
                element=qtag, attribute="config-ref",
                message=(
                    f"<{qtag}> config-ref=\"{config_ref}\" resolves to a {wrong_kind}, but <{qtag}> "
                    f"requires a {expected_config_kind} -- these are two distinct configuration "
                    f"elements, not interchangeable."
                ),
            )
        )
        return
    issues.append(
        ValidationIssue(
            element=qtag, attribute="config-ref",
            message=(
                f"<{qtag}> config-ref=\"{config_ref}\" does not resolve to any {expected_config_kind} "
                f"declared in this document."
            ),
        )
    )
