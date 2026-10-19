"""Issue #20: real MCP discovery, preflight and execution share one input contract."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from apcore import Executor, Registry
from apcore.errors import ModuleError
from jsonschema import Draft202012Validator

from apcore_mcp.server.factory import MCPServerFactory
from apcore_mcp.server.router import ExecutionRouter
from tests.conformance_fixtures import load_fixture

_CASES = load_fixture("mcp_input_contract.json")["schema_cases"]


class Echo:
    description = "Echo validated inputs and preserve an omitted limit's default"
    output_schema = {"type": "object"}

    def __init__(self, schema):
        self.input_schema = deepcopy(schema)

    async def execute(self, inputs, context=None):
        return {"received": inputs, "limit": inputs.get("limit", 7)}


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("validate_inputs", [False, True])
async def test_mcp_input_contract(case, validate_inputs):
    schema = deepcopy(case["input_schema"])
    registry = Registry()
    registry.register("demo.echo", Echo(schema))
    executor = Executor.from_registry(registry)
    factory = MCPServerFactory()
    tool = factory.build_tools(registry)[0]
    expected = deepcopy(schema) or {"properties": {}}
    expected["type"] = "object"
    assert tool.inputSchema == expected
    descriptor = registry.get_definition("demo.echo")
    for strict in [False, True]:
        assert factory.build_tool(descriptor, registry=registry, strict=strict).inputSchema == expected
    assert schema == case["input_schema"]
    router = ExecutionRouter(executor, validate_inputs=validate_inputs)
    validator = Draft202012Validator(tool.inputSchema)
    for sample in case["samples"]:
        arguments = deepcopy(sample["arguments"])
        assert validator.is_valid(arguments) is sample["valid"], sample["id"]
        assert executor.validate("demo.echo", arguments).valid is sample["valid"], sample["id"]
        if sample["valid"]:
            expected_output = await executor.call_async("demo.echo", deepcopy(arguments))
        else:
            with pytest.raises(ModuleError):
                await executor.call_async("demo.echo", deepcopy(arguments))
        content, is_error, _ = await router.handle_call("demo.echo", arguments)
        assert is_error is not sample["valid"], (sample["id"], content)
        if sample["valid"]:
            assert json.loads(content[0]["text"]) == expected_output
            assert json.loads(content[0]["text"])["received"] == sample["arguments"]
            if "limit" in arguments:
                assert json.loads(content[0]["text"])["limit"] == arguments["limit"]
        assert arguments == sample["arguments"]
    for property_schema in tool.inputSchema.get("properties", {}).values():
        property_schema["description"] = "Changed on the advertised copy"
        break
    tool.inputSchema["properties"] = {"changed": {"type": "string"}}
    assert registry.get_definition("demo.echo").input_schema == schema


@pytest.mark.parametrize("schema", [[], ["invalid"], True, 42])
def test_mcp_rejects_malformed_schema(schema):
    from types import SimpleNamespace

    descriptor = SimpleNamespace(module_id="demo.echo", input_schema=schema)
    with pytest.raises(ValueError, match="MCP input schema must be an object"):
        MCPServerFactory().build_tool(descriptor)


@pytest.mark.parametrize(
    "case", load_fixture("mcp_input_contract.json")["root_schema_cases"], ids=lambda case: case["id"]
)
def test_mcp_root_schema_contract(case: dict[str, Any]) -> None:
    schema = deepcopy(case["input_schema"])
    registry = Registry()
    registry.register("demo.echo", Echo(schema))
    descriptor = registry.get_definition("demo.echo")
    factory = MCPServerFactory()
    if case["accepted"]:
        tool = factory.build_tool(descriptor)
        assert tool.inputSchema["type"] == "object"
        assert len(factory.build_tools(registry)) == 1
        assert Draft202012Validator(schema).is_valid({})
        assert Draft202012Validator(tool.inputSchema).is_valid({})
        assert Executor.from_registry(registry).validate("demo.echo", {}).valid
    else:
        with pytest.raises(ValueError, match="MCP input schema must allow an object root"):
            factory.build_tool(descriptor)
        assert factory.build_tools(registry) == []
    assert descriptor.input_schema == schema
