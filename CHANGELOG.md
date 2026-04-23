# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.14.0] - 2026-04-23

### Changed

- **Dependency bump**: `apcore >= 0.19.0` (was `>= 0.18.0`).
- **New dependency**: `apcore-toolkit >= 0.5.0` — picks up the `ScannedModule.display` field and the `BindingLoader` pure-data loader (not wired in apcore-mcp; this project does not load binding YAML directly).
- `ExecutionRouter.handle_call` response `content` item type widened from `list[dict[str, str]]` to `list[dict[str, Any]]` to carry the optional `_meta` field. The factory translates this to MCP `TextContent.meta` on wire.
- `MCPServerFactory.register_handlers` gains optional `async_bridge` and `descriptor_lookup` kwargs. Backward-compatible: when omitted, behavior is unchanged.

### Added

- **W3C Trace Context propagation** — `ExecutionRouter` now parses `_meta.traceparent` on inbound `tools/call` requests and seeds the apcore `Context` with the extracted `TraceParent`. Responses carry `_meta.traceparent` (per `TextContent.meta`) built from `TraceContext.inject(context)`, letting MCP clients correlate trace chains across module boundaries. Relies on apcore 0.19's strict validation in `Context.create(trace_parent=...)` (all-zero/all-f trace ids are regenerated with a WARN).
- **Async Task Bridge** (F-043) — new `apcore_mcp.server.async_task_bridge.AsyncTaskBridge` wraps apcore's `AsyncTaskManager`. Modules whose descriptor carries `metadata.async == True` or `annotations.extra["mcp_async"] == "true"` are routed to `AsyncTaskManager.submit()` and return an immediate `{"task_id", "status": "pending"}` envelope. Four reserved MCP meta-tools are registered: `__apcore_task_submit`, `__apcore_task_status`, `__apcore_task_cancel`, `__apcore_task_list`. Progress fan-out is available via `_meta.progressToken` (bound per task). `MCPServerFactory.build_tool` now rejects any module whose id starts with `__apcore_`. Enable/disable via `APCoreMCP(async_tasks=...)` or `serve(async_tasks=...)` (default on). Tuning knobs: `async_max_concurrent`, `async_max_tasks`.
- **Observability auto-wiring** — `serve(observability=True)` / `APCoreMCP(observability=True)` instantiate `apcore.observability.MetricsCollector` + `MetricsMiddleware` and `UsageCollector` + `UsageMiddleware` on the Executor and expose `/{explorer_prefix}/api/usage` (and `/api/usage/{module_id}`) returning `ModuleUsageSummary` / `ModuleUsageDetail` JSON. The `metrics_collector=True` sentinel auto-provisions only the metrics middleware (no usage tracking). A user-supplied `MetricsExporter` object continues to work unchanged (back-compat).
- **`--observability` CLI flag** — toggles metrics + usage middleware and usage routes.
- **isinstance-based error dispatch** in `adapters/errors.py` — `TaskLimitExceededError`, `DependencyNotFoundError`, and `DependencyVersionMismatchError` are dispatched via `isinstance` checks against the apcore 0.19 error classes, not duck-typed codes.
- **Expanded `ModuleAnnotations` surfacing** in `AnnotationMapper.to_description_suffix`: `cache_ttl`, `cache_key_fields`, and `pagination_style` now appear in the description annotation block when non-default. Aligns with apcore 0.19's 12-field `ModuleAnnotations`.
- **`DEFAULT_ANNOTATIONS`** in `adapters/annotations.py` extended with `cache_ttl=0`, `cache_key_fields=None`, and `pagination_style="cursor"` to match apcore 0.19 defaults.
- **New error codes** in `constants.ERROR_CODES` and `ErrorMapper`:
  - `DEPENDENCY_NOT_FOUND` — raised by `resolve_dependencies` for missing required deps (replaces prior `ModuleLoadError` path per PROTOCOL_SPEC §5.15.2).
  - `DEPENDENCY_VERSION_MISMATCH` — raised when a declared `version` constraint is unsatisfied.
  - `TASK_LIMIT_EXCEEDED` — raised by `AsyncTaskManager.submit` at capacity. Mapped with `retryable: True`.
  - `VERSION_CONSTRAINT_INVALID` — raised on malformed version constraint strings.
  - `BINDING_SCHEMA_INFERENCE_FAILED` — replaces the deprecated `BINDING_SCHEMA_MISSING` code for auto-schema inference failures.
  - `BINDING_SCHEMA_MODE_CONFLICT`, `BINDING_STRICT_SCHEMA_INCOMPATIBLE`, `BINDING_POLICY_VIOLATION` — parse-time binding validation errors per DECLARATIVE_CONFIG_SPEC.

### Notes

- The `display` overlay resolution in `server/factory.py` already consumes `metadata["display"]["mcp"]` (alias / description / guidance) as produced by `DisplayResolver`; no changes needed for the 0.19 canonical `DisplayOverlay` shape.
- The apcore-toolkit `BindingLoader` was not wired in: apcore-mcp does not load `.binding.yaml` files directly. Registry-bound loads continue to flow through apcore's own `BindingLoader` inside the upstream SDK.
- Async task bridge is in-memory only; tasks do not survive server restart (matches apcore semantics).
- Meta-tool names use the reserved `__apcore_` prefix; user-registered modules with this prefix are now rejected at `build_tool` time to prevent shadowing.
- Usage endpoints are only mounted when Explorer is enabled; headless stdio deployments continue to have no HTTP surface.

---

## [0.13.0] - 2026-04-06

### Added

- **Pipeline Strategy Selection** (F-036) — `serve(strategy=)` parameter and CLI `--strategy` flag with 5 presets: standard, internal, testing, performance, minimal.
- **Tool Output Redaction** (F-038) — `serve(redact_output=True)` applies `redact_sensitive()` to tool output before MCP serialization. Enabled by default.
- **Pipeline Observability** (F-037) — `serve(trace=True)` enables `call_async_with_trace()` for per-step pipeline timing in responses.
- **Tool Preflight Validation** (F-039) — `ExecutionRouter.validate_tool()` for dry-run validation via `Executor.validate()`.
- **YAML Pipeline Configuration** (F-040) — Config Bus `mcp.pipeline` section for declarative pipeline customization.
- **Annotation Metadata Passthrough** (F-041) — `ModuleAnnotations.extra` keys prefixed with `mcp_` flow to tool descriptions.
- **4 new error mappings** — `ConfigEnvMapConflictError`, `PipelineAbortError`, `StepNotFoundError`, `VersionIncompatibleError`.
- **RegistryListener wired to `serve(dynamic=True)`** — dynamic tool registration now operational.

### Changed

- **Dependency bump**: `apcore >= 0.17.1` (was `>= 0.15.1`).
- Pipeline v2 alignment: 11-step pipeline, `call_chain_guard` rename, middleware before input validation.

---

## [0.12.0] - 2026-03-31

### Added

- **Config Bus namespace registration** (F-033) — Registers `mcp` namespace with apcore Config Bus (`APCORE_MCP` env prefix). MCP configuration (transport, host, port, auth, explorer) can be managed via unified `apcore.yaml`.
- **Error Formatter Registry integration** (F-034) — `MCPErrorFormatter` registered with apcore's `ErrorFormatterRegistry`, formalizing MCP error formatting into the shared protocol.
- **Dot-namespaced event constants** (F-035) — `APCORE_EVENTS` dict with canonical event type names from apcore 0.15.0 (§9.16).
- **6 new error code mappings** — `CONFIG_NAMESPACE_DUPLICATE`, `CONFIG_NAMESPACE_RESERVED`, `CONFIG_ENV_PREFIX_CONFLICT`, `CONFIG_MOUNT_ERROR`, `CONFIG_BIND_ERROR`, `ERROR_FORMATTER_DUPLICATE`.

### Changed

- Dependency bump: requires `apcore >= 0.15.1` (was `>= 0.14.0`) for Config Bus (§9.4), Error Formatter Registry (§8.8), and dot-namespaced event types (§9.16).

---

## [0.11.0] - 2026-03-26

### Added

- **Display overlay in `build_tool()`** (§5.13) — MCP tool name, description, and guidance now sourced from `metadata["display"]["mcp"]` when present.
  - Tool name: `metadata["display"]["mcp"]["alias"]` (pre-sanitized by `DisplayResolver`, already `[a-zA-Z_][a-zA-Z0-9_-]*` and ≤ 64 chars).
  - Tool description: `metadata["display"]["mcp"]["description"]`, with `guidance` appended as `\n\nGuidance: <text>` when set.
  - Falls back to raw `module.name` / `module.description` when no display overlay is present.

### Changed

- Dependency bump: requires `apcore-toolkit >= 0.4.0` for `DisplayResolver`.

### Tests

- `TestBuildToolDisplayOverlay` (6 tests): MCP alias used as tool name, MCP description used, guidance appended, surface-specific override wins, fallback to scanner values when no overlay.

---

## [0.10.1] - 2026-03-22

### Changed
- Rebrand: aipartnerup → aiperceivable

## [0.10.0] - 2026-03-14

### Changed

- **BREAKING: `output_formatter` default changed to `None`**: `APCoreMCP` no longer defaults to `apcore_toolkit.to_markdown`. Results are now serialized as raw JSON by default. To restore Markdown formatting, pass `output_formatter=to_markdown` explicitly (requires `apcore-toolkit`).
- **Dependency bump**: Requires `apcore>=0.13.0` (was `>=0.9.0`). Picks up new annotation fields (`cacheable`, `paginated`, `cache_ttl`, `cache_key_fields`, `pagination_style`) and `ExecutionCancelledError` now extending `ModuleError`.
- **Annotation description suffix**: `AnnotationMapper.to_description_suffix()` now includes `cacheable` and `paginated` when set to non-default values.

### Removed

- **`apcore-toolkit` dependency**: Removed from `pyproject.toml` dependencies. `apcore-toolkit` is no longer required to use `apcore-mcp`. Users who want Markdown formatting can install it separately and pass `to_markdown` as the `output_formatter`.

## [0.9.0] - 2026-03-06

### Added

- **`async_serve()` context manager**: New public API for embedding the MCP server into a larger ASGI application. Returns a `Starlette` app via `async with async_serve(registry) as mcp_app:`, enabling co-hosting with A2A, Django ASGI, or other services under a single uvicorn process.
- **`TransportManager.build_streamable_http_app()`**: Low-level async context manager that builds a Starlette ASGI app with MCP transport, health, and metrics routes. Supports `extra_routes` and `middleware` injection.
- **`ExecutionCancelledError` handling**: `ErrorMapper` now maps apcore's `ExecutionCancelledError` to a safe `EXECUTION_CANCELLED` response with `retryable=True`. Internal cancellation details are never leaked.
- **New error codes**: `VERSION_INCOMPATIBLE`, `ERROR_CODE_COLLISION`, and `EXECUTION_CANCELLED` added to `ERROR_CODES` constants.
- **Deep merge for streaming**: Streaming chunk accumulation uses recursive deep merge (depth-capped at 32) instead of shallow merge, correctly handling nested response structures.

### Changed

- **Dependency bump**: Requires `apcore>=0.9.0` (was `>=0.7.0`). Picks up `PreflightResult`, execution pipeline, retry middleware, error code registry, and more.
- **Preflight validation aligned with apcore 0.9.0**: `ExecutionRouter` now passes the router-built `Context` (with identity, callbacks) to `Executor.validate()`, enabling accurate ACL and call-chain preflight checks. Error formatting handles all three `PreflightResult` error shapes: nested schema errors, flat field errors, and code-only errors.
- **Annotation description suffix**: `AnnotationMapper.to_description_suffix()` now produces safety warnings (`WARNING: DESTRUCTIVE`, `REQUIRES APPROVAL`) as a separate section above the machine-readable annotation block, improving AI agent awareness of dangerous operations.
- **Auth middleware best-effort identity on exempt paths**: `AuthMiddleware` now attempts identity extraction on exempt paths. Valid tokens populate `auth_identity_var` even when auth is not required, allowing downstream handlers to use identity when available.

## [0.8.0] - 2026-03-02

### Added

- **Approval system (F-028)**: Full runtime approval support via `ElicitationApprovalHandler` that bridges MCP elicitation to apcore's approval system. New `approval_handler` parameter on `serve()`. Supports `request_approval()` and `check_approval()` methods.
  - `ElicitationApprovalHandler`: Presents approval requests to users via MCP elicitation. Maps elicit actions (`accept`/`decline`/`cancel`) to `ApprovalResult` statuses.
  - CLI `--approval` flag with choices: `elicit`, `auto-approve`, `always-deny`, `off` (default).
- **Approval error codes**: `APPROVAL_DENIED`, `APPROVAL_TIMEOUT`, `APPROVAL_PENDING` added to `ERROR_CODES`.
- **Enhanced error responses with AI guidance**: `ErrorMapper` now extracts `retryable`, `ai_guidance`, `user_fixable`, and `suggestion` fields from apcore `ModuleError` and includes non-None values in error response dicts. `ExecutionRouter` appends AI guidance as structured JSON to error text content for AI agent consumption.
- **AI intent metadata in tool descriptions**: `MCPServerFactory.build_tool()` reads `descriptor.metadata` for AI intent keys (`x-when-to-use`, `x-when-not-to-use`, `x-common-mistakes`, `x-workflow-hints`) and appends them to tool descriptions for agent visibility.
- **Streaming annotation**: `DEFAULT_ANNOTATIONS` now includes `streaming` field. `AnnotationMapper.to_description_suffix()` includes `streaming=true` when the annotation is set.

### Changed

- **`APPROVAL_TIMEOUT` auto-retryable**: `ErrorMapper` sets `retryable=True` for `APPROVAL_TIMEOUT` errors, signaling to AI agents that the operation can be retried.
- **`APPROVAL_PENDING` includes `approval_id`**: `ErrorMapper` extracts `approval_id` from error details for `APPROVAL_PENDING` errors.
- **Error text content enriched**: Router error text now includes AI guidance fields as a structured JSON appendix when present, enabling AI agents to parse retry/fix hints.

## [0.7.0] - 2026-02-28

### Added

- **JWT Authentication (F-027)**: Optional JWT-based authentication for HTTP transports (`streamable-http`, `sse`). New `authenticator` parameter on `serve()` and `MCPServer`. Validates Bearer tokens, maps JWT claims to apcore `Identity`, and injects identity into `Context` for ACL enforcement.
  - `JWTAuthenticator`: Configurable JWT validation with `ClaimMapping` for flexible claim-to-Identity field mapping. Supports custom algorithms, audience, issuer, and required claims.
  - `AuthMiddleware`: ASGI middleware that bridges HTTP authentication to MCP handlers via `ContextVar[Identity]`. Supports `exempt_paths` (exact match) and `exempt_prefixes` (prefix match) for unauthenticated endpoints.
  - `Authenticator` Protocol: `@runtime_checkable` protocol for custom authentication backends.
- **Permissive auth mode**: `require_auth=False` parameter on `serve()` and `MCPServer` allows unauthenticated requests to proceed without identity instead of returning 401.
- **`exempt_paths` parameter**: `serve()` and `MCPServer` accept `exempt_paths` for exact-path authentication bypass (e.g. `{"/health", "/metrics"}`).
- **CLI JWT flags**: `--jwt-secret`, `--jwt-algorithm`, `--jwt-audience`, `--jwt-issuer` arguments for enabling JWT authentication from the command line.
- **CLI `--jwt-key-file`**: Read JWT verification key from a PEM file (e.g. RS256 public key). Takes priority over `--jwt-secret` and `APCORE_JWT_SECRET` env var.
- **CLI `--jwt-require-auth` / `--no-jwt-require-auth`**: Toggle permissive auth mode from the command line.
- **CLI `--exempt-paths`**: Comma-separated list of paths exempt from authentication.
- **`APCORE_JWT_SECRET` env var fallback**: CLI resolves JWT key in priority order: `--jwt-key-file` > `--jwt-secret` > `APCORE_JWT_SECRET` environment variable.
- **Explorer Authorization UI**: Swagger-UI-style Authorization input field in the Tool Explorer. Paste a Bearer token to authenticate tool execution requests. Generated cURL commands automatically include the Authorization header.
- **Explorer auth enforcement**: When `authenticator` is set, tool execution via the Explorer returns 401 Unauthorized without a valid Bearer token. The Explorer UI displays a clear error message prompting the user to enter a token.
- **Auth failure audit logging**: `AuthMiddleware` emits a `WARNING` log with the request path on authentication failure.
- **`extract_headers()` utility**: Public helper to extract ASGI scope headers as a lowercase-key dict. Exported from `apcore_mcp.auth`.
- **JWT authentication example**: `examples/run.py` supports `APCORE_JWT_SECRET` environment variable to demonstrate JWT authentication with a sample token.
- **PyJWT dependency**: Added `PyJWT>=2.0` to project dependencies.

### Changed

- **Explorer UI layout**: Redesigned from a bottom-panel layout to a Swagger-UI-style inline accordion. Each tool expands its detail, schema, and "Try it" section directly below the tool name. Only one tool can be expanded at a time. Detail is loaded once on first expand and cached.
- **AuthMiddleware `exempt_prefixes`**: Added `exempt_prefixes` parameter for prefix-based path exemption. Explorer paths are automatically exempt when both `explorer` and `authenticator` are enabled, so the Explorer UI always loads.
- **`extract_headers` refactored**: Moved from private `AuthMiddleware._extract_headers()` to module-level `extract_headers()` function for reuse in Explorer routes.

## [0.6.0] - 2026-02-25

### Added

- **Example modules**: `examples/` with 5 runnable demo modules — 3 class-based (`text_echo`, `math_calc`, `greeting`) and 2 binding.yaml (`convert_temperature`, `word_count`) — for quick Explorer UI demo out of the box.

### Changed

- **BREAKING: `ExecutionRouter.handle_call()` return type**: Changed from `(content, is_error)` to `(content, is_error, trace_id)`. Callers that unpack the 2-tuple must update to 3-tuple unpacking.
- **BREAKING: Explorer `/call` response format**: Changed from `{"result": ...}` / `{"error": ...}` to MCP-compliant `CallToolResult` format: `{"content": [...], "isError": bool, "_meta": {"_trace_id": ...}}`.

### Fixed

- **MCP protocol compliance**: Router no longer injects `_trace_id` as a content block in tool results. `trace_id` is now returned as a separate tuple element and surfaced in Explorer responses via `_meta`. Factory handler raises exceptions for errors so the MCP SDK correctly sets `isError=True`.
- **Explorer UI default values**: `defaultFromSchema()` now correctly skips `null` defaults and falls through to type-based placeholders, fixing blank form fields for binding.yaml modules.

## [0.5.1] - 2026-02-25

### Changed

- **Rename Inspector to Explorer**: Renamed the MCP Tool Inspector module to MCP Tool Explorer across the entire codebase — module path (`apcore_mcp.inspector` → `apcore_mcp.explorer`), CLI flags, Python API parameters, HTML UI, tests, README, and CHANGELOG. No functional changes; all endpoints and behavior remain identical.

### Fixed

- **Version test**: Fixed `test_run_uses_package_version_when_version_is_none` to patch `importlib.metadata.version` so the test is not sensitive to the installed package version.

## [0.5.0] - 2026-02-24

### Added

- **MCP Tool Explorer (F-026)**: Optional browser-based UI for inspecting and testing MCP tools, mounted at `/explorer` when `explorer=True`. Includes 4 HTTP endpoints (`GET /explorer/`, `GET /explorer/tools`, `GET /explorer/tools/<name>`, `POST /explorer/tools/<name>/call`), a self-contained HTML/CSS/JS page with no external dependencies, configurable `explorer_prefix`, and `allow_execute` guard (default `False`). HTTP transports only; silently ignored for stdio.
- **CLI Explorer flags**: `--explorer`, `--explorer-prefix`, and `--allow-execute` arguments.
- **Explorer UI: proactive execution status detection**: The Explorer probes execution status on page load via a lightweight POST to `/tools/__probe__/call`, so the "Tool execution is disabled" message appears immediately instead of requiring a user click first.
- **Explorer UI: URL-safe tool name encoding**: Tool names in fetch URLs are wrapped with `encodeURIComponent()` to prevent malformed URLs when tool names contain special characters.
- **Explorer UI: error handling on tool detail fetch**: `.catch()` handler on the `loadDetail` fetch chain displays network errors in the detail panel instead of silently swallowing them.

## [0.4.0] - 2026-02-23

### Added

- **Resource handlers**: `MCPServerFactory.register_resource_handlers()` for serving documentation resources via MCP.
- **CI workflow**: GitHub Actions CI pipeline and `CODEOWNERS` file.
- **Missing error codes**: Added `MODULE_EXECUTE_ERROR` and `GENERAL_INVALID_INPUT` to error codes constants.
- **serve() parameter tests**: Comprehensive test suite for `serve()` parameter validation.
- **Metrics endpoint tests**: Dedicated test suite for Prometheus `/metrics` endpoint.

### Changed

- **Version management**: Consolidated version into `__init__.__version__`, removed `_version.py`.

### Fixed

- **Cache configuration**: Removed unnecessary cache configuration from Python setup step.
- **Code formatting**: Improved linting checks in CI workflow, factory, router, and test files.

### Refactored

- **Import cleanup**: Removed unused imports across multiple test files; reordered imports in MCPServer for consistency.
- **Code structure**: General readability and maintainability improvements.

## [0.3.0] - 2026-02-22

### Added

- **metrics_collector parameter**: `serve(metrics_collector=...)` accepts a `MetricsCollector` instance to enable Prometheus metrics export.
- **`/metrics` Prometheus endpoint**: HTTP-based transports (`streamable-http`, `sse`) now serve a `/metrics` route returning Prometheus text format when a `metrics_collector` is provided. Returns 404 when no collector is configured.
- **trace_id passback**: Every successful response now includes a second content item with `_trace_id` metadata for request tracing. *(Removed in 0.5.1: trace_id moved out of content blocks into separate return value for MCP protocol compliance.)*
- **validate_inputs**: `serve(validate_inputs=True)` enables pre-execution input validation via `Executor.validate()`. Invalid inputs are rejected before module execution.
- **Always-on Context**: `Context` is now always created for every tool call, enabling trace_id generation even without MCP callbacks.

### Changed

- **SchemaExporter integration**: `MCPServerFactory.build_tool()` now uses `apcore.schema.exporter.SchemaExporter.export_mcp()` for canonical MCP annotation mapping instead of duplicating logic.
- **to_strict_schema() delegation**: `OpenAIConverter._apply_strict_mode()` now delegates to `apcore.schema.strict.to_strict_schema()` instead of custom recursive implementation. This adds x-* extension stripping, oneOf/anyOf/allOf recursion, $defs recursion, and alphabetically sorted required lists.
- **Dependency bump**: Requires `apcore>=0.5.0` (was `>=0.2.0`).

### Removed

- **Custom strict mode**: Removed `OpenAIConverter._apply_strict_recursive()` in favor of `to_strict_schema()`.

## [0.2.0] - 2026-02-20

### Added

- **MCPServer**: Non-blocking MCP server wrapper for framework integrations with configurable transport and async event loop management.
- **serve() hooks**: `on_startup` and `on_shutdown` callbacks for lifecycle management.
- **Health endpoint**: Built-in health check support for HTTP-based transports.
- **Constants module**: Centralized `REGISTRY_EVENTS`, `ErrorCodes`, and `MODULE_ID_PATTERN` for consistent values across adapters and listeners.
- **Module ID validation**: Enhanced `id_normalizer.normalize()` with format validation using `MODULE_ID_PATTERN`.
- **Exported building blocks**: Public API exports for `MCPServerFactory`, `ExecutionRouter`, `RegistryListener`, and `TransportManager`.

### Fixed

- **MCP Tool metadata**: Fixed use of `_meta` instead of `meta` in MCP Tool constructor for proper internal metadata handling.

### Refactored

- **Circular import resolution**: Moved utility functions (`resolve_registry`, `resolve_executor`) to dedicated `_utils.py` module to prevent circular dependencies between `__init__.py` and `server/server.py`.

## [0.1.0] - 2026-02-15

### Added

- **Public API**: `serve()` to launch an MCP Server from any apcore Registry or Executor.
- **Public API**: `to_openai_tools()` to export apcore modules as OpenAI-compatible tool definitions.
- **CLI**: `apcore-mcp` command with `--extensions-dir`, `--transport`, `--host`, `--port`, `--name`, `--version`, and `--log-level` options.
- **Three transports**: stdio (default), Streamable HTTP, and SSE.
- **SchemaConverter**: JSON Schema conversion with `$ref`/`$defs` inlining for MCP and OpenAI compatibility.
- **AnnotationMapper**: Maps apcore annotations (readonly, destructive, idempotent, open_world) to MCP `ToolAnnotations`.
- **ErrorMapper**: Sanitizes apcore errors for safe client exposure — no stack traces, no internal details leaked.
- **ModuleIDNormalizer**: Bijective dot-to-dash conversion for OpenAI function name compatibility.
- **OpenAIConverter**: Full registry-to-OpenAI conversion with `strict` mode (Structured Outputs) and `embed_annotations` support.
- **MCPServerFactory**: Creates MCP Server instances, builds Tool objects, and registers `list_tools`/`call_tool` handlers.
- **ExecutionRouter**: Routes MCP tool calls to apcore Executor with error sanitization.
- **TransportManager**: Manages stdio, Streamable HTTP, and SSE transport lifecycle.
- **RegistryListener**: Thread-safe dynamic tool registration via `registry.on("register"/"unregister")` callbacks.
- **Structured logging**: All components use `logging.getLogger(__name__)` under the `apcore_mcp` namespace.
- **Dual input**: Both `serve()` and `to_openai_tools()` accept either a Registry or Executor instance.
- **Filtering**: `tags` and `prefix` parameters for selective module exposure.
- **260 tests**: Unit, integration, E2E, performance, and security test suites.

[0.10.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/aiperceivable/apcore-mcp-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/aiperceivable/apcore-mcp-python/releases/tag/v0.1.0
