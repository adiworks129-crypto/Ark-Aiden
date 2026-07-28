"""
Naming templates and the business-domain vocabulary the generator draws
from.

Kept separate from generator.py so future naming styles or vocabulary
domains can be added here without touching estate-construction logic.
Milestone 3 implements exactly one of each (naming_style='kebab-case',
vocabulary_domain='enterprise_default') — see GeneratorConfig for why
unsupported values are rejected rather than silently substituted.

Deliberate design choice: nouns are drawn independently per layer (see
topology.py), so an experience app's name is NOT guaranteed to relate to
the process/system apps it actually calls. This is realistic (real
estates don't always name things thematically) and useful: it means an
agent evaluated against a generated estate can't infer real dependencies
from naming alone and has to trace the actual ApiCallStep/FlowRefStep
edges — a more honest test than one where names give the answer away.
"""

from __future__ import annotations

# A generic enterprise noun list — deliberately not tied to any one
# business scenario (unlike Milestone 1's hand-authored "Order
# Management" estate), since the generator has to work for arbitrary
# configs, not reproduce one narrative.
VOCABULARY: list[str] = [
    "order",
    "customer",
    "inventory",
    "payment",
    "shipment",
    "invoice",
    "catalog",
    "pricing",
    "loyalty",
    "fulfillment",
    "returns",
    "subscription",
    "notification",
    "account",
    "warehouse",
    "vendor",
]

_LAYER_LABELS = {"experience": "Experience", "process": "Process", "system": "System"}


def app_id(layer: str, noun: str) -> str:
    return f"app-{noun}-{layer}"


def app_name(layer: str, noun: str) -> str:
    return f"{noun}-{layer}"


def api_id(layer: str, noun: str) -> str:
    return f"api-{noun}-{layer}-v1"


def api_name(layer: str, noun: str) -> str:
    return f"{noun.title()} {_LAYER_LABELS[layer]} API"


def entry_flow_id(layer: str, noun: str) -> str:
    return f"flow-{noun}-{layer}-main"


def entry_flow_name(layer: str, noun: str) -> str:
    return f"{noun}-{layer}-main-flow"


def secondary_flow_id(layer: str, noun: str) -> str:
    return f"flow-{noun}-{layer}-scheduled"


def secondary_flow_name(layer: str, noun: str) -> str:
    return f"{noun}-{layer}-scheduled-flow"


def subflow_id(layer: str, noun: str, variant: str = "primary") -> str:
    suffix = "sub" if variant == "primary" else "secondary-sub"
    return f"flow-{noun}-{layer}-{suffix}"


def subflow_name(layer: str, noun: str, variant: str = "primary") -> str:
    suffix = "sub-flow" if variant == "primary" else "secondary-sub-flow"
    return f"{noun}-{layer}-{suffix}"


def step_id(layer: str, noun: str, purpose: str) -> str:
    return f"step-{noun}-{layer}-{purpose}"


def entry_path(layer: str, noun: str) -> str:
    if layer == "process":
        return f"/{noun}/process"
    return f"/{noun}/{{id}}"


def entry_method(layer: str) -> str:
    return "POST" if layer == "process" else "GET"


def build_dataweave(layer: str, noun: str) -> str:
    if layer == "process":
        return (
            "%dw 2.0\n"
            "output application/json\n"
            "---\n"
            "{\n"
            f"    {noun}Id: payload.id,\n"
            '    status: "PROCESSED"\n'
            "}"
        )
    return (
        "%dw 2.0\n"
        "output application/json\n"
        "---\n"
        "{\n"
        f"    {noun}Id: attributes.uriParams.id,\n"
        '    status: "OK"\n'
        "}"
    )
