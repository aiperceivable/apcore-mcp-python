<div align="center">
  <img src="https://raw.githubusercontent.com/aiperceivable/apcore-mcp/main/apcore-mcp-logo.svg" alt="apcore-mcp logo" width="200"/>
</div>

# apcore-mcp

Automatic MCP Server & OpenAI Tools Bridge for apcore.

**apcore-mcp** turns any [apcore](https://github.com/aiperceivable/apcore)-based project into an MCP Server and OpenAI tool provider — with **zero code changes** to your existing project.

```
┌──────────────────┐
│  django-apcore   │  ← your existing apcore project (unchanged)
│  flask-apcore    │
│  ...             │
└────────┬─────────┘
         │  extensions directory
         ▼
┌──────────────────┐
│    apcore-mcp    │  ← just install & point to extensions dir
└───┬──────────┬───┘
    │          │
    ▼          ▼
  MCP       OpenAI
 Server      Tools
```

## MCP input schemas

MCP discovery preserves the module's runtime input semantics: optional properties,
explicit nullable values, defaults, local references and free-form objects remain
unchanged. Arguments pass through without null-placeholder deletion. Explicit
additional-property restrictions still apply. Factory `strict` options are retained
for compatibility and do not project OpenAI strict schemas onto MCP. OpenAI tool
exports and standalone SchemaConverter APIs retain their existing behavior.


## Design Philosophy

- **Zero intrusion** — your apcore project needs no code changes, no imports, no dependencies on apcore-mcp
- **Zero configuration** — point to an extensions directory, everything is auto-discovered
- **Pure adapter** — apcore-mcp reads from the apcore Registry; it never modifies your modules
- **Works with any `xxx-apcore` project** — if it uses the apcore Module Registry, apcore-mcp can serve it

## Documentation

For full documentation, including Quick Start guides for both Python and TypeScript, visit:
**[https://aiperceivable.github.io/apcore-mcp/](https://aiperceivable.github.io/apcore-mcp/)**

## Installation

Install apcore-mcp alongside your existing apcore project:

Requires Python 3.11+ and apcore >= 0.32.0. The `markdown` and `openapi` extras require apcore-toolkit >= 0.14.0.

```bash
pip install apcore-mcp
```

That's it. Your existing project requires no changes.

Markdown tool descriptions (`rich_description=True`) and the built-in `csv` /
`jsonl` output formats are powered by `apcore-toolkit`, which ships as an
optional extra — install it when you want either:

```bash
pip install 'apcore-mcp[markdown]'
```

Without the extra, both features degrade gracefully: descriptions fall back to
the plain one-liner and output falls back to JSON, each with a WARN log.

## Quick Start

### Try it now

The repo includes 5 example modules (class-based + binding.yaml) you can run immediately:

```bash
pip install -e .
PYTHONPATH=./examples/binding_demo python examples/run.py
# Open http://127.0.0.1:8000/explorer/
```

See [examples/README.md](examples/README.md) for all run modes and module details.

### Zero-code approach (CLI)

If you already have an apcore-based project with an extensions directory, just run:

```bash
apcore-mcp --extensions-dir /path/to/your/extensions
```

All modules are auto-discovered and exposed as MCP tools. No code needed.

### Programmatic approach (Python API)

The `APCoreMCP` class is the recommended entry point — one object, all capabilities:

```python
from apcore_mcp import APCoreMCP

mcp = APCoreMCP("./extensions")

# Launch as MCP Server
mcp.serve()

# Or with HTTP + Explorer UI
mcp.serve(transport="streamable-http", port=8000, explorer=True)

# Or export as OpenAI tools
tools = mcp.to_openai_tools()
```

You can also pass an existing `Registry` or `Executor`:

```python
from apcore import Registry
from apcore_mcp import APCoreMCP

registry = Registry(extensions_dir="./extensions")
registry.discover()
mcp = APCoreMCP(registry, name="my-server", tags=["public"])
```

<details>
<summary>Function-based API (still supported)</summary>

```python
from apcore import Registry
from apcore_mcp import serve, to_openai_tools

registry = Registry(extensions_dir="./extensions")
registry.discover()

serve(registry)
tools = to_openai_tools(registry)
```
</details>

## Integration with Existing Projects

### Typical apcore project structure

```
your-project/
├── extensions/          ← modules live here
│   ├── image_resize/
│   ├── text_translate/
│   └── ...
├── your_app.py          ← your existing code (untouched)
└── ...
```

### Adding MCP support

No changes to your project. Just run apcore-mcp alongside it:

```bash
# Install (one time)
pip install apcore-mcp

# Run
apcore-mcp --extensions-dir ./extensions
```

Your existing application continues to work exactly as before. apcore-mcp operates as a separate process that reads from the same extensions directory.

### Adding OpenAI tools support

For OpenAI integration, a thin script is needed — but still **no changes to your existing modules**:

```python
from apcore import Registry
from apcore_mcp import to_openai_tools

registry = Registry(extensions_dir="./extensions")
registry.discover()

tools = to_openai_tools(registry)
# Use with openai.chat.completions.create(tools=tools)
```

## MCP Client Configuration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "apcore": {
      "command": "apcore-mcp",
      "args": ["--extensions-dir", "/path/to/your/extensions"]
    }
  }
}
```

### Claude Code

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "apcore": {
      "command": "apcore-mcp",
      "args": ["--extensions-dir", "./extensions"]
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "apcore": {
      "command": "apcore-mcp",
      "args": ["--extensions-dir", "./extensions"]
    }
  }
}
```

### Remote HTTP access

```bash
apcore-mcp --extensions-dir ./extensions \
    --transport streamable-http \
    --host 0.0.0.0 \
    --port 9000
```

Connect any MCP client to `http://your-host:9000/mcp`.

## CLI Reference

```
apcore-mcp --extensions-dir PATH [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--extensions-dir` | *(required)* | Path to apcore extensions directory |
| `--transport` | `stdio` | Transport: `stdio`, `streamable-http`, or `sse` |
| `--host` | `127.0.0.1` | Host for HTTP-based transports |
| `--port` | `8000` | Port for HTTP-based transports (1-65535) |
| `--name` | `apcore-mcp` | MCP server name (max 255 chars) |
| `--version` | package version | MCP server version string |
| `--log-level` | `INFO` | Logging: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--explorer` | off | Enable the browser-based Tool Explorer UI (HTTP only) |
| `--explorer-prefix` | `/explorer` | URL prefix for the explorer UI |
| `--allow-execute` | off | Allow tool execution from the explorer UI |
| `--jwt-secret` | — | JWT secret key for Bearer token auth (HTTP only) |
| `--jwt-key-file` | — | Path to PEM key file for JWT verification (e.g. RS256 public key) |
| `--jwt-algorithm` | `HS256` | JWT signing algorithm |
| `--jwt-audience` | — | Expected JWT audience claim |
| `--jwt-issuer` | — | Expected JWT issuer claim |
| `--jwt-require-auth` | on | Require valid token; use `--no-jwt-require-auth` for permissive mode |
| `--exempt-paths` | — | Comma-separated paths exempt from auth (e.g. `/health,/metrics`) |
| `--approval` | `off` | Approval handler: `elicit`, `auto-approve`, `always-deny`, or `off` |
| `--output-format` | `json` | Built-in output format: `json`, `csv`, or `jsonl` |

JWT key resolution priority: `--jwt-key-file` > `--jwt-secret` > `APCORE_JWT_SECRET` environment variable.

Exit codes: `0` normal, `1` invalid arguments, `2` startup failure.

## Python API Reference

### `APCoreMCP` (recommended)

The unified entry point — configure once, use everywhere:

```python
from apcore_mcp import APCoreMCP

mcp = APCoreMCP(
    "./extensions",              # path, Registry, or Executor
    name="apcore-mcp",          # server name
    version=None,                # defaults to package version
    tags=None,                   # filter modules by tags
    prefix=None,                 # filter modules by ID prefix
    log_level=None,              # logging level ("DEBUG", "INFO", etc.)
    validate_inputs=False,       # validate inputs against schemas
    metrics_collector=None,      # MetricsExporter | bool — `True` auto-instantiates the collector
    observability=False,         # enable MetricsMiddleware + UsageMiddleware + /metrics + /api/usage
    authenticator=None,          # Authenticator for JWT/token auth (HTTP only)
    require_auth=True,           # False = permissive mode (no 401)
    exempt_paths=None,           # exact paths that bypass auth
    approval_handler=None,       # approval handler for runtime approval
    output_formatter=None,        # default: None (raw JSON); pass to_markdown to opt into apcore-toolkit Markdown
    middleware=None,             # list[Middleware] — user middleware applied after built-ins
    acl=None,                    # apcore.ACL — module access control
    async_tasks=True,            # enable F-043 Async Task Bridge
    async_max_concurrent=10,     # max concurrent async tasks
    async_max_tasks=1000,        # max queued async tasks
)

# Note: redact_output, strategy, and trace are configurable on the
# function-based serve() / async_serve(); they are not exposed on
# APCoreMCP.serve() (see the `serve()` reference below).

# Launch as MCP server (blocking)
mcp.serve(transport="streamable-http", port=8000, explorer=True)

# Export as OpenAI tools
tools = mcp.to_openai_tools(strict=True)

# Embed into ASGI app
async with mcp.async_serve(explorer=True) as app:
    ...

# Inspect
mcp.tools       # list of module IDs
mcp.registry    # underlying Registry
mcp.executor    # underlying Executor
```

### Standard output with stdio transport

During stdio startup, execution and shutdown, apcore-mcp reserves stdout for
MCP messages and sends diagnostic output to stderr. This includes module
`print()` calls, logging handlers already targeting stdout, and apcore's
`StdoutExporter` and `StdoutSubscriber`. HTTP/SSE runtime output keeps the host's
normal policy. Configuration loading is protected before transport selection.

A stdio server owns process stdout for its lifetime; run one per process. The
scope is restored on normal exit, startup failure and cancellation. If the OS
rejects descriptor restoration, further stdio ownership fails closed; restart
the process before serving stdio again.

The CLI protects backend discovery; the convenience `serve()` also protects APCoreMCP
construction. Work performed before calling these entry points, including
imports and construction of a separately instantiated `APCoreMCP`, remains the
caller's responsibility. Keep diagnostics on stderr there too.

With ordinary process streams, fd 1 is redirected and MCP writes through its
own saved descriptor. When embedding with custom streams that are not bound to stdout fd 1 and a
distinct stderr descriptor, protection covers Python stdout lookups and
existing stdout logging handlers. A saved raw stream in that mode, an explicitly duplicated
protocol descriptor, or a cached native Windows standard handle can bypass the
scope. Do not write to those endpoints or merge stderr into stdout in a stdio
launcher. The threaded `MCPServer` cancels and awaits its loop tasks, finalizes async
generators, and waits for executor workers before restoring stdout. Finish
application background work running outside that loop before the server stops.

### `serve()` (function-based)

```python
from apcore_mcp import serve

serve(
    registry_or_executor,        # Registry or Executor
    transport="stdio",           # "stdio" | "streamable-http" | "sse"
    host="127.0.0.1",           # host for HTTP transports
    port=8000,                   # port for HTTP transports
    name="apcore-mcp",          # server name
    version=None,                # defaults to package version
    on_startup=None,             # callback before transport starts
    on_shutdown=None,            # callback after transport completes
    tags=None,                   # filter modules by tags
    prefix=None,                 # filter modules by ID prefix
    log_level=None,              # logging level ("DEBUG", "INFO", etc.)
    dynamic=False,               # rebuild tools on registry events
    validate_inputs=False,       # validate inputs against schemas
    metrics_collector=None,      # MetricsExporter | bool — `True` auto-instantiates apcore.observability.MetricsCollector
    explorer=False,              # enable browser-based Tool Explorer UI
    explorer_prefix="/explorer", # URL prefix for the explorer
    allow_execute=False,         # allow tool execution from the explorer
    explorer_title="MCP Tool Explorer",
    explorer_project_name=None,
    explorer_project_url=None,
    authenticator=None,          # Authenticator for JWT/token auth (HTTP only)
    require_auth=True,           # False = permissive mode (no 401)
    exempt_paths=None,           # exact paths that bypass auth
    approval_handler=None,       # approval handler for runtime approval
    output_formatter=None,       # default None (raw JSON); pass apcore_toolkit.to_markdown to opt in
    strategy=None,               # pipeline strategy preset: "standard" | "internal" | "testing" | "performance" | "minimal"
    redact_output=True,          # mask x-sensitive / _secret_* fields in outputs
    trace=False,                 # enable per-call apcore pipeline trace metadata
    middleware=None,             # list[Middleware] — applied after built-ins
    acl=None,                    # apcore.ACL — module access control
    observability=False,         # enable MetricsMiddleware + UsageMiddleware + /metrics + /api/usage
    async_tasks=True,            # enable F-043 Async Task Bridge
    async_max_concurrent=10,     # max concurrent async tasks
    async_max_tasks=1000,        # max queued async tasks
    # Note: schema_converter / annotation_mapper / error_mapper hooks are reserved for v0.16+ (EB-2)
)
```

Accepts either a `Registry` or `Executor`. When a `Registry` is passed, an `Executor` is created automatically.

### `async_serve()`

Embed the MCP server into a larger ASGI application (e.g. co-host with A2A, Django ASGI):

```python
from apcore_mcp import async_serve

async with async_serve(registry, explorer=True) as mcp_app:
    combined = Starlette(routes=[
        Mount("/mcp", app=mcp_app),
        Mount("/a2a", app=a2a_app),
    ])
    config = uvicorn.Config(combined, host="0.0.0.0", port=8000)
    await uvicorn.Server(config).serve()
```

Accepts the same parameters as `serve()` (except `transport`, `host`, `port`, `on_startup`, `on_shutdown`). Returns a `Starlette` app via async context manager.

### Tool Explorer

When `explorer=True` is passed to `serve()`, a browser-based Tool Explorer UI is mounted on HTTP transports. It provides an interactive page for browsing tool schemas and testing tool execution.

```python
serve(registry, transport="streamable-http", explorer=True, allow_execute=True)
# Open http://127.0.0.1:8000/explorer/ in a browser
```

**Endpoints:**

| Endpoint | Description |
|----------|-------------|
| `GET /explorer/` | Interactive HTML page (self-contained, no external dependencies) |
| `GET /explorer/tools` | JSON array of all tools with name, description, annotations |
| `GET /explorer/tools/<name>` | Full tool detail with inputSchema |
| `POST /explorer/tools/<name>/call` | Execute a tool (requires `allow_execute=True`) |

- **HTTP transports only** (`streamable-http`, `sse`). Silently ignored for `stdio`.
- **Execution disabled by default** — set `allow_execute=True` to enable Try-it.
- **Custom prefix** — use `explorer_prefix="/browse"` to mount at a different path.

### JWT Authentication

Optional Bearer token authentication for HTTP transports. Supports symmetric (HS256) and asymmetric (RS256) algorithms.

```python
from apcore_mcp.auth import JWTAuthenticator

auth = JWTAuthenticator(key="my-secret")

serve(
    registry,
    transport="streamable-http",
    authenticator=auth,
    explorer=True,
    allow_execute=True,
)
```

**Permissive mode** — allow unauthenticated access (identity is `None` when no token is provided):

```python
serve(registry, transport="streamable-http", authenticator=auth, require_auth=False)
```

**Path exemption** — bypass auth for specific paths:

```python
serve(registry, transport="streamable-http", authenticator=auth, exempt_paths={"/health", "/metrics"})
```

See [examples/README.md](examples/README.md) for a runnable JWT demo with a pre-generated test token.

### Approval Mechanism

Optional runtime approval for tool execution. Bridges MCP elicitation to apcore's approval system.

```python
from apcore_mcp.adapters.approval import ElicitationApprovalHandler

handler = ElicitationApprovalHandler()

serve(
    registry,
    transport="streamable-http",
    approval_handler=handler,
    explorer=True,
)
```

**Built-in handlers:**

| Handler | Description |
|---------|-------------|
| `ElicitationApprovalHandler` | Prompts the MCP client for user confirmation via elicitation |
| `AutoApproveHandler` | Auto-approves all requests (dev/testing only) |
| `AlwaysDenyHandler` | Rejects all requests (enforcement) |

CLI usage:

```bash
apcore-mcp --extensions-dir ./extensions --approval elicit
```

### Output Formatting

By default, tool execution results are serialized as JSON (`json.dumps`). You can customize this by passing an `output_format` name or a custom `output_formatter` callable.

**Built-in formats** (requires the `[markdown]` extra — `pip install 'apcore-mcp[markdown]'`):

```python
# Via CLI
# apcore-mcp --extensions-dir ./extensions --output-format csv

# Via API
mcp = APCoreMCP("./extensions", output_format="csv")
```

Supports `json`, `csv`, and `jsonl`. Non-tabular data gracefully falls back to JSON.

**Custom formatter**:
Pass a callable that converts a `dict` or `list` result into a string.

```python
def my_formatter(data: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in data.items())

mcp = APCoreMCP("./extensions", output_formatter=my_formatter)
```

The `output_formatter` parameter is also available on the function-based `serve()` API and on `ExecutionRouter` directly.

### Extension Helpers

Modules can report progress and request user input during execution via MCP protocol callbacks. Both helpers no-op gracefully when called outside an MCP context.

```python
from apcore_mcp import report_progress, elicit

# Inside a module's execute():
await report_progress(context, progress=50, total=100, message="Halfway done")

result = await elicit(context, "Confirm deletion?", {"type": "object", "properties": {"confirm": {"type": "boolean"}}})
if result and result["action"] == "accept":
    # proceed
    ...
```

### `/metrics` Prometheus Endpoint

When `metrics_collector` is provided to `serve()`, a `/metrics` HTTP endpoint is exposed that returns metrics in Prometheus text exposition format.

- **Available on HTTP-based transports only** (`streamable-http`, `sse`). Not available with `stdio` transport.
- **Returns Prometheus text format** with Content-Type `text/plain; version=0.0.4; charset=utf-8`.
- **Returns 404** when no `metrics_collector` is configured.

```python
from apcore.observability import MetricsCollector
from apcore_mcp import serve

collector = MetricsCollector()
serve(registry, transport="streamable-http", metrics_collector=collector)
# GET http://127.0.0.1:8000/metrics -> Prometheus text format
```

### `to_openai_tools()`

```python
from apcore_mcp import to_openai_tools

tools = to_openai_tools(
    registry_or_executor,       # Registry or Executor
    embed_annotations=False,    # append annotation hints to descriptions
    strict=False,               # OpenAI Structured Outputs strict mode
    tags=None,                  # filter by tags, e.g. ["image"]
    prefix=None,                # filter by module ID prefix, e.g. "image"
)
```

Returns a list of dicts directly usable with the OpenAI API:

```python
import openai

client = openai.OpenAI()
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Resize the image to 512x512"}],
    tools=tools,
)
```

**Strict mode** (`strict=True`): sets `additionalProperties: false`, makes all properties required (optional ones become nullable), removes defaults.

**Annotation embedding** (`embed_annotations=True`): appends `[Annotations: read_only, idempotent]` to descriptions.

**Filtering**: `tags=["image"]` or `prefix="text"` to expose a subset of modules.

### Using with an Executor

If you need custom middleware, ACL, or execution configuration:

```python
from apcore import Registry, Executor

registry = Registry(extensions_dir="./extensions")
registry.discover()
executor = Executor(registry)

serve(executor)
tools = to_openai_tools(executor)
```

> **Security note — `system.*` management modules.** Enabling apcore's
> `sys_modules.enabled` config registers `system.health.*` / `system.usage.*`
> / `system.manifest.*` (exposed here as MCP resources) and
> `system.control.*` (exposed as tools) into the registry this server
> serves — but enabling it does **not** by itself protect them. Without a
> configured `mcp.acl` (or `acl=`) section, `apcore.ACL.discover()` finds no
> `acl/` directory and returns `None`, which is indistinguishable from "no
> ACL was ever configured": every caller can read health/usage/manifest and,
> more importantly, invoke `system.control.*` (reload modules, change
> runtime config, toggle features). Always pair `sys_modules.enabled: true`
> with an `mcp.acl` section — see the worked example in
> `apcore_mcp.acl_builder`'s module docstring
> (aiperceivable/apcore-mcp#14/#15). `serve()`/`async_serve()` log a startup
> WARNING via `executor.governance_state()` when this gap is detected, but
> the server still starts — the warning is advisory only.

## Features

- **Auto-discovery** — all modules in the extensions directory are found and exposed automatically
- **Display overlay** — `metadata["display"]["mcp"]` controls MCP tool names, descriptions, and guidance per module (§5.13); set via `binding_path` in `fastapi-apcore`
- **Markdown tool descriptions** (`rich_description=True`, v0.15+) — render `Tool.description` / OpenAI `function.description` as canonical apcore-toolkit Markdown (parameters, returns, behavior table, tags, examples) so LLMs get more decision-relevant signal per token. Requires the `[markdown]` extra.
- **Module preview meta-tool** (`__apcore_module_preview`, v0.15+) — lets AI orchestrators run `executor.validate()` to predict state changes WITHOUT executing the module (apcore PROTOCOL_SPEC §5.6). Returns `{valid, requires_approval, predicted_changes, checks}`.
- **Three transports** — stdio (default, for desktop clients), Streamable HTTP, and SSE
- **JWT authentication** — optional Bearer token auth for HTTP transports with `JWTAuthenticator`, permissive mode, PEM key file support, and env var fallback
- **Approval mechanism** — runtime approval via MCP elicitation, auto-approve, or always-deny handlers
- **AI guidance** — error responses include `retryable`, `ai_guidance`, `user_fixable`, and `suggestion` fields for agent consumption
- **AI intent metadata** — tool descriptions enriched with `x-when-to-use`, `x-when-not-to-use`, `x-common-mistakes`, `x-workflow-hints` from module metadata
- **Extension helpers** — modules can call `report_progress()` and `elicit()` during execution for MCP progress reporting and user input
- **Annotation mapping** — apcore annotations (readonly, destructive, idempotent) map to MCP ToolAnnotations
- **Schema conversion** — JSON Schema `$ref`/`$defs` inlining, strict mode for OpenAI Structured Outputs
- **Error sanitization** — ACL errors and internal errors are sanitized; stack traces are never leaked
- **Dynamic registration** — modules registered/unregistered at runtime are reflected immediately
- **Dual output** — same registry powers both MCP Server and OpenAI tool definitions
- **Tool Explorer** — browser-based UI for browsing schemas and testing tools interactively, with Swagger-UI-style auth input
- **Config Bus integration** — registers an `mcp` namespace with the apcore Config Bus; configure transport, host, port, and more via unified `apcore.yaml` or `APCORE_MCP_*` env vars
- **Error Formatter Registry** — registers an MCP-specific error formatter for ecosystem-wide consistent error handling

## Config Bus Integration

apcore-mcp registers an `mcp` namespace with the apcore Config Bus at import time. This means MCP settings can live alongside other apcore configuration in a single `apcore.yaml`:

```yaml
apcore:
  version: "1.0.0"
mcp:
  transport: streamable-http
  host: 0.0.0.0
  port: 9000
  explorer: true
  require_auth: false
```

Environment variable overrides use the `APCORE_MCP_` prefix:

```bash
APCORE_MCP_TRANSPORT=streamable-http
APCORE_MCP_PORT=9000
APCORE_MCP_EXPLORER=true
```

**Defaults:** `transport=stdio`, `host=127.0.0.1`, `port=8000`, `explorer=false`, `require_auth=true`.

The namespace, prefix, and defaults are also available as importable constants:

```python
from apcore_mcp import MCP_NAMESPACE, MCP_ENV_PREFIX, MCP_DEFAULTS
```

## How It Works

### Mapping: apcore to MCP

| apcore | MCP |
|--------|-----|
| `metadata["display"]["mcp"]["alias"]` or `module_id` | Tool name |
| `metadata["display"]["mcp"]["description"]` + guidance suffix or `description` | Tool description |
| `input_schema` | `inputSchema` |
| `annotations.readonly` | `ToolAnnotations.readOnlyHint` |
| `annotations.destructive` | `ToolAnnotations.destructiveHint` |
| `annotations.idempotent` | `ToolAnnotations.idempotentHint` |
| `annotations.open_world` | `ToolAnnotations.openWorldHint` |

### Mapping: apcore to OpenAI Tools

| apcore | OpenAI |
|--------|--------|
| `module_id` (`image.resize`) | `name` (`image-resize`) |
| `description` | `description` |
| `input_schema` | `parameters` |

Module IDs with dots are normalized to dashes for OpenAI compatibility (bijective mapping).

### Architecture

```
Your apcore project (unchanged)
    │
    │  extensions directory
    ▼
apcore-mcp (separate process / library call)
    │
    ├── MCP Server path
    │     SchemaConverter + AnnotationMapper
    │       → MCPServerFactory → ExecutionRouter → TransportManager
    │
    └── OpenAI Tools path
          SchemaConverter + AnnotationMapper + IDNormalizer
            → OpenAIConverter → list[dict]
```

## Structured MCP results (0.23.0, pending publication)

The common structured-output validation subset is Draft-07, Draft 2019-09 and
Draft 2020-12 (default). Mixed-draft embedded schemas and other declared drafts
are rejected rather than reinterpreted; repeating the same draft is allowed.
Numeric JSON text is promoted only when parsing and serialization preserve its
decimal value. Rounded/underflowed values return a payload-free error when an
output schema is advertised, otherwise the original text remains unchanged.
Runtime integer ranges differ; use JSON strings for portable arbitrary-size IDs.

This capability is prepared in the 0.23.0 development source. Publication awaits the corrected apcore dependency; published 0.22.3 does not include it.

For clients negotiating MCP 2025-06-18 or a later supported version, object output schemas are exposed as `Tool.outputSchema`. Successful calls return `structuredContent` parsed from the primary, already-redacted JSON text, while retaining the original text and any additional trace content. The bridge validates that object against an independent snapshot of the advertised schema, including references and constraints, after redaction; mismatches return a payload-free `isError` result. Validation follows the declared JSON Schema dialect (Draft 2020-12 by default), resolves local references and anchors, and never fetches external schema URLs. References in inactive branches and unused definitions must also resolve locally; literal JSON defaults and examples are preserved. Non-finite numbers, including exponent overflow, cannot become structured JSON. Non-object results and custom text formats remain text-only when no object output schema is advertised. Async task submission tools omit the final module output schema.

Clients negotiating 2025-03-26 or 2024-11-05 retain text-only tools and results. Stdio and SSE use the negotiated session version; Streamable HTTP uses the trusted `MCP-Protocol-Version` header on each request, defaulting to 2025-03-26 when absent. Client-supplied request metadata cannot enable structured output.

## Development

```bash
git clone https://github.com/aiperceivable/apcore-mcp-python.git
cd apcore-mcp-python
pip install -e ".[dev]"
pytest                           # full test suite
pytest --cov                     # with coverage report
```

## License

Apache-2.0
