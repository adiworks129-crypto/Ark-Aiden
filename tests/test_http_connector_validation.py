"""
Tests for Feature 1 (Documentation-as-Schema Validation for MuleSoft
Connectors) -- HTTP connector only.

These are unit tests against hand-written XML snippets, per the task's own
scope: no trajectory run, no wiring into ark.generator/ark.mutation/
ark.adapters. One test (near the bottom) does render a real Milestone 1
artifact through the existing adapter to document a real, currently-true
finding this schema work surfaced -- see its own docstring.
"""

from __future__ import annotations

import unittest

from ark.validation.mulesoft_http_connector import (
    DEFAULT_SCHEMA_PATH,
    load_schema,
    validate_http_connector_xml,
)

_XML_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<mule xmlns="http://www.mulesoft.org/schema/mule/core"\n'
    '      xmlns:http="http://www.mulesoft.org/schema/mule/http"\n'
    '      xmlns:oauth="http://www.mulesoft.org/schema/mule/oauth"\n'
    '      xmlns:tls="http://www.mulesoft.org/schema/mule/tls">\n'
)
_XML_FOOTER = "</mule>\n"


def _wrap(body: str) -> str:
    return _XML_HEADER + body + _XML_FOOTER


class TestSchemaFileItself(unittest.TestCase):
    def test_default_schema_file_exists_and_loads(self):
        self.assertTrue(DEFAULT_SCHEMA_PATH.exists())
        schema = load_schema()
        self.assertIn("elements", schema)
        self.assertIn("http:listener-config", schema["elements"])
        self.assertIn("http:request-config", schema["elements"])

    def test_schema_cites_a_real_source_for_every_element(self):
        schema = load_schema()
        for name, rule in schema["elements"].items():
            self.assertIn("source", rule, f"{name} has no cited source")
            self.assertTrue(rule["source"].startswith("https://docs.mulesoft.com/"), name)

    def test_schema_documents_five_authentication_schemes_not_three(self):
        """The task's own draft assumed "Basic / OAuth / OAuth2" (three
        schemes) -- verified against real docs.mulesoft.com content this
        session and corrected: HTTP Connector documents OAuth 2.0 only,
        with two distinct grant types, plus Digest and NTLM alongside
        Basic. Five real schemes, not three, and no generic "OAuth"/OAuth1
        exists to invent."""
        schema = load_schema()
        choice = schema["elements"]["http:authentication"]["required_children_choice"]
        self.assertEqual(
            set(choice),
            {
                "http:basic-authentication",
                "http:digest-authentication",
                "http:ntlm-authentication",
                "oauth:authorization-code-grant-type",
                "oauth:client-credentials-grant-type",
            },
        )


class TestValidDocuments(unittest.TestCase):
    def test_minimal_valid_listener_and_request_with_matching_configs(self):
        xml = _wrap(
            """
            <http:listener-config name="HTTP_Listener_config" basePath="api">
                <http:listener-connection host="0.0.0.0" port="8081"/>
            </http:listener-config>
            <http:request-config name="HTTP_Request_config">
                <http:request-connection host="localhost" port="8082"/>
            </http:request-config>
            <flow name="server">
                <http:listener path="/orders" allowedMethods="GET" config-ref="HTTP_Listener_config"/>
                <http:request method="POST" path="/downstream" config-ref="HTTP_Request_config"/>
                <logger message="not an http element -- must be ignored, not flagged"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertTrue(result.is_valid, result.issues)
        self.assertEqual(result.issues, [])

    def test_valid_with_basic_authentication(self):
        xml = _wrap(
            """
            <http:request-config name="ReqConfig">
                <http:request-connection host="api.example.com" port="443" protocol="HTTPS">
                    <http:authentication>
                        <http:basic-authentication username="user" password="pass" preemptive="true"/>
                    </http:authentication>
                </http:request-connection>
            </http:request-config>
            <flow name="client">
                <http:request path="/user" config-ref="ReqConfig"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertTrue(result.is_valid, result.issues)

    def test_valid_with_oauth2_authorization_code_grant(self):
        xml = _wrap(
            """
            <http:request-config name="ReqConfig">
                <http:request-connection host="api.github.com" port="443">
                    <http:authentication>
                        <oauth:authorization-code-grant-type
                            externalCallbackUrl="http://myapp.example.com:8082/callback"
                            localAuthorizationUrl="http://localhost:8082/login"
                            authorizationUrl="https://github.com/login/oauth/authorize"
                            clientId="CLIENT_ID"
                            clientSecret="CLIENT_SECRET"
                            tokenUrl="https://github.com/login/oauth/access_token"/>
                    </http:authentication>
                </http:request-connection>
            </http:request-config>
            <flow name="client">
                <http:request path="/user" config-ref="ReqConfig"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertTrue(result.is_valid, result.issues)

    def test_valid_with_reconnect_child(self):
        xml = _wrap(
            """
            <http:listener-config name="LisConfig">
                <http:listener-connection host="0.0.0.0" port="8081">
                    <reconnect count="3" frequency="2000"/>
                </http:listener-connection>
            </http:listener-config>
            <flow name="server">
                <http:listener path="/x" config-ref="LisConfig"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertTrue(result.is_valid, result.issues)


class TestInvalidDocuments(unittest.TestCase):
    def test_missing_required_attribute_on_listener_config(self):
        xml = _wrap(
            """
            <http:listener-config basePath="api">
                <http:listener-connection host="0.0.0.0" port="8081"/>
            </http:listener-config>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertFalse(result.is_valid)
        self.assertTrue(any(i.attribute == "name" for i in result.issues))

    def test_unknown_invented_attribute_is_flagged(self):
        xml = _wrap(
            """
            <http:listener-config name="LisConfig">
                <http:listener-connection host="0.0.0.0" port="8081" retries="5"/>
            </http:listener-config>
            <flow name="server">
                <http:listener path="/x" config-ref="LisConfig"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertFalse(result.is_valid)
        self.assertTrue(any(i.attribute == "retries" for i in result.issues))

    def test_config_ref_pointing_at_the_wrong_kind_of_config_is_flagged(self):
        """A listener referencing a request-config's name (or vice versa)
        -- these are two distinct configuration elements per the schema,
        not interchangeable, even though the name happens to resolve."""
        xml = _wrap(
            """
            <http:request-config name="SharedName">
                <http:request-connection host="example.com" port="443"/>
            </http:request-config>
            <flow name="server">
                <http:listener path="/x" config-ref="SharedName"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("distinct configuration elements" in i.message for i in result.issues))

    def test_dangling_config_ref_is_flagged(self):
        xml = _wrap(
            """
            <flow name="server">
                <http:listener path="/x" config-ref="DoesNotExistAnywhere"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("does not resolve to any" in i.message for i in result.issues))

    def test_authentication_with_zero_schemes_is_flagged(self):
        xml = _wrap(
            """
            <http:request-config name="ReqConfig">
                <http:request-connection host="example.com" port="443">
                    <http:authentication/>
                </http:request-connection>
            </http:request-config>
            <flow name="client">
                <http:request path="/x" config-ref="ReqConfig"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("no authentication scheme" in i.message for i in result.issues))

    def test_authentication_with_two_schemes_is_flagged(self):
        xml = _wrap(
            """
            <http:request-config name="ReqConfig">
                <http:request-connection host="example.com" port="443">
                    <http:authentication>
                        <http:basic-authentication username="u" password="p"/>
                        <http:digest-authentication username="u" password="p"/>
                    </http:authentication>
                </http:request-connection>
            </http:request-config>
            <flow name="client">
                <http:request path="/x" config-ref="ReqConfig"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("more than one authentication scheme" in i.message for i in result.issues))

    def test_missing_required_attribute_on_oauth_grant_type(self):
        """tokenUrl omitted -- required per the schema (and the real docs'
        every worked example includes it)."""
        xml = _wrap(
            """
            <http:request-config name="ReqConfig">
                <http:request-connection host="example.com" port="443">
                    <http:authentication>
                        <oauth:client-credentials-grant-type clientId="a" clientSecret="b"/>
                    </http:authentication>
                </http:request-connection>
            </http:request-config>
            <flow name="client">
                <http:request path="/x" config-ref="ReqConfig"/>
            </flow>
            """
        )
        result = validate_http_connector_xml(xml)
        self.assertFalse(result.is_valid)
        self.assertTrue(any(i.attribute == "tokenUrl" for i in result.issues))

    def test_not_well_formed_xml_is_reported_not_raised(self):
        result = validate_http_connector_xml("<mule><http:listener path='/x'></mule>")
        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.issues), 1)
        self.assertIn("well-formed", result.issues[0].message)


class TestAgainstRealArkRenderedOutput(unittest.TestCase):
    """Runs the validator against a real artifact the actual MuleSoft
    adapter renders (no new trajectory run -- this uses the existing
    Milestone 1 estate + existing adapter code, both already part of the
    pipeline). Still not a pipeline-integration test (the validator still
    isn't called from anywhere in ark/adapters -- that remains a separate,
    later task).

    History: when this test file was first written, it asserted the
    OPPOSITE of what it asserts now -- Ark's renderer at the time never
    emitted an http:listener-config/http:request-config global element at
    all, so every real artifact failed this validator's config-ref checks,
    and that was the whole point of the test (a genuine finding, not a
    validator bug). A later, separate session fixed that renderer bug
    (see ark/adapters/mulesoft/renderer.py's `_render_http_connector_configs`
    and its own docstring) without touching this validator or its schema
    at all, per that session's explicit scope. This test was updated
    afterwards to check the now-fixed reality, per its own original
    "should be updated to match" note."""

    def test_real_rendered_artifact_no_longer_has_dangling_config_refs(self):
        from ark.adapters.mulesoft.adapter import MuleSoftAdapter
        from ark.core.validate import validate_ground_truth

        estate = validate_ground_truth("examples/milestone1/ground_truth.json")
        rendered = MuleSoftAdapter().render(estate)

        for path, content in rendered.artifacts.items():
            if not path.endswith(".xml"):
                continue
            with self.subTest(path=path):
                result = validate_http_connector_xml(content)
                config_ref_issues = [i for i in result.issues if i.attribute == "config-ref"]
                self.assertEqual(
                    config_ref_issues, [],
                    "Expected zero config-ref issues now that the renderer fix is in place.",
                )


if __name__ == "__main__":
    unittest.main()
