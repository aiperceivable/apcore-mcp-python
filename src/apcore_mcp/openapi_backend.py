"""OpenAPI backend — serve an OpenAPI 3.0/3.1 document as MCP tools.

Composes apcore-toolkit's shipped pieces into a populated ``Registry``::

    load_spec -> OpenAPIScanner.scan -> HTTPProxyRegistryWriter.write -> Registry

and hands it to the machinery apcore-mcp already has. No scanning logic, no
schema conversion and no new execution path live here.

See ``apcore-mcp/docs/features/openapi-backend.md`` for the specification and
``conformance/fixtures/openapi_backend.json`` for the shared contract.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "MODULE_ID_SEGMENT",
    "build_openapi_backend_from_config",
    "openapi_backend",
    "project_module_id",
    "resolve_spec_location",
]

#: One dot-separated segment of an apcore-legal module ID. apcore's registry
#: enforces ``^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`` at ``Registry.register``
#: and again at ``Executor.call``; this is that pattern, per segment.
MODULE_ID_SEGMENT = re.compile(r"^[a-z][a-z0-9_]*$")

#: Methods that write. Used only to decide whether the "nothing will ask for
#: approval" startup warning applies (FR-OPENAPI-005).
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_URL_SCHEMES = ("http://", "https://")


def project_module_id(module_id: str) -> str | None:
    """Map a scanner-derived module ID into apcore's legal alphabet, or None.

    apcore-toolkit's ``derive_module_id`` sanitizes to ``[A-Za-z0-9_.-]``.
    apcore's registry accepts only lowercase, digits, underscores and dots —
    "no hyphens" — so the two alphabets differ and the scanner's output is not
    directly registrable. Measured against apcore 0.30.0 and apcore-toolkit
    0.11.1, only two of nine realistic operation shapes register unrepaired,
    and the canonical Swagger Petstore (``listPets``, ``createPets``,
    ``showPetById``) is entirely in the rejected set: it scans cleanly, fails
    registration on every operation as a per-module ``WriteResult``, and yields
    an **empty registry**.

    The projection is lowercase, then ``-`` -> ``_``. Both are mechanical and
    lossless up to case. It deliberately stops there: a segment that still does
    not begin with a lowercase letter (``/v1/2fa`` -> ``v1.2fa.post``) can only
    be repaired by *inventing* a character, which is a naming decision that
    belongs to the operator's own ``derive_module_id`` / ``transform_module``
    hook rather than to a silent default.

    Returns:
        The projected ID, or None when it cannot be made legal.
    """
    candidate = module_id.lower().replace("-", "_")
    if not candidate:
        return None
    if not all(MODULE_ID_SEGMENT.match(segment) for segment in candidate.split(".")):
        return None
    return candidate


def resolve_spec_location(
    spec: Any,
    *,
    project_root: str | None = None,
) -> Any:
    """Resolve the ``mcp.openapi.spec`` value (FR-OPENAPI-007).

    ``spec`` is the ``mcp`` namespace's first path-typed configuration key, and
    apcore 0.30.0's protections for path-typed keys do **not** reach it:
    ``Config.path_typed_keys()`` returns a hardcoded tuple of apcore's own four
    keys and never consults a namespace registered through
    ``Config.register_namespace``, and the PROTOCOL_SPEC §9.2.1 requirement-5
    empty-value discard is gated on that same fixed set. So the three rules are
    the bridge's own:

    1. an ``http(s)://`` value is a URL, used **verbatim** — never resolved,
       never made absolute. The discriminator is the scheme prefix, not an
       inference from the string's shape;
    2. a set-but-empty value is discarded (the caller falls through to the next
       configuration tier) — ``""`` is a legal relative path to every
       filesystem API and never the one an operator meant;
    3. a relative filesystem path resolves against ``Config.project_root``
       (§9.2.2's *target* semantics, adopted immediately because this key has
       never shipped and so owes no deprecation window).

    Returns:
        The URL unchanged, an absolute path, or None when the value was empty
        and the caller should fall through.
    """
    if spec is None:
        return None
    if not isinstance(spec, str | Path):
        # An already-parsed document. Nothing to resolve.
        return spec

    text = str(spec)
    if text.strip() == "":
        logger.warning(
            "mcp.openapi.spec is set but empty; it is path-typed and an empty string is not a "
            "path (mirrors PROTOCOL_SPEC §9.2.1 requirement 5). Ignoring the value."
        )
        return None

    if text.startswith(_URL_SCHEMES):
        return text

    path = Path(text)
    if path.is_absolute():
        return str(path)
    base = Path(project_root) if project_root else Path.cwd()
    return str((base / path).resolve())


def _resolve_project_root(explicit: str | None) -> str | None:
    """Read ``Config.project_root`` (apcore 0.30.0), or fall back to CWD."""
    if explicit:
        return explicit
    try:
        from apcore.config import Config

        config = Config.get_instance() if hasattr(Config, "get_instance") else None
        root = getattr(config, "project_root", None) if config is not None else None
        if isinstance(root, str) and root:
            return root
    except Exception:
        logger.debug("Config.project_root unavailable; falling back to CWD", exc_info=True)
    return None


def _require_toolkit() -> Any:
    """Import apcore-toolkit, or fail with the actionable install message.

    apcore-toolkit is an *extra* in Python and an unconditional dependency in
    TypeScript and Rust, so this path exists only here. Unlike the Markdown
    helpers it is not a graceful degradation — there is no fallback for "serve
    this API" — so it raises rather than returning None.
    """
    try:
        import apcore_toolkit
    except ImportError as exc:  # pragma: no cover - exercised by a dedicated test
        raise RuntimeError(
            "The OpenAPI backend requires apcore-toolkit. Install it with: " "pip install 'apcore-mcp[openapi]'"
        ) from exc
    return apcore_toolkit


def openapi_backend(
    spec: Any,
    *,
    base_url: str | None = None,
    prefix: str | None = None,
    include: str | None = None,
    exclude: str | None = None,
    include_deprecated: bool = True,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    auth_header_factory: Callable[[], dict[str, str]] | None = None,
    registry: Any | None = None,
    has_other_backend_source: bool = False,
    project_root: str | None = None,
    acknowledge_unapproved_writes: bool = False,
    transform_operation: Callable[[str, str, dict[str, Any]], dict[str, Any] | None] | None = None,
    transform_module: Callable[[Any], Any | None] | None = None,
    derive_module_id: Callable[[str, str, dict[str, Any]], str | None] | None = None,
) -> Any:
    """Build a ``Registry`` from an OpenAPI 3.0/3.1 document.

    See ``Contract: openapi_backend`` in
    ``apcore-mcp/docs/features/openapi-backend.md``.
    """
    toolkit = _require_toolkit()
    from apcore import Registry

    if has_other_backend_source and not prefix:
        raise ValueError(
            "mcp.openapi.prefix is required when an OpenAPI backend is combined with another "
            "backend source: the scanner deduplicates IDs within one scan only and knows nothing "
            "about modules already in the registry. Set --openapi-prefix / mcp.openapi.prefix."
        )

    # --- 1. Locate and load -------------------------------------------------
    resolved = resolve_spec_location(spec, project_root=_resolve_project_root(project_root))
    if resolved is None:
        raise ValueError("mcp.openapi.spec is required and resolved to nothing.")

    document = resolved if isinstance(resolved, dict) else toolkit.load_spec(resolved, headers=headers, timeout=timeout)

    # --- 2. Scan ------------------------------------------------------------
    skipped: list[tuple[str, str]] = []

    def _project(module: Any) -> Any | None:
        """Caller hook first, projection last.

        The order matters and is normative: running the projection last makes
        the invariant *every registered module ID is apcore-legal* hold
        unconditionally, whatever a caller's own hook returns. It also runs
        BEFORE the scanner's ``deduplicate_ids`` — which happens after this
        callback — because lowercasing can CREATE a collision the document did
        not have (``listPets`` and ``listpets``).
        """
        if transform_module is not None:
            module = transform_module(module)
            if module is None:
                return None
        projected = project_module_id(module.module_id)
        if projected is None:
            segments = module.module_id.lower().replace("-", "_").split(".")
            bad = next(
                (seg for seg in segments if not MODULE_ID_SEGMENT.match(seg)),
                module.module_id,
            )
            skipped.append((module.module_id, bad))
            return None
        if projected == module.module_id:
            return module
        from dataclasses import replace as _replace

        return _replace(module, module_id=projected)

    modules = toolkit.OpenAPIScanner().scan(
        document,
        include=include,
        exclude=exclude,
        base_path_prefix=prefix,
        include_deprecated=include_deprecated,
        transform_operation=transform_operation,
        transform_module=_project,
        derive_module_id=derive_module_id,
    )

    for derived, segment in skipped:
        logger.warning(
            "OpenAPI operation skipped: derived module ID %r is not a legal apcore module ID — "
            "the segment %r does not match %s. apcore's registry would refuse it. Supply a "
            "derive_module_id or transform_module hook to name this operation yourself.",
            derived,
            segment,
            MODULE_ID_SEGMENT.pattern,
        )

    for module in modules:
        for warning in module.warnings or []:
            logger.warning("OpenAPI scan warning for %s: %s", module.module_id, warning)

    if not modules:
        logger.warning("OpenAPI document yielded zero modules; the server will start with no tools from it.")

    # --- 3. Collision preflight (FR-OPENAPI-006) ----------------------------
    target = registry if registry is not None else Registry()
    existing = set(_registry_ids(target))
    collisions = sorted({m.module_id for m in modules} & existing)
    if collisions:
        raise ValueError(
            "OpenAPI module IDs collide with modules already in the registry: "
            + ", ".join(collisions)
            + ". Nothing was registered. Set or change mcp.openapi.prefix so the two ID spaces "
            "cannot overlap."
        )

    # --- 4. Base URL --------------------------------------------------------
    effective_base_url = base_url or _document_server_url(document)
    if not effective_base_url:
        raise ValueError(
            "mcp.openapi.base_url is required: the document declares no usable absolute "
            "servers[0].url, so every proxied call would resolve against an unknown host."
        )

    # --- 5. Write -----------------------------------------------------------
    writer = toolkit.HTTPProxyRegistryWriter(
        base_url=effective_base_url,
        auth_header_factory=auth_header_factory,
    )
    for result in writer.write(modules, target):
        if getattr(result, "verification_error", None):
            logger.error(
                "OpenAPI module %s failed to register: %s",
                getattr(result, "module_id", "?"),
                result.verification_error,
            )

    if not acknowledge_unapproved_writes:
        _warn_if_writes_have_no_approval_path(modules, target)
    return target


def build_openapi_backend_from_config(
    openapi_config: Any | None,
    *,
    registry: Any | None = None,
    has_other_backend_source: bool = False,
) -> Any | None:
    """Build a ``Registry`` from a Config Bus ``mcp.openapi`` mapping, or None.

    Mirrors ``acl_builder.build_acl_from_config`` and
    ``middleware_builder.build_middleware_from_config``: the raw ``mcp.openapi``
    Config Bus value is a plain mapping (from ``apcore.yaml`` or
    ``APCORE_MCP_OPENAPI_*`` env vars), not the keyword arguments
    :func:`openapi_backend` takes directly, so this is the one place that
    translates between them.

    PRD F-054 documents ``mcp.openapi`` as reached from three routes — the
    Config Bus, the CLI flags, and ``APCoreMCP.from_openapi`` — and PRD F-054
    Acceptance Criterion 1 states plainly that ``mcp.openapi.spec`` alone,
    with no CLI flag and no explicit code, "starts a server". Before this
    function existed, ``mcp.openapi`` was registered as a Config Bus namespace
    default (so the key round-tripped) but nothing ever read it back — the
    Config Bus route was documented and silently absent in all three bridges.

    ``auth_header_factory`` is deliberately not read from ``openapi_config``:
    it is a callable, and a Config Bus value sourced from YAML/JSON/env can
    never carry one. A caller needing per-request auth headers from a
    Config-Bus-only setup has no route to supply it here — same limitation
    ``build_acl_from_config`` has for anything callable-shaped.

    Returns:
        A ``Registry``, or None when no ``mcp.openapi`` section is configured.

    Raises:
        ValueError: When ``openapi_config`` is truthy but not a mapping, or is
            a mapping with no ``spec`` key. Every other fault surfaces from
            :func:`openapi_backend` itself.
    """
    if not openapi_config:
        return None
    if not isinstance(openapi_config, dict):
        raise ValueError(f"mcp.openapi must be a mapping, got {type(openapi_config).__name__}")

    spec = openapi_config.get("spec")
    if not spec:
        raise ValueError("mcp.openapi.spec is required when mcp.openapi is configured")

    return openapi_backend(
        spec,
        base_url=openapi_config.get("base_url"),
        prefix=openapi_config.get("prefix"),
        include=openapi_config.get("include"),
        exclude=openapi_config.get("exclude"),
        include_deprecated=openapi_config.get("include_deprecated", True),
        headers=openapi_config.get("headers"),
        timeout=openapi_config.get("timeout", 30.0),
        registry=registry,
        has_other_backend_source=has_other_backend_source,
        acknowledge_unapproved_writes=openapi_config.get("acknowledge_unapproved_writes", False),
    )


def _registry_ids(registry: Any) -> list[str]:
    try:
        return list(registry.list(visibility=["public", "hidden"]))
    except TypeError:
        return list(registry.list())
    except Exception:  # pragma: no cover - a registry double without list()
        return []


def _document_server_url(document: dict[str, Any]) -> str | None:
    servers = document.get("servers")
    if not isinstance(servers, list) or not servers:
        return None
    first = servers[0]
    if not isinstance(first, dict):
        return None
    url = first.get("url")
    return url if isinstance(url, str) and url.startswith(_URL_SCHEMES) else None


def _warn_if_writes_have_no_approval_path(modules: list[Any], registry: Any) -> None:
    """Warn that nothing will ask for approval before a write (FR-OPENAPI-005).

    The toolkit infers annotations from the HTTP method alone and never infers
    ``requires_approval``, so every scanned module arrives with it False: a
    ``POST /charges`` that moves money is annotated exactly like a
    ``POST /echo`` and the approval gate fires for neither.

    This reports the **absence of an approval path, never the presence of
    protection** — the rule apcore states on
    ``GovernanceState.unprotected_control_surface``: *"a wired ACL that permits
    every call still yields False."* An attached ACL therefore does not
    suppress it. Whether a rule's ``targets`` cover these modules is a
    match-relation question, which is exactly the predicate §6.2.1 tier 2
    declines to close, and a predicate that cannot be closed must not silence a
    safety warning.
    """
    writes = [
        m
        for m in modules
        if str((m.metadata or {}).get("http_method", "")).upper() in _WRITE_METHODS
        and not getattr(m.annotations, "requires_approval", False)
    ]
    if not writes:
        return
    logger.warning(
        "%d OpenAPI operation(s) use a write method (POST/PUT/PATCH/DELETE) and declare "
        "requires_approval=False — the approval gate will not fire for any of them. The scanner "
        "cannot know which operations are consequential and does not guess. Close it with an ACL "
        "rule carrying `approval: required`, `gate_destructive` on the ExecutionPolicy, or a "
        "transform_module hook that sets the annotation. Set "
        "mcp.openapi.acknowledge_unapproved_writes: true to record this as a deliberate decision.",
        len(writes),
    )
