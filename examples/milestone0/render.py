"""
Hand-written MuleSoft XML renderer — Milestone 0 example ONLY.

This is deliberately NOT a general-purpose adapter. It knows how to render
exactly the one example estate in ground_truth.json (one HTTP-listener
flow, one transform, one flow-ref to one sub-flow with one logger). The
point of Milestone 0 is to prove that a ground-truth estate CAN be
faithfully, testably rendered into a realistic MuleSoft artifact before
Milestone 2 builds the general, template-driven adapter that has to
handle arbitrary estates.

Scope note: the schemaLocation URLs in the output are static boilerplate,
not derived from ground truth — real Mule projects always include them,
so they're included here for realism, but Ark's ground-truth schema has
no notion of "which XSD version" yet. That's an intentional simplification
per the plan's "cap fidelity at what affects agent reasoning" risk note,
not an oversight.
"""

from __future__ import annotations

from ark.core.models import FlowRefStep, GroundTruthEstate, LoggerStep, TransformStep

_SCHEMA_LOCATION = (
    "\n"
    "http://www.mulesoft.org/schema/mule/core http://www.mulesoft.org/schema/mule/core/current/mule.xsd\n"
    "http://www.mulesoft.org/schema/mule/http http://www.mulesoft.org/schema/mule/http/current/mule-http.xsd\n"
    "http://www.mulesoft.org/schema/mule/ee/core http://www.mulesoft.org/schema/mule/ee/core/current/mule-ee.xsd"
)


def render_milestone0_xml(estate: GroundTruthEstate) -> str:
    """Render the one Milestone-0 example estate into MuleSoft-shaped XML.

    Raises NotImplementedError for any step/flow shape outside the one
    example this function was written for — it is not meant to generalize.
    """
    app = estate.applications[0]
    main_flow = next(f for f in app.flows if f.flow_type == "flow")
    sub_flows = [f for f in app.flows if f.flow_type == "sub_flow"]

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
    lines.append("")
    lines.append(f'    <flow name="{main_flow.name}">')

    trigger = main_flow.trigger
    if trigger is None or trigger.type != "http-listener":
        raise NotImplementedError("Milestone 0 renderer only handles an http-listener trigger.")
    lines.append(
        f'        <http:listener config-ref="{trigger.listener_config_ref}" '
        f'path="{trigger.path}" allowedMethods="{trigger.method}"/>'
    )

    for step in main_flow.steps:
        if isinstance(step, TransformStep):
            lines.append(f'        <ee:transform doc:name="{step.name}">')
            lines.append("            <ee:message>")
            lines.append(f"                <ee:set-payload><![CDATA[{step.dataweave}]]></ee:set-payload>")
            lines.append("            </ee:message>")
            lines.append("        </ee:transform>")
        elif isinstance(step, FlowRefStep):
            target = next(f for f in app.flows if f.id == step.target_flow_id)
            lines.append(f'        <flow-ref name="{target.name}"/>')
        else:
            raise NotImplementedError(
                f"Milestone 0 renderer does not handle step kind '{step.kind}' in a main flow."
            )

    lines.append("    </flow>")

    for sub in sub_flows:
        lines.append("")
        lines.append(f'    <sub-flow name="{sub.name}">')
        for step in sub.steps:
            if isinstance(step, LoggerStep):
                lines.append(f'        <logger level="{step.level}" message="{step.message}"/>')
            else:
                raise NotImplementedError(
                    f"Milestone 0 renderer does not handle step kind '{step.kind}' in a sub-flow."
                )
        lines.append("    </sub-flow>")

    lines.append("")
    lines.append("</mule>")
    lines.append("")
    return "\n".join(lines)
