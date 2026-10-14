"""Structured MCP output contract and protocol boundary regressions."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from mcp import types
from mcp.server.lowlevel.server import RequestContext, request_ctx
from starlette.requests import Request

from apcore_mcp.server.factory import MCPServerFactory
from apcore_mcp.server.router import ExecutionRouter
from tests.conftest import ModuleDescriptor


@contextmanager
def request_context(version="2025-06-18", http_version=None, *, http=False, forged_version=None):
    session = SimpleNamespace(client_params=SimpleNamespace(protocolVersion=version))
    headers = [] if http_version is None else [(b"mcp-protocol-version", http_version.encode())]
    request = Request({"type": "http", "headers": headers}) if http else None
    meta = types.RequestParams.Meta(mcpProtocolVersion=forged_version) if forged_version else None
    token = request_ctx.set(
        RequestContext(request_id=1, meta=meta, session=session, lifespan_context={}, request=request)
    )
    try:
        yield
    finally:
        request_ctx.reset(token)


def descriptor(schema, *, asynchronous=False):
    return ModuleDescriptor(
        module_id="test.output",
        description="Test output",
        input_schema={"type": "object"},
        output_schema=schema,
        metadata={"async": asynchronous},
    )


def server_for(result, schema, *, error=False):
    factory = MCPServerFactory()
    server = factory.create_server()
    tool = factory.build_tool(descriptor(schema))

    class Router:
        async def handle_call(self, name, arguments, extra):
            return [{"type": "text", "text": result, "_meta": {"traceparent": "safe-trace"}}], error, "trace"

    factory.register_handlers(server, [tool], Router())
    return server


async def list_tools(server):
    return (await server.request_handlers[types.ListToolsRequest](None)).root


async def call_tool(server):
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="test.output", arguments={}))
    return (await server.request_handlers[types.CallToolRequest](request)).root


def test_build_tool_advertises_exact_object_schema_with_references():
    schema = {
        "type": "object",
        "$defs": {"count": {"type": "integer"}},
        "properties": {"count": {"$ref": "#/$defs/count"}},
    }
    tool = MCPServerFactory().build_tool(descriptor(schema))
    assert tool.outputSchema == schema


@pytest.mark.parametrize("schema", [{}, {"type": "array"}, {"type": "string"}])
def test_build_tool_omits_non_object_schema(schema):
    assert MCPServerFactory().build_tool(descriptor(schema)).outputSchema is None


def test_build_tool_omits_final_schema_for_async_submission():
    assert MCPServerFactory().build_tool(descriptor({"type": "object"}, asynchronous=True)).outputSchema is None


@pytest.mark.parametrize("version", ["2025-06-18", "2025-11-25", "unknown-version"])
async def test_modern_protocol_preserves_text_and_returns_matching_object(version):
    text = '{"count": 2}'
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}
    server = server_for(text, schema)
    with request_context(version):
        assert (await list_tools(server)).tools[0].outputSchema == schema
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == json.loads(result.content[0].text) == {"count": 2}
    assert result.content[0].text == text
    assert result.content[0].meta == {"traceparent": "safe-trace"}


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26"])
async def test_legacy_protocol_omits_both_structured_fields(version):
    server = server_for('{"count":2}', {"type": "object", "required": ["absent"]})
    with request_context(version, forged_version="2025-06-18"):
        tool = (await list_tools(server)).tools[0]
        result = await call_tool(server)
    assert "outputSchema" not in tool.model_dump(exclude_none=True)
    assert "structuredContent" not in result.model_dump(exclude_none=True)
    assert not result.isError
    assert json.loads(result.content[0].text) == {"count": 2}


@pytest.mark.parametrize(
    "header,structured", [("2025-06-18", True), ("2025-11-25", True), ("2025-03-26", False), (None, False)]
)
async def test_http_uses_request_header_instead_of_shared_session_or_forged_meta(header, structured):
    server = server_for('{"count":2}', {"type": "object"})
    with request_context("2025-06-18", header, http=True, forged_version="2025-06-18"):
        tool = (await list_tools(server)).tools[0]
        result = await call_tool(server)
    assert (tool.outputSchema is not None) is structured
    assert (result.structuredContent is not None) is structured


@pytest.mark.parametrize("text", ["[1,2]", "42", "null", "custom formatted output"])
async def test_values_without_object_schema_remain_text_only(text):
    server = server_for(text, {})
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent is None
    assert result.content[0].text == text


@pytest.mark.parametrize("text", ['{"secret":"private-canary"}', '["private-canary"]', "private-canary"])
async def test_schema_mismatch_returns_payload_free_error(text):
    server = server_for(
        text, {"type": "object", "properties": {"secret": {"const": "expected"}}, "required": ["secret"]}
    )
    with request_context():
        result = await call_tool(server)
    assert result.isError
    assert result.structuredContent is None
    assert "private-canary" not in result.model_dump_json()
    assert "after redaction" in result.content[0].text


async def test_existing_errors_have_no_structured_output():
    server = server_for('{"code":"DENIED"}', {"type": "object"}, error=True)
    with request_context():
        result = await call_tool(server)
    assert result.isError
    assert result.structuredContent is None
    assert result.content[0].text == '{"code":"DENIED"}'


@pytest.mark.parametrize("require_original_secret", [False, True])
async def test_router_redaction_precedes_structured_projection_and_schema_validation(require_original_secret):
    schema = {
        "type": "object",
        "properties": {"secret": {"type": "string", "x-sensitive": True}, "count": {"type": "integer"}},
    }
    if require_original_secret:
        schema["properties"]["secret"]["const"] = "private-canary"

    class Executor:
        async def call_async(self, module_id, inputs):
            return {"secret": "private-canary", "count": 2}

    factory = MCPServerFactory()
    server = factory.create_server()
    router = ExecutionRouter(Executor(), output_schema_map={"test.output": schema})
    factory.register_handlers(server, [factory.build_tool(descriptor(schema))], router)
    with request_context():
        result = await call_tool(server)
    assert "private-canary" not in result.model_dump_json()
    assert result.isError is require_original_secret
    if not require_original_secret:
        assert result.structuredContent == json.loads(result.content[0].text)
        assert result.structuredContent["count"] == 2


@pytest.mark.parametrize(
    "version,structured", [("2024-11-05", False), ("2025-03-26", False), ("2025-06-18", True), ("2025-11-25", True)]
)
async def test_wire_initialize_list_and_call_use_negotiated_session_version(version, structured):
    import anyio
    from mcp.shared.message import SessionMessage

    server = server_for('{"count":2}', {"type": "object"})
    factory = MCPServerFactory()
    send_client, read_server = anyio.create_memory_object_stream(10)
    send_server, read_client = anyio.create_memory_object_stream(10)

    async def send(method, params, identifier):
        message = types.JSONRPCMessage(types.JSONRPCRequest(jsonrpc="2.0", id=identifier, method=method, params=params))
        await send_client.send(SessionMessage(message))
        return (await read_client.receive()).message.root.result

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(server.run, read_server, send_server, factory.build_init_options(server, "test", "1"))
            initialized = await send(
                "initialize",
                {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                1,
            )
            assert initialized["protocolVersion"] == version
            tools = await send("tools/list", {}, 2)
            assert ("outputSchema" in tools["tools"][0]) is structured
            result = await send("tools/call", {"name": "test.output", "arguments": {}}, 3)
            assert ("structuredContent" in result) is structured
            assert result["isError"] is False
            if structured:
                assert result["structuredContent"] == json.loads(result["content"][0]["text"])
            group.cancel_scope.cancel()


async def test_sse_uses_negotiated_session_even_when_http_post_has_no_version_header():
    from apcore_mcp.server.transport import _protocol_source_var

    token = _protocol_source_var.set("session")
    try:
        server = server_for('{"count":2}', {"type": "object"})
        with request_context("2025-06-18", http=True):
            assert (await list_tools(server)).tools[0].outputSchema is not None
            assert (await call_tool(server)).structuredContent == {"count": 2}
    finally:
        _protocol_source_var.reset(token)


async def test_empty_success_with_advertised_schema_returns_payload_free_error():
    from apcore_mcp.server.factory import _build_call_result

    with request_context():
        result = _build_call_result([], False, {"type": "object"})
    assert result.isError
    assert result.structuredContent is None


async def test_trace_mode_preserves_extra_text_and_primary_json_object():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}

    class Executor:
        async def call_async(self, module_id, inputs):
            return {"count": 2}

        async def call_async_with_trace(self, module_id, inputs):
            return {"count": 2}, SimpleNamespace(strategy_name="direct", total_duration_ms=1.0, steps=[])

    factory = MCPServerFactory()
    server = factory.create_server()
    router = ExecutionRouter(Executor(), output_schema_map={"test.output": schema}, trace=True)
    factory.register_handlers(server, [factory.build_tool(descriptor(schema))], router)
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == json.loads(result.content[0].text) == {"count": 2}
    assert len(result.content) == 2
    assert json.loads(result.content[1].text)["strategy_name"] == "direct"
    assert result.content[0].meta["traceparent"].startswith("00-")


@pytest.mark.parametrize("streamable_http,expected_source", [(False, "session"), (True, "http")])
async def test_transport_protocol_source_is_owned_by_transport_and_resets_after_run(streamable_http, expected_source):
    from apcore_mcp.server.transport import TransportManager, _protocol_source_var

    class Server:
        async def run(self, read_stream, write_stream, init_options):
            assert _protocol_source_var.get() == expected_source

    previous = _protocol_source_var.get()
    await TransportManager()._run_scoped(Server(), None, None, None, "test-session", streamable_http)
    assert _protocol_source_var.get() == previous


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("schema", [{}, {"type": "object"}])
async def test_non_finite_json_constants_do_not_produce_inconsistent_structured_content(constant, schema):
    text = '{"value":' + constant + "}"
    server = server_for(text, schema)
    with request_context():
        result = await call_tool(server)
    assert result.structuredContent is None
    assert result.isError is bool(schema)
    if not schema:
        assert result.content[0].text == text


@pytest.mark.parametrize("text", ['{"number":1e400}', '{"nested":{"number":-1e400}}', '{"values":[{"number":1e400}]}'])
@pytest.mark.parametrize("schema", [{}, {"type": "object"}])
async def test_exponent_overflow_never_produces_non_finite_structured_numbers(text, schema):
    server = server_for(text, schema)
    with request_context():
        result = await call_tool(server)
    assert result.structuredContent is None
    assert result.isError is bool(schema)
    if not schema:
        assert result.content[0].text == text
    else:
        assert "1e400" not in result.model_dump_json()


def test_build_tool_output_schema_is_an_independent_descriptor_snapshot():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "$defs": {"nested": {"const": [1, 2]}}}
    module = descriptor(schema)
    tool = MCPServerFactory().build_tool(module)
    module.output_schema["properties"]["count"]["type"] = "string"
    module.output_schema["$defs"]["nested"]["const"].append(3)
    assert tool.outputSchema["properties"]["count"]["type"] == "integer"
    assert tool.outputSchema["$defs"]["nested"]["const"] == [1, 2]
    tool.outputSchema["properties"]["count"]["type"] = "number"
    assert module.output_schema["properties"]["count"]["type"] == "string"


async def test_unresolved_remote_output_reference_never_fetches_and_fails_without_payload():
    from unittest.mock import patch

    schema = {"type": "object", "properties": {"count": {"$ref": "http://example.test/private-schema"}}}
    server = server_for('{"count":"private-canary"}', schema)
    with patch("urllib.request.urlopen") as fetch, request_context():
        result = await call_tool(server)
    fetch.assert_not_called()
    assert result.isError
    assert result.structuredContent is None
    assert "private-canary" not in result.model_dump_json()
    assert "example.test" not in result.model_dump_json()


@pytest.mark.parametrize("reference", ["#/$defs/count", "#count", "http://example.test/schema#count"])
@pytest.mark.parametrize("valid", [True, False])
async def test_local_output_references_anchors_and_self_ids_validate_without_fetching(reference, valid):
    from unittest.mock import patch

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "http://example.test/schema",
        "type": "object",
        "$defs": {"count": {"$anchor": "count", "type": "integer"}},
        "properties": {"count": {"$ref": reference}},
    }
    server = server_for('{"count":1}' if valid else '{"count":"private-canary"}', schema)
    with patch("urllib.request.urlopen") as fetch, request_context():
        result = await call_tool(server)
    fetch.assert_not_called()
    assert result.isError is not valid
    if valid:
        assert result.structuredContent == {"count": 1}
    else:
        assert "private-canary" not in result.model_dump_json()


async def test_draft_2020_output_reference_preserves_sibling_constraints():
    schema = {
        "type": "object",
        "$defs": {"count": {"type": "number"}},
        "properties": {"count": {"$ref": "#/$defs/count", "maximum": 5}},
    }
    server = server_for('{"count":100}', schema)
    with request_context():
        result = await call_tool(server)
    assert result.isError
    assert result.structuredContent is None


async def test_output_validation_honors_declared_draft_7_reference_semantics():
    # Draft 7 ignores siblings beside $ref, whereas Draft 2020-12 asserts them.
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "definitions": {"count": {"type": "number"}},
        "properties": {"count": {"$ref": "#/definitions/count", "maximum": 5}},
    }
    server = server_for('{"count":100}', schema)
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == {"count": 100}


@pytest.mark.parametrize("version", ["2025-03-26", "2025-06-18"])
@pytest.mark.parametrize("requires_original_value", [False, True])
async def test_governed_router_redacts_local_reference_before_text_and_structured_wire_output(
    version, requires_original_value
):
    import anyio
    from mcp.shared.message import SessionMessage

    field = {"type": "string", "x-sensitive": True}
    if requires_original_value:
        field["const"] = "private-canary"
    schema = {
        "type": "object",
        "$defs": {"Payload": {"type": "object", "properties": {"value": field}}},
        "properties": {"payload": {"$ref": "#/$defs/Payload"}},
    }

    class Executor:
        async def call_async(self, module_id, inputs):
            return {"payload": {"value": "private-canary", "public": 1}}

    factory = MCPServerFactory()
    server = factory.create_server()
    factory.register_handlers(
        server,
        [factory.build_tool(descriptor(schema))],
        ExecutionRouter(Executor(), output_schema_map={"test.output": schema}),
    )
    send_client, read_server = anyio.create_memory_object_stream(10)
    send_server, read_client = anyio.create_memory_object_stream(10)

    async def send(method, params, identifier):
        await send_client.send(
            SessionMessage(
                types.JSONRPCMessage(types.JSONRPCRequest(jsonrpc="2.0", id=identifier, method=method, params=params))
            )
        )
        return (await read_client.receive()).message.root.result

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(server.run, read_server, send_server, factory.build_init_options(server, "test", "1"))
            await send(
                "initialize",
                {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                1,
            )
            result = await send("tools/call", {"name": "test.output", "arguments": {}}, 2)
            assert "private-canary" not in json.dumps(result)
            assert result["isError"] is (requires_original_value and version == "2025-06-18")
            if not result["isError"]:
                text = json.loads(result["content"][0]["text"])
                assert text["payload"]["value"] == "***REDACTED***"
                assert text["payload"]["public"] == 1
                if version == "2025-06-18":
                    assert result["structuredContent"] == text
            group.cancel_scope.cancel()


@pytest.mark.parametrize("reference", ["https://example.test/private", "file:///tmp/private-schema", "#/$defs/missing"])
@pytest.mark.parametrize("location", ["inactive", "unused"])
async def test_output_schema_references_resolve_even_in_inactive_branches_or_unused_definitions(reference, location):
    from unittest.mock import patch

    schema = {"type": "object"}
    if location == "inactive":
        schema.update(
            {
                "if": {"properties": {"enabled": {"const": True}}, "required": ["enabled"]},
                "then": {"$ref": reference},
                "else": {},
            }
        )
    else:
        schema["$defs"] = {"Unused": {"$ref": reference}}
    server = server_for('{"enabled":false}', schema)
    with patch("urllib.request.urlopen") as fetch, request_context():
        result = await call_tool(server)
    fetch.assert_not_called()
    assert result.isError
    assert result.structuredContent is None
    assert "example.test" not in result.model_dump_json()


@pytest.mark.parametrize("keyword", ["const", "default", "examples"])
async def test_output_schema_literal_reference_strings_do_not_trigger_schema_resolution(keyword):
    literal = {"$ref": "https://example.test/private"}
    schema = {"type": "object", keyword: [literal] if keyword == "examples" else literal}
    text = json.dumps(literal) if keyword == "const" else '{"enabled":false}'
    server = server_for(text, schema)
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == json.loads(text)


async def test_output_schema_recursive_references_are_checked_without_expansion():
    schema = {
        "type": "object",
        "$defs": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}}},
        "properties": {"head": {"$ref": "#/$defs/Node"}},
    }
    server = server_for('{"head":{"next":{"next":{}}}}', schema)
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == {"head": {"next": {"next": {}}}}


async def test_declared_unknown_output_dialect_returns_payload_free_error_without_fallback_warning():
    import warnings

    schema = {"$schema": "https://example.test/unknown-dialect", "type": "object"}
    server = server_for("{}", schema)
    with warnings.catch_warnings(record=True) as captured, request_context():
        warnings.simplefilter("always")
        result = await call_tool(server)
    assert result.isError
    assert result.structuredContent is None
    assert "example.test" not in result.model_dump_json()
    assert not captured


@pytest.mark.parametrize("dialect", ["2020", "draft7"])
async def test_governed_router_redacts_positional_array_references_in_actual_wire_results(dialect):
    import anyio
    from mcp.shared.message import SessionMessage

    positional = {"$ref": "#/definitions/Sensitive"}
    shape = (
        {"prefixItems": [positional], "items": {"type": "string"}}
        if dialect == "2020"
        else {"items": [positional], "additionalItems": {"type": "string"}}
    )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema"
        if dialect == "2020"
        else "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "definitions": {"Sensitive": {"type": "string", "x-sensitive": True}},
        "properties": {"rows": {"type": "array", "items": {"type": "array", **shape}}},
    }

    class Executor:
        async def call_async(self, module_id, inputs):
            return {"rows": [["private-canary", "public"]]}

    factory = MCPServerFactory()
    server = factory.create_server()
    factory.register_handlers(
        server,
        [factory.build_tool(descriptor(schema))],
        ExecutionRouter(Executor(), output_schema_map={"test.output": schema}),
    )
    send_client, read_server = anyio.create_memory_object_stream(10)
    send_server, read_client = anyio.create_memory_object_stream(10)

    async def send(method, params, identifier):
        await send_client.send(
            SessionMessage(
                types.JSONRPCMessage(types.JSONRPCRequest(jsonrpc="2.0", id=identifier, method=method, params=params))
            )
        )
        return (await read_client.receive()).message.root.result

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(server.run, read_server, send_server, factory.build_init_options(server, "test", "1"))
            await send(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                1,
            )
            result = await send("tools/call", {"name": "test.output", "arguments": {}}, 2)
            assert not result["isError"]
            assert "private-canary" not in json.dumps(result)
            assert (
                result["structuredContent"]
                == json.loads(result["content"][0]["text"])
                == {"rows": [["***REDACTED***", "public"]]}
            )
            group.cancel_scope.cancel()


async def test_mutable_exposed_tool_schema_replacement_keeps_wire_discovery_and_validation_consistent():
    import anyio
    from mcp.shared.message import SessionMessage

    original_schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    module = descriptor(original_schema)
    factory = MCPServerFactory()
    tool = factory.build_tool(module)
    server = factory.create_server()

    class Router:
        async def handle_call(self, name, arguments, extra):
            return [{"type": "text", "text": '{"count":"text"}'}], False, None

    factory.register_handlers(server, [tool], Router())
    send_client, read_server = anyio.create_memory_object_stream(10)
    send_server, read_client = anyio.create_memory_object_stream(10)

    async def send(method, params, identifier):
        await send_client.send(
            SessionMessage(
                types.JSONRPCMessage(types.JSONRPCRequest(jsonrpc="2.0", id=identifier, method=method, params=params))
            )
        )
        return (await read_client.receive()).message.root.result

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(server.run, read_server, send_server, factory.build_init_options(server, "test", "1"))
            await send(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                1,
            )
            assert (await send("tools/call", {"name": "test.output", "arguments": {}}, 2))["isError"]
            tool.outputSchema = {"type": "object", "properties": {"count": {"type": "string"}}}
            listed = await send("tools/list", {}, 3)
            assert listed["tools"][0]["outputSchema"] == tool.outputSchema
            result = await send("tools/call", {"name": "test.output", "arguments": {}}, 4)
            assert not result["isError"]
            assert result["structuredContent"] == json.loads(result["content"][0]["text"]) == {"count": "text"}
            assert module.output_schema == original_schema
            group.cancel_scope.cancel()


@pytest.mark.parametrize(
    "dialect,keyword", [(None, "$dynamicRef"), ("https://json-schema.org/draft/2019-09/schema", "$recursiveRef")]
)
async def test_unused_supported_dialect_reference_never_fetches_external_schema(dialect, keyword):
    from unittest.mock import patch

    schema = {"type": "object", "$defs": {"Unused": {keyword: "https://example.test/private"}}}
    if dialect:
        schema["$schema"] = dialect
    server = server_for("{}", schema)
    with patch("urllib.request.urlopen") as fetch, request_context():
        result = await call_tool(server)
    fetch.assert_not_called()
    assert result.isError
    assert result.structuredContent is None
    assert "example.test" not in result.model_dump_json()


async def test_structured_schema_rejects_embedded_different_draft_resource():
    schema = {
        "type": "object",
        "$defs": {"Legacy": {"$schema": "http://json-schema.org/draft-07/schema#", "$id": "legacy", "type": "object"}},
        "properties": {"payload": {"$ref": "legacy"}},
    }
    server = server_for('{"payload":{}}', schema)
    with request_context():
        result = await call_tool(server)
    assert result.isError
    assert result.structuredContent is None


@pytest.mark.parametrize(
    "dialect", ["http://json-schema.org/draft-04/schema#", "http://json-schema.org/draft-06/schema#"]
)
async def test_structured_schema_rejects_declared_drafts_outside_common_supported_set(dialect):
    server = server_for("{}", {"$schema": dialect, "type": "object"})
    with request_context():
        result = await call_tool(server)
    assert result.isError
    assert result.structuredContent is None


async def test_structured_schema_accepts_equivalent_repeated_draft_and_ignores_literal_declarations():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema#",
        "type": "object",
        "$defs": {"Payload": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}},
        "properties": {"payload": {"$ref": "#/$defs/Payload"}},
        "default": {"$schema": "http://json-schema.org/draft-07/schema#"},
    }
    server = server_for('{"payload":{}}', schema)
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == {"payload": {}}


@pytest.mark.parametrize("raw", ["0.1234567890123456789012345", "1e-400"])
@pytest.mark.parametrize("schema", [{}, {"type": "object"}])
async def test_structured_numeric_parse_rejects_rounding_and_underflow_without_changing_text(raw, schema):
    text = '{"number":' + raw + "}"
    server = server_for(text, schema)
    with request_context():
        result = await call_tool(server)
    assert result.structuredContent is None
    assert result.isError is bool(schema)
    if not schema:
        assert result.content[0].text == text


@pytest.mark.parametrize("raw", ["1.000e2", "1.23000e-2", "5e-324", "-0.0"])
async def test_structured_numeric_parse_accepts_scientific_equivalents_and_native_float_json(raw):
    server = server_for('{"number":' + raw + "}", {"type": "object"})
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == json.loads(result.content[0].text)


async def test_structured_numeric_parse_preserves_native_arbitrary_precision_integer():
    number = 10**40 + 1
    server = server_for(json.dumps({"number": number}), {"type": "object"})
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent["number"] == number


@pytest.mark.parametrize("schema", [{}, {"type": "object"}])
async def test_structured_json_parse_depth_limit_returns_controlled_result(schema):
    text = '{"nested":' + "[" * 10000 + "0" + "]" * 10000 + "}"
    server = server_for(text, schema)
    with request_context():
        result = await call_tool(server)
    assert result.structuredContent is None
    assert result.isError is bool(schema)
    if not schema:
        assert result.content[0].text == text
    else:
        assert "nested" not in result.content[0].text


@pytest.mark.parametrize("reverse_definitions", [False, True])
async def test_referenced_target_inherits_root_draft_independent_of_definition_order(reverse_definitions):
    target = {"type": "object", "$defs": {"opaque": {"$ref": "https://example.test/private"}}}
    definitions = {"Target": target, "Alias": {"$ref": "#/definitions/Target"}}
    if reverse_definitions:
        definitions = dict(reversed(list(definitions.items())))
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "definitions": definitions,
        "properties": {"payload": {"$ref": "#/definitions/Alias"}},
    }
    server = server_for('{"payload":{}}', schema)
    with request_context():
        result = await call_tool(server)
    assert not result.isError
    assert result.structuredContent == {"payload": {}}
