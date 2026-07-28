"""
General MuleSoft XML/YAML renderer — Milestone 2.

This supersedes examples/milestone0/render.py, which was deliberately a
one-off hand-written function that only ever handled that one example
flow shape (see its docstring). This module renders *any* estate
conforming to the current ground-truth schema: any number of
applications, any number of flows per application (main or sub-flow), and
all five step kinds (Transform/FlowRef/Logger/ApiCall, plus Connector —
added for Feature 2's domain-conditioned component injection; see
`_render_step`'s ConnectorStep branch for the rendering-fidelity decision
behind it).

Scope decisions (see Ark_Architecture_and_Plan.md, Milestone 2 section,
for the full write-up):

- One combined XML file per application (real Mule projects can split
  flows across many files; which file a flow "belongs in" isn't a
  ground-truth concept, so one file per app is the minimal realistic
  convention, not a gap).
- Ground-truth entity ids are deliberately NOT embedded as comments in the
  rendered artifacts. Ark_Architecture_and_Plan.md already flags
  ground-truth/artifact drift as a top risk; adding a second,
  independently-drifting provenance mechanism inside the artifacts
  themselves would make that worse. The rendering manifest (manifest.py)
  is the single authoritative artifact <-> entity mapping.
- pom.xml, mule-artifact.json, environment .properties files, and API
  Manager policy bindings are not generated — none of them affect how an
  agent would reason about flow/API/mapping logic, which is what these
  artifacts are for.
"""

from __future__ import annotations

from ark.core.models import (
    API,
    ApiCallStep,
    Application,
    ConnectorStep,
    Flow,
    FlowRefStep,
    GroundTruthEstate,
    HttpListenerTrigger,
    LoggerStep,
    SchedulerTrigger,
    TransformStep,
)

# Static adapter-side boilerplate, not derived from ground truth — same
# treatment as Milestone 0's schemaLocation constant (see its render.py).
_SCHEMA_LOCATION = (
    "\n"
    "http://www.mulesoft.org/schema/mule/core http://www.mulesoft.org/schema/mule/core/current/mule.xsd\n"
    "http://www.mulesoft.org/schema/mule/http http://www.mulesoft.org/schema/mule/http/current/mule-http.xsd\n"
    "http://www.mulesoft.org/schema/mule/ee/core http://www.mulesoft.org/schema/mule/ee/core/current/mule-ee.xsd"
)

# Requester connector config name. Ground truth records a listener config
# ref per HttpListenerTrigger (real data, since a real Mule dev chooses that
# name), but has no equivalent field for outbound requester configs — a
# request connector config is adapter-side plumbing, not a ground-truth
# concept, so this is a fixed convention rather than something read from
# the estate.
_HTTP_REQUEST_CONFIG_REF = "HTTP_Request_config"

# Placeholder connection details for the global http:listener-config /
# http:request-config elements every config-ref needs to resolve to (see
# _render_http_connector_configs()). Same reasoning as
# _HTTP_REQUEST_CONFIG_REF above: host/port are adapter-side rendering
# plumbing, not a ground-truth concept -- HttpListenerTrigger only ever
# records path/method/listener_config_ref (see ark/core/models.py), so
# there is no real value to read here, and no existing convention
# elsewhere in the generator to reuse (checked: no host/port/base_url
# field exists anywhere in ark/core/models.py or ark/generator/). These
# values match the real docs.mulesoft.com XML reference's own examples
# (http-connector-xml-reference / http-authentication) almost verbatim,
# rather than being arbitrary: "0.0.0.0" for a listener ("All Interfaces",
# the docs' own default) and "localhost" for a requester.
_LISTENER_HOST = "0.0.0.0"
_LISTENER_BASE_PORT = 8081
"""Every distinct listener_config_ref name found in one Application gets
its own port, starting here and incrementing in sorted-name order -- so
two distinct listener configs in the same file never share a bind
address, even though neither value is a real ground-truth concept."""
_REQUEST_HOST = "localhost"
_REQUEST_PORT = 8082
"""Only one http:request-config is ever rendered per Application (see
_HTTP_REQUEST_CONFIG_REF), so this needs no per-name variation."""


class MuleSoftRenderError(Exception):
    """Raised when the ground truth can't be faithfully rendered into
    MuleSoft XML — e.g. an ApiCallStep targets an API whose entry flow has
    no resolvable HTTP contract. Ark prefers a loud, explicit failure here
    over silently emitting misleading artifacts."""


def index_apis(estate: GroundTruthEstate) -> dict[str, tuple[Application, API, Flow | None]]:
    """Build an estate-wide lookup: API id -> (owning Application, API, entry Flow).

    Used to resolve ApiCallStep targets, which — unlike FlowRefStep — are
    allowed to point at an API owned by a different Application (see the
    Milestone 1 note on the two referential-integrity scopes).
    """
    index: dict[str, tuple[Application, API, Flow | None]] = {}
    for app in estate.applications:
        flows_by_id = {flow.id: flow for flow in app.flows}
        for api in app.apis:
            index[api.id] = (app, api, flows_by_id.get(api.entry_flow_id))
    return index


def render_application_xml(app: Application, api_index: dict) -> tuple[str, list[dict]]:
    """Render one Application's flows into a single Mule XML string.

    Returns (xml_text, entities) where entities is a flat list of
    {"id", "type", ...} dicts for every Application/Flow/Step rendered
    into this file — the raw material manifest.py turns into the
    artifact <-> entity mapping.
    """
    flow_names_by_id = {flow.id: flow.name for flow in app.flows}

    entities: list[dict] = [{"id": app.id, "type": "Application", "name": app.name}]

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        '<mule xmlns="http://www.mulesoft.org/schema/mule/core"\n'
        '      xmlns:http="http://www.mulesoft.org/schema/mule/http"\n'
        '      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core"\n'
        '      xmlns:doc="http://www.mulesoft.org/schema/mule/documentation"\n'
        '      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        f'      xsi:schemaLocation="{_SCHEMA_LOCATION}">'
    )

    config_lines = _render_http_connector_configs(app)
    if config_lines:
        lines.append("")
        lines.extend(config_lines)

    for flow in app.flows:
        lines.append("")
        flow_lines, flow_entities = _render_flow(flow, app, api_index, flow_names_by_id)
        lines.extend(flow_lines)
        entities.extend(flow_entities)

    lines.append("")
    lines.append("</mule>")
    lines.append("")
    return "\n".join(lines), entities


def _render_http_connector_configs(app: Application) -> list[str]:
    """Render the global `http:listener-config` / `http:request-config`
    elements every `config-ref` this Application's flows use actually
    needs to resolve to -- previously missing entirely (every config-ref
    was dangling; see ark/validation/mulesoft_http_connector.py, built and
    run against real output in a prior session, which is what surfaced
    this).

    One `http:listener-config` per DISTINCT `listener_config_ref` name
    used anywhere in this app's flows (several flows legitimately sharing
    one name is the real, documented MuleSoft pattern -- config-ref exists
    specifically so a name can be reused, not duplicated per flow). At
    most one `http:request-config`, named `_HTTP_REQUEST_CONFIG_REF`,
    emitted only if this app has at least one ApiCallStep anywhere (no
    outbound calls, no request-config needed).

    Ground truth itself supplies the listener_config_ref NAME (a real Mule
    developer's choice); it does not supply host/port for either kind of
    config, so those are adapter-side placeholders -- see the module-level
    comment on _LISTENER_HOST/_REQUEST_HOST for why, and why that's the
    right call rather than inventing ground-truth fields that don't exist.
    """
    listener_names = sorted(
        {
            flow.trigger.listener_config_ref
            for flow in app.flows
            if isinstance(flow.trigger, HttpListenerTrigger)
        }
    )
    needs_request_config = any(
        isinstance(step, ApiCallStep) for flow in app.flows for step in flow.steps
    )

    lines: list[str] = []
    for index, name in enumerate(listener_names):
        port = _LISTENER_BASE_PORT + index
        lines.append(f'    <http:listener-config name="{name}">')
        lines.append(f'        <http:listener-connection host="{_LISTENER_HOST}" port="{port}"/>')
        lines.append("    </http:listener-config>")

    if needs_request_config:
        lines.append(f'    <http:request-config name="{_HTTP_REQUEST_CONFIG_REF}">')
        lines.append(f'        <http:request-connection host="{_REQUEST_HOST}" port="{_REQUEST_PORT}"/>')
        lines.append("    </http:request-config>")

    return lines


def render_api_yaml(api: API, entry_flow_name: str | None) -> str:
    """Render a minimal API metadata file for one API.

    Deliberately not a full RAML/OAS document (that level of fidelity
    isn't needed for an agent to reason about which API this is, what
    version it's at, and which flow implements it — generating one would
    be exactly the kind of scope creep Milestone 2 was told to avoid).
    """
    lines = [
        f"title: {api.name}",
        f"version: {api.version}",
        f"entryFlow: {entry_flow_name if entry_flow_name is not None else '(unresolved)'}",
    ]
    return "\n".join(lines) + "\n"


def _render_flow(
    flow: Flow, app: Application, api_index: dict, flow_names_by_id: dict[str, str]
) -> tuple[list[str], list[dict]]:
    tag = "flow" if flow.flow_type == "flow" else "sub-flow"
    entities: list[dict] = [{"id": flow.id, "type": "Flow", "name": flow.name, "flow_type": flow.flow_type}]

    lines = [f'    <{tag} name="{flow.name}">']
    if flow.trigger is not None:
        lines.extend(_render_trigger(flow.trigger))
    for step in flow.steps:
        lines.extend(_render_step(step, app, api_index, flow_names_by_id))
        entities.append(_step_entity(step, flow.id, flow_names_by_id))
    lines.append(f"    </{tag}>")

    return lines, entities


def _step_entity(step, flow_id: str, flow_names_by_id: dict[str, str]) -> dict:
    """Build this step's manifest entity dict, including a rendered-visible
    label -- added in Milestone 6.2 so the evaluator can resolve an agent's
    entity_reference down to step/component granularity, per
    Ark_Evaluator_Design.md Section 5.5. This is purely additive to the
    dict manifest.py consumes; it changes no rendered XML/YAML content.

    Per-step-kind label rule (matches the design doc exactly):
    - TransformStep / ApiCallStep / ConnectorStep already have a real
      `.name` field that's already rendered as `doc:name` in the XML --
      reuse it as-is. (ConnectorStep added for Feature 2 — see
      `_render_step`'s ConnectorStep branch.)
    - FlowRefStep has no `.name`; its rendered identity IS its target, so
      the label is derived from the resolved target flow's name. Note:
      deliberately NOT also aliased to the bare target name -- a
      FlowRefStep can only target a Flow in the same Application (a real
      MuleSoft constraint), which means both entities always render into
      the very same artifact file. Aliasing the step to its target's bare
      name would make it permanently, unavoidably ambiguous with the
      target Flow entity itself in every such case, not just an edge
      case -- worse than not having the alias at all. If an agent refers
      to the bare flow name, that should resolve to the Flow (the real
      entity a mutation could land on), not to every step that happens to
      reference it.
    - LoggerStep has no `.name` and the renderer doesn't emit a `doc:name`
      on `<logger>` elements (adding one would change actual rendered XML
      content, not just the manifest -- deliberately avoided). Its message
      text is used as the identifying label instead; a short-form alias is
      added for long messages, since an agent might paraphrase rather than
      quote it verbatim.
    """
    entity: dict = {"id": step.id, "type": f"Step:{step.kind}", "flow_id": flow_id}

    if isinstance(step, (TransformStep, ApiCallStep, ConnectorStep)):
        entity["name"] = step.name
    elif isinstance(step, FlowRefStep):
        target_name = flow_names_by_id.get(step.target_flow_id, step.target_flow_id)
        entity["name"] = f"reference to '{target_name}'"
    elif isinstance(step, LoggerStep):
        entity["name"] = step.message
        if len(step.message) > 40:
            entity["aliases"] = [step.message[:40].rstrip()]

    return entity


def _render_trigger(trigger) -> list[str]:
    if isinstance(trigger, HttpListenerTrigger):
        return [
            f'        <http:listener config-ref="{trigger.listener_config_ref}" '
            f'path="{trigger.path}" allowedMethods="{trigger.method}"/>'
        ]
    if isinstance(trigger, SchedulerTrigger):
        return [
            "        <scheduler>",
            "            <scheduling-strategy>",
            f'                <cron expression="{trigger.cron_expression}"/>',
            "            </scheduling-strategy>",
            "        </scheduler>",
        ]
    raise MuleSoftRenderError(f"Unsupported trigger type: {trigger!r}")


def _render_step(step, app: Application, api_index: dict, flow_names_by_id: dict[str, str]) -> list[str]:
    if isinstance(step, TransformStep):
        return [
            f'        <ee:transform doc:name="{step.name}">',
            "            <ee:message>",
            f"                <ee:set-payload><![CDATA[{step.dataweave}]]></ee:set-payload>",
            "            </ee:message>",
            "        </ee:transform>",
        ]

    if isinstance(step, FlowRefStep):
        target_name = flow_names_by_id.get(step.target_flow_id)
        if target_name is None:
            # Should be unreachable for a ground-truth file that passed
            # validate_ground_truth(), since FlowRefStep targets are
            # checked there. Guarded here anyway so the renderer never
            # silently emits a dangling reference.
            raise MuleSoftRenderError(
                f"FlowRefStep '{step.id}' targets unknown flow id '{step.target_flow_id}' "
                f"in Application '{app.id}'. Did you call validate_ground_truth() first?"
            )
        return [f'        <flow-ref name="{target_name}"/>']

    if isinstance(step, LoggerStep):
        return [f'        <logger level="{step.level}" message="{step.message}"/>']

    if isinstance(step, ApiCallStep):
        target = api_index.get(step.target_api_id)
        if target is None:
            raise MuleSoftRenderError(
                f"ApiCallStep '{step.id}' targets unknown API id '{step.target_api_id}'. "
                f"Did you call validate_ground_truth() first?"
            )
        _, _, entry_flow = target
        if entry_flow is None or not isinstance(entry_flow.trigger, HttpListenerTrigger):
            raise MuleSoftRenderError(
                f"ApiCallStep '{step.id}' targets API '{step.target_api_id}', but its entry "
                f"flow has no http-listener trigger to derive a request path/method from. "
                f"The MuleSoft adapter can only render api-call steps that target an "
                f"HTTP-triggered API — this is a known Milestone 2 limitation, not a bug."
            )
        return [
            f'        <http:request method="{entry_flow.trigger.method}" '
            f'path="{entry_flow.trigger.path}" config-ref="{_HTTP_REQUEST_CONFIG_REF}" '
            f'doc:name="{step.name}"/>'
        ]

    if isinstance(step, ConnectorStep):
        # Rendering-fidelity decision (Feature 2): ApiCallStep is the
        # closest existing analog for "an outbound call to something
        # external" — but ApiCallStep's own rendering (<http:request .../>
        # above) is only valid because that step specifically targets
        # another Ark-modeled, HTTP-triggered API in *this* estate, whose
        # real path/method ground truth actually supplies. A ConnectorStep
        # names an external, real-world enterprise system
        # (`connector_type`, e.g. "sap_retail_scm") that Ark has no
        # ground-truth path/method/schema for at all -- inventing
        # connector-specific XML (e.g. a fabricated `<sap:...>` namespace)
        # would fabricate syntax with no grounding in real
        # docs.mulesoft.com content, unlike every other element this
        # renderer emits. Every real MuleSoft connector (SAP, Salesforce,
        # Database, ...) defines its OWN dedicated XML namespace and
        # operations -- there is no single generic "call any connector"
        # element in real Mule to reuse instead.
        #
        # So this uses only real, generic, already-used-elsewhere Mule
        # syntax: a `<logger>` element (the same core element LoggerStep
        # already renders, and a common real pattern for the
        # request/response trace around a connector invocation), carrying
        # the same `doc:name` labeling convention already used for
        # TransformStep/ApiCallStep, preceded by a plain XML comment
        # explicitly naming the connector_type -- clearly labeled, well-
        # formed, zero invented namespace, and (like every comment in Mule
        # XML) still fully visible to an agent reading the raw artifact
        # text, even though it isn't a distinct executable element.
        return [
            f"        <!-- External connector reference: {step.connector_type} -->",
            f'        <logger level="INFO" doc:name="{step.name}" message="{step.description}"/>',
        ]

    raise MuleSoftRenderError(f"Unsupported step kind: {step!r}")
