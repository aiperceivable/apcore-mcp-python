"""Module-ID handling in the OpenAPI backend since apcore-toolkit 0.13.0.

The toolkit now normalises every ``module_id`` it emits into apcore's
Canonical ID alphabet — after ``base_path_prefix`` and both ID-affecting
hooks — so the bridge registers the scanner's IDs as emitted and keeps only
its skip policy, applied to the ID ``scan`` returns. The shared fixture
(``openapi_backend.json``) pins the hook-free cases in all three languages;
these tests cover what needs a caller hook, which the Rust backend does not
expose.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import replace
from typing import Any

import pytest

pytest.importorskip("apcore_toolkit", reason="the OpenAPI backend needs apcore-mcp[openapi]")

from apcore_mcp.openapi_backend import openapi_backend, project_module_id  # noqa: E402

_OK = {"responses": {"200": {"description": "ok"}}}

_DOC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/pets": {"get": {"operationId": "listPets", **_OK}},
        "/pets/{petId}": {"get": _OK},
    },
}


def _ids(registry: Any) -> list[str]:
    return sorted(registry.list(visibility=["public", "hidden"]))


def _skip_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "OpenAPI operation skipped" in r.getMessage()]


def test_registers_the_ids_the_scanner_emits() -> None:
    # camelCase operationId and camelCase path parameter are both split into
    # words by the toolkit; the bridge's old projection gave `listpets` and
    # `pets.petid.get`.
    assert _ids(openapi_backend(_DOC)) == ["list_pets", "pets.pet_id.get"]


def test_a_hook_returning_a_camelcase_id_is_normalised_not_skipped(caplog: pytest.LogCaptureFixture) -> None:
    """A skip check inside ``transform_module`` would see ``MyThing`` and drop it."""

    def rename(module: Any) -> Any:
        return replace(module, module_id="MyThing") if module.module_id == "list_pets" else module

    with caplog.at_level(logging.WARNING):
        registry = openapi_backend(_DOC, transform_module=rename)

    assert _ids(registry) == ["my_thing", "pets.pet_id.get"]
    assert _skip_records(caplog) == []


def test_the_caller_transform_module_still_runs_and_can_drop(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    def drop_item(module: Any) -> Any | None:
        seen.append(module.module_id)
        return None if module.module_id == "pets.pet_id.get" else module

    with caplog.at_level(logging.WARNING):
        registry = openapi_backend(_DOC, transform_module=drop_item)

    assert seen == ["list_pets", "pets.pet_id.get"]
    assert _ids(registry) == ["list_pets"]
    assert _skip_records(caplog) == [], "a module the caller's hook dropped is not the bridge's skip"


def test_an_id_normalisation_cannot_repair_is_skipped_naming_the_emitted_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hook returns ``Pets.2Fa``; the toolkit emits ``pets.2_fa``; the warning names the latter."""

    def derive(path: str, method: str, operation: dict[str, Any]) -> str | None:
        return "Pets.2Fa" if path == "/pets" else None

    with caplog.at_level(logging.WARNING):
        registry = openapi_backend(_DOC, derive_module_id=derive)

    assert _ids(registry) == ["pets.pet_id.get"]
    skips = _skip_records(caplog)
    assert len(skips) == 1
    assert "'pets.2_fa'" in skips[0] and "'2_fa'" in skips[0]
    assert "Pets.2Fa" not in skips[0]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "a skipped module must not reach the writer"


def test_an_empty_id_from_a_hook_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    def blank(module: Any) -> Any:
        # `-` normalises to the empty ID, which only a hook can produce.
        return replace(module, module_id="-") if module.module_id == "list_pets" else module

    with caplog.at_level(logging.WARNING):
        registry = openapi_backend(_DOC, transform_module=blank)

    assert _ids(registry) == ["pets.pet_id.get"]
    skips = _skip_records(caplog)
    assert len(skips) == 1 and "module ID ''" in skips[0]


def test_a_skipped_module_does_not_count_toward_the_collision_preflight() -> None:
    from apcore import FunctionModule, Registry

    registry = Registry()
    registry.register(
        "keep",
        FunctionModule(
            module_id="keep",
            description="stub",
            func=lambda **_: {},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
    )
    doc = {**_DOC, "paths": {"/v1/2fa": {"post": _OK}, "/pets": {"get": {"operationId": "listPets", **_OK}}}}
    assert _ids(openapi_backend(doc, registry=registry, acknowledge_unapproved_writes=True)) == ["keep", "list_pets"]


def test_project_module_id_is_deprecated_with_its_behaviour_unchanged() -> None:
    with pytest.warns(DeprecationWarning, match=r"apcore-toolkit >= 0\.13\.0"):
        assert project_module_id("listPets") == "listpets"
    with pytest.warns(DeprecationWarning):
        assert project_module_id("pet-store.items.get") == "pet_store.items.get"
    with pytest.warns(DeprecationWarning):
        assert project_module_id("v1.2fa.post") is None
    with pytest.warns(DeprecationWarning):
        assert project_module_id("") is None


def test_openapi_backend_no_longer_calls_project_module_id() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        openapi_backend(_DOC)
    assert not [w for w in caught if "project_module_id" in str(w.message)]
