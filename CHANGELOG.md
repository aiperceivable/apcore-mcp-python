# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.22.1] - 2026-09-24

Bugfix release, Python-only — the 0.22.0 `$ref` sibling-key fix itself had a regression that a
code-review pass caught before release. No contract change; `apcore-mcp-rust` and
`apcore-mcp-typescript` needed no equivalent fix (see **Fixed**). 1048 tests pass (was 1046).

### Fixed

- **`SchemaConverter._inline_refs` raised `TypeError` when a `$ref` resolved to a non-dict JSON
  Schema value**, e.g. a boolean schema declared as `"$defs": {"Anything": true}` — a valid JSON
  Schema construct. 0.22.0's sibling-key merge did `merged = dict(inlined)` unconditionally, and
  `dict(True)` raises `TypeError: 'bool' object is not iterable`. This is a regression versus both
  the pre-0.22.0 behaviour (which returned a non-dict `$ref` target unchanged) and the Rust and
  TypeScript bridges, whose 0.22.0 implementations already guarded this case (Rust:
  `match inlined { Value::Object(m) => m, other => ... }`; TypeScript:
  `typeof inlinedTarget === "object" && !Array.isArray(inlinedTarget) ? {...} : {}`). Fixed to
  return the resolved value unchanged when the `$ref` node has no sibling keys, and to fall back to
  an empty `dict` base — rather than raising — when siblings are present alongside a non-dict
  target, matching the other two bridges. New regression tests in `tests/adapters/test_schema.py`:
  `test_ref_to_boolean_schema_without_siblings`, `test_ref_to_boolean_schema_with_sibling`.

## [0.22.0] - 2026-09-24

Raises the required floor to `apcore` 0.31.0 and `apcore-toolkit` 0.12.0, and fixes a
credential-disclosure defect found while reviewing what those two releases changed. Released in step
with `apcore-mcp-rust` 0.22.0 and `apcore-mcp-typescript` 0.22.0. 1046 tests pass (was 1037).

### Security

- **`$ref` sibling keys were discarded during `SchemaConverter` inlining, dropping `x-sensitive`.**
  `SchemaConverter._inline_refs` (`src/apcore_mcp/adapters/schema.py`), on encountering a node like
  `{"$ref": "#/$defs/Token", "x-sensitive": true}`, took the `$ref` branch and returned *only* the
  resolved `$defs` entry — every key written beside `$ref` (`x-sensitive`, `description`,
  `deprecated`, ...) was silently dropped. This is a credential-disclosure path, not a fidelity
  nicety: `ExecutionRouter`'s output redaction (`src/apcore_mcp/server/router.py::_maybe_redact`)
  reads `x-sensitive` off the *resolved* output schema, via `apcore.redact_sensitive`, to decide what
  to mask. A sensitive field sitting behind a `$ref` reached the redactor with nothing to redact on
  and came back in plaintext.

  `_inline_refs` now resolves the `$ref` target, recursively inlines refs within it, and
  shallow-merges the node's own sibling keys **over** the resolved-and-inlined result — sibling
  winning on key conflict, siblings that are themselves subschemas independently walked for their own
  nested `$ref`s, and a chained `$ref`-to-`$ref` carrying siblings contributed at each hop with the
  outermost sibling winning. A `$ref` naming a definition absent from `$defs` still raises `KeyError`
  unchanged — sibling merging only applies once a reference resolves. See
  [`docs/features/schema-converter.md#ref-sibling-keys-are-preserved`](https://github.com/aiperceivable/apcore-mcp/blob/main/docs/features/schema-converter.md#ref-sibling-keys-are-preserved)
  in the spec repo.

  Found by reviewing what apcore 0.31.0 (decision D-98/D-124) and apcore-toolkit 0.12.0 changed: both
  fixed the identical defect in their own `$ref` resolvers. `SchemaConverter._inline_refs` is a fully
  independent implementation with no shared code path to either, so it was not fixed by the dependency
  floor raise below and carried the same latent bug on its own.

### Tests

- `tests/test_schema_converter_conformance.py` (9 cases: 8 `test_cases` + 1 `error_cases`) — loads
  the new shared fixture `schema_converter.json` via `tests/conformance_fixtures.py`, following the
  same pattern as `test_output_redaction_conformance.py`. Drives
  `SchemaConverter.convert_input_schema(descriptor, strict=False)` against a stand-in descriptor;
  `strict=False` keeps the already-pinned `additionalProperties` injection out of the expected
  output. Confirmed to fail against the pre-fix `_inline_refs`.

### Changed — dependency floor

- **Required `apcore` floor raised to 0.31.0** (was `>=0.30.0`) and **required `apcore-toolkit`
  floor raised to 0.12.0** (was `>=0.11.1`, across the `markdown`, `openapi` and `dev` extras).
  apcore 0.31.0 is two joined audit cycles (`PROTOCOL_SPEC` v1.37.0 → v1.59.0) settling 54
  cross-language divergences, five of them security defects — none on a surface this package uses.
  Every `apcore.*`/`apcore_toolkit.*` symbol this package imports (`ACL`/`ACLRule`,
  `Context`/`Identity` via plain construction rather than `ContextFactory.create_context`,
  `Registry`, `Module`, `ModuleError`, `redact_sensitive`, the async task manager,
  `OpenAPIScanner`/`load_spec`, `HTTPProxyRegistryWriter`) was grepped against both changelogs'
  breaking-change sections; nothing on that surface changed. apcore-toolkit 0.12.0 adds the Device
  Authorization Flow (RFC 8628, unused here) and `BindingLoader.load`'s `pattern` parameter
  (`BindingLoader` is not used by this package); its own required-apcore-floor bump to 0.31.0 is
  inherited transitively. Confirmed after running the full suite: no other code needed to change for
  the floor raise itself — the fix above is an independent, unrelated defect found while reviewing
  the two changelogs, not a consequence of the version bump.
- The monorepo-root `uv.lock` already resolved `apcore` and `apcore-toolkit` to 0.31.0/0.12.0 via
  their workspace-editable local paths before this change (they carry no version specifier in the
  lock's `requires-dist` — workspace sources bypass it entirely), so it needed no regeneration. This
  package has no lock file of its own.

## [0.21.0] - 2026-09-07

No behaviour change. Released in step with `apcore-mcp-rust` 0.21.0 and
`apcore-mcp-typescript` 0.21.0, which fix three classes of OpenAPI-backend defect — filed as four
issues — that this package never had; Python is the reference implementation for each. Version
parity across the three bridges is kept so a deployment can pin one number.
1037 tests pass (was 1029).

### Added

- `tests/test_openapi_option_plumbing.py` (8 tests) — pins the OpenAPI backend's option plumbing:
  `headers` and `timeout` reaching the spec fetch, `timeout` being seconds and spec-fetch-only
  (proxied calls stay on apcore-toolkit's 60 s default), and `Config.project_root` being consulted
  for a relative `spec`. No behaviour changed here — Python is the reference implementation for all
  three, and the Rust and TypeScript bridges were fixed against it
  ([apcore-mcp-rust#8](https://github.com/aiperceivable/apcore-mcp-rust/issues/8),
  [#9](https://github.com/aiperceivable/apcore-mcp-rust/issues/9),
  [apcore-mcp-typescript#10](https://github.com/aiperceivable/apcore-mcp-typescript/issues/10),
  [apcore-mcp#19](https://github.com/aiperceivable/apcore-mcp/issues/19)). These tests exist so it
  cannot drift into the same shape: the conformance suite hands the backend an already-parsed
  document and calls `resolve_spec_location` directly with an explicit `project_root`, covering the
  pure functions and never the wiring between them. Confirmed to fail when each defect is injected.


## [0.20.0] - 2026-09-06 

Bugfix release from a `/apcore-skills:sync` pass across all three bridges. 0.20.0's tests all passed
and its features work as documented — this release closes gaps between what shipped and what the
PRD/SRS actually promise, found by re-verifying documented claims against source directly rather than
against the 0.20.0 session's own narrative. 1029 tests pass (was 1015).

### Fixed

- **`mcp.openapi.spec` set on the Config Bus alone now starts a server (PRD F-054 Acceptance
  Criterion 1).** Before this release, `mcp.openapi` was registered as a Config Bus namespace default
  — the key round-tripped — but nothing ever read it back. Only `--from-openapi` and explicit
  `from_openapi()`/`openapi_backend()` calls worked, contradicting the PRD's explicit "`mcp.openapi.spec`
  ... starts a server" and its Description listing the Config Bus as one of three routes.
  `extensions_dir_or_backend` is now optional on `APCoreMCP.__init__`, `serve()`, `async_serve()`,
  `to_openai_tools()`, and `MCPServer.__init__`; when omitted, the backend resolves from
  `mcp.openapi` alone, raising `ValueError` when neither a backend nor `mcp.openapi.spec` is given.
  When both an explicit backend and `mcp.openapi` are configured, the two are unioned (backend
  first, OpenAPI layered on top) and `mcp.openapi.prefix` becomes required, exactly as the CLI's
  own two-flag combination already required. New `build_openapi_backend_from_config` helper
  (mirrors `acl_builder.build_acl_from_config`). The CLI's own backend-source check now also
  accepts "neither flag, but `mcp.openapi` is configured" instead of exiting 2 before ever reaching
  this path.

- **§6.2.1 tier-2 ACL diagnostic (FR-ACL-004) read the wrong field names — it fired, but every
  warning rendered as `mcp.acl.rules[0] '?': `.** `ACL.validate_rules()`'s real
  `RuleValidationFinding` shape is `{rule_index, condition_path, condition_key, effect,
  sync_resolvable, async_resolvable}` — verified directly against a live `ACL` instance. This
  function read `path`/`reason`/`message`, none of which exist, so every `getattr(..., default)`
  silently returned the default and the warning carried no actionable content. `serve()` calling
  it every startup and it firing correctly on paper is not the same claim as it firing usefully;
  the second was false. Now reads the real fields and constructs the explanatory sentence itself
  (apcore hands back structured data only, no free-text reason).

- **`mcp.openapi.acknowledge_unapproved_writes` was documented as the suppression for the
  "nothing will ask for approval" warning (FR-OPENAPI-005) in the warning's own message text, but
  nothing ever read it.** Setting it exactly as instructed had no effect. `openapi_backend` now
  accepts `acknowledge_unapproved_writes: bool = False` and skips the warning when true;
  `build_openapi_backend_from_config` reads it from the Config Bus mapping.

### Tests

- `tests/test_openapi_config_bus_wiring.py` (9 cases) — the Config-Bus-only backend, the
  union-plus-prefix-required interaction, and `acknowledge_unapproved_writes` suppression.
- `tests/test_cli.py` — 2 new cases for the relaxed backend-source rule.
- `tests/test_acl_tier2_warning.py` (3 cases) — the corrected field names, pinned against a real
  `apcore.ACL` instance; asserts the exact string `'?'` never appears, which is what the bug
  produced.

### Documentation

- `docs/features/openapi-backend.md`'s `## Contract: openapi_backend` Returns section and
  `docs/srs-apcore-mcp.md`'s FR-OPENAPI-001 both claimed registered-module `metadata` visibility
  (`http_method`/`url_path`/`openapi.*` via `get_definition`) as universal. Measured directly: only
  Rust's writer builds a full `ModuleDescriptor` and preserves it; Python's and TypeScript's writers
  both call the 2-arg `register(id, module)` form and drop it — an upstream (apcore-toolkit)
  inconsistency this bridge cannot fix, now stated as Rust-only in both documents and in
  `conformance/fixtures/openapi_backend.json`'s notes, and corrected in this repo's own 0.20.0 entry
  below (which had stated the gap as universal).
- `docs/srs-apcore-mcp.md`'s FR-OPENAPI-002 `####` heading read "projects unchanged onto both
  protocol surfaces", contradicting its own Description and Boundary Conditions, which document a
  projection step that changes the ID. Corrected to "is projected, then reaches both protocol
  surfaces unchanged".

Feature release: the **OpenAPI backend** — point the bridge at an OpenAPI 3.0/3.1 document and every
operation becomes an MCP tool, proxied over HTTP, with no apcore project on the other end — plus the
`mcp.acl` half of apcore 0.29.0's PROTOCOL_SPEC §6.2.1 pattern-array closure. Raises the required
`apcore` floor to `0.30.0` and the `apcore-toolkit` floor to `0.11.1`. 1015 tests pass.

### Added

- **`apcore_mcp.openapi_backend` — a third backend source.** `openapi_backend(spec, **options)`
  composes apcore-toolkit's shipped pieces (`load_spec` → `OpenAPIScanner.scan` →
  `HTTPProxyRegistryWriter.write`) into a populated `Registry` and hands it to the machinery this
  bridge already has: no scanning logic, no schema conversion and no new execution path.
  `APCoreMCP.from_openapi(spec, **options)` is the convenience constructor; `mcp.openapi` is the new
  Config Bus section; and the CLI gains `--from-openapi`, `--openapi-base-url`, `--openapi-prefix`,
  `--openapi-include`, `--openapi-exclude`, `--openapi-header` and `--openapi-no-deprecated`.
  New `[openapi]` extra: `pip install 'apcore-mcp[openapi]'` (it resolves `apcore-toolkit[http-proxy]`,
  which brings `httpx`; a missing toolkit raises with that install line rather than an `ImportError`).

- **A module-ID projection, without which the backend serves nothing.** apcore-toolkit's
  `derive_module_id` sanitizes to `[A-Za-z0-9_.-]`; apcore's registry accepts only
  `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$` and enforces it at `Registry.register` **and** at
  `Executor.call`. Of nine realistic operation shapes only two register unrepaired, and the
  canonical Swagger Petstore (`listPets`, `createPets`, `showPetById`) is entirely in the rejected
  set — measured end-to-end, it scans cleanly, fails registration on every operation as a per-module
  `WriteResult`, and yields an **empty registry**. `project_module_id` lowercases and maps `-` to
  `_`; a segment that still does not begin with a lowercase letter (`/v1/2fa`) is **skipped with a
  WARNING** rather than repaired, because completing it means inventing a character — a naming
  decision that belongs to the operator's own `derive_module_id` / `transform_module` hook. The
  projection runs after any caller hook (so *every registered ID is apcore-legal* holds
  unconditionally) and before the scanner's `deduplicate_ids` (because lowercasing can create a
  collision the document did not have).

- **A pre-write collision preflight.** The full derived-ID set is intersected against the target
  registry before `HTTPProxyRegistryWriter.write` is called; a non-empty intersection fails startup
  naming **every** colliding ID, having written nothing. Without it a duplicate arrives as one
  failed `WriteResult`, is logged at ERROR, and leaves a **partial registry** — a tool the document
  advertises, absent from `tools/list`, with one log line as the only notice. `prefix` is separately
  mandatory when the OpenAPI backend is combined with another backend source; it is naming hygiene,
  not the collision defence.

- **A startup warning that nothing will ask for approval before a write.** The toolkit infers
  annotations from the HTTP method alone and never infers `requires_approval`, so every scanned
  module arrives with it `False`: a `POST /charges` that moves money is annotated exactly like a
  `POST /echo`. The warning reports the **absence of an approval path, never the presence of
  protection** — the rule apcore states on `GovernanceState.unprotected_control_surface` (*"a wired
  ACL that permits every call still yields False"*) — so an attached ACL does **not** suppress it.

- **`_warn_acl_rules_that_protect_nothing` — the §6.2.1 tier-2 startup diagnostic.** `serve()` /
  `async_serve()` now call `ACL.validate_rules()` once the registry is assembled and log each
  finding at WARNING with the rule index and field. Closing the pattern-array *shape* does not
  exhaust the inert class: `["$not", "*"]` has legal arity, exactly one operand, and matches
  nothing — the identical fail-open through a well-formed array. Advisory only, on the same terms as
  the unprotected-control-surface check beside it. No bridge called `validate_rules()` before this.

### Changed

- **`build_acl_from_config` validates in PROTOCOL_SPEC §6.2.1's normative order.** `effect` →
  `approval` → `callers` → `targets`, with `default_effect` ahead of the rule loop and the
  unknown-key check ahead of all four. This builder ran it in reverse, so a rule wrong in both
  `effect` and `callers` was refused for `callers` here and for `effect` by apcore's own doors — the
  same file, two answers, depending on which door it reached first.

- **Required floors: `apcore>=0.30.0`, `apcore-toolkit>=0.11.1`.** apcore **0.29.0** is the
  correctness floor — on 0.28.0 the pattern-array shapes below load silently and the deployment
  believes it has a rule it does not have. apcore-toolkit **0.11.0** is the capability floor
  (`OpenAPIScanner` does not exist below it, and it is where the Rust writer stopped rejecting
  `HEAD`/`OPTIONS`/`TRACE`). apcore-toolkit **0.11.1** changes no API and exists to raise its own
  apcore floor, which forces apcore **0.30.0** — independently needed for `Config.project_root`.

### Fixed

- **`ACLRuleError` escaped `build_acl_from_config` raw, in the wrong type and without the rule
  index.** apcore raises it from inside `ACLRule.__post_init__` and its message names the *type* —
  `ACLRule has an invalid 'targets' (PROTOCOL_SPEC §6.2.1): …` — which is correct for apcore, where
  a rule under construction has no position, and useless to an operator holding a 20-rule YAML block
  when the §6.2.1 closure's entire remedy is its message. It is also the wrong type: `ACLRuleError`
  extends `ModuleError`, not `ValueError`, so this function's documented "raises ValueError on
  malformed entries" contract was false for every §6.2.1 fault and a caller with `except ValueError:`
  around startup silently stopped catching them. Now re-raised as `ValueError` prefixed
  `mcp.acl.rules[i]` (`mcp.acl` for a section-scoped fault), apcore's message preserved verbatim
  after the prefix and the original chained as `__cause__`.

- **`mcp.openapi.spec` is path-typed and apcore 0.30.0's protections do not reach it.** Measured:
  `Config.path_typed_keys()` returns a hardcoded tuple of apcore's own four keys and never consults
  a namespace registered through `Config.register_namespace`, and the §9.2.1 requirement-5
  empty-value discard is gated on that same set. So `APCORE_MCP_OPENAPI_SPEC=` would be an override
  to `""`, resolving to the working directory — exactly the silent failure apcore closed for
  `APCORE_ACL_ROOT=`, in a namespace the fix does not extend to. `resolve_spec_location` owns three
  rules instead: an `http(s)://` value verbatim, a set-but-empty value discarded with a WARNING so
  resolution falls through, and a relative path resolved against `Config.project_root` — §9.2.2's
  *target* semantics, adopted immediately because this key has never shipped and owes no deprecation
  window.

### Upstream gaps found while implementing (reported, not worked around)

- **`OpenAPIScanner` + `HTTPProxyRegistryWriter` cannot serve the reference spec the scanner was
  verified against.** They are documented as an end-to-end pair; that verification asserted
  byte-identical `ScannedModule` output across SDKs, which never exercised registrability. The
  projection above is this bridge's local repair; the fix belongs in apcore-toolkit.
- **`ScannedModule.metadata` never reaches the registered descriptor — in Python and TypeScript.**
  This writer calls `registry.register(mod.module_id, module_instance)` with no `metadata=`, even
  though apcore's `register` accepts one, and apcore exposes no way to attach metadata afterwards
  (`get_module_metadata` reads; nothing writes). The proxy routes correctly — it closes over the
  values — but `http_method` / `url_path` are invisible to `get_definition`, and therefore to the
  MCP `_meta` projection. **Not universal**: apcore-toolkit-rust's writer builds a full
  `ModuleDescriptor` (required by apcore-rust's `Registry::register` signature) and copies
  `metadata` into it, so the Rust bridge's registered modules *do* carry it. An earlier draft of
  this note (and the shared fixture) stated the gap as language-independent; corrected after
  verifying all three writers directly.

### Tests

- `tests/test_openapi_backend_conformance.py` drives the new shared fixture
  `openapi_backend.json` (9 module cases + 3 spec-resolution cases + 4 error cases).
- `tests/test_acl_conformance.py` drives `acl_config.json` at `contract_version` 1.2 (32 cases),
  and understands the 1.2 additions `expected_error_substrings`, `expected_error_names_field` and
  `must_not_contain`.
- `test_rule_approval_required_on_deny_raises` is renamed and reversed: it asserted a raw
  `ACLRuleError` escaping, which is the behaviour this release changes. Its docstring quotes the old
  claim so the reversal is legible.


## [0.19.0] - 2026-09-01

Aligns `apcore-mcp` with the `system.*` management-surface contract (aiperceivable/apcore-mcp#14, #15, #16 Phase A; aiperceivable/apcore-mcp-python#8) and bumps the required `apcore` floor to `0.28.0` and `apcore-toolkit` floor to `0.10.2`.

### Added

- **`system.health.*` / `system.usage.*` / `system.manifest.*` are now projected as MCP resources instead of tools (aiperceivable/apcore-mcp#15).** `system.control.*` is unaffected and keeps its normal Tool projection — it performs writes. `MCPServerFactory.build_tools()` skips the read-only three via the new `is_readonly_system_module()` predicate (prefix-only classification, no adapter-level toggle); `register_resource_handlers()` (now taking an optional `router` argument) projects `system.health.summary` / `system.usage.summary` / `system.manifest.full` as static `apcore://` resources and `system.health.module` / `system.usage.module` / `system.manifest.module` as resource *templates* (`resources/templates/list`) parameterized by `{module_id}`. `system.usage.summary` / `system.usage.module` additionally accept an optional `?period=` query parameter on read. Every `apcore://system.*` read dispatches through `ExecutionRouter.handle_call` — never directly against the registry — so ACL and audit apply identically to `tools/call` and `resources/read`. A resource/template is registered only for a module id the registry actually holds.
- **`Executor.governance_state()`-backed startup warning for an unprotected `system.control.*` surface (aiperceivable/apcore-mcp#15(b)).** `serve()` / `async_serve()` / `MCPServer` (all funnel through `APCoreMCP._build_server_components()`) now call the apcore 0.28.0 `governance_state()` read-only accessor once the executor is fully assembled and log a multi-line WARNING — naming exactly which of ACL / approval handler / per-module `requires_approval` is missing, with concrete fix suggestions — when `unprotected_control_surface` is true. Advisory only; the server still starts. Executors that predate `governance_state()` are silently skipped.
- **`com.aiperceivable/management` extension capability broadcast at `initialize` (aiperceivable/apcore-mcp#16 Phase A).** `MCPServerFactory.build_init_options()` takes an optional `management_surfaces={"health": bool, "usage": bool, "manifest": bool, "control": bool}`; when any is true, `capabilities.extensions["com.aiperceivable/management"] = {"surfaces": [...], "protocolVersion": "1.30.0"}` is added, `surfaces` listing only the true keys in stable order. `APCoreMCP._build_server_components()` computes the four booleans from an unfiltered registry scan. Purely additive discovery — a client that ignores the capability still gets full, unaffected access to every tool and resource.
- **ACL rules may now carry `approval: "required"` via the Config Bus `mcp.acl` YAML path (aiperceivable/apcore-mcp#14).** `acl_builder._ALLOWED_RULE_KEYS` was a closed set that did not include apcore 0.28.0's new `ACLRule.approval` field (argument-scoped approval, spec §6.1.6), so any rule written with `approval: required` was rejected with "unexpected keys" before it ever reached `ACLRule` — the feature was unreachable from YAML. `approval` (`"required"` / `"not_required"`) is now accepted, validated, and passed through to `ACLRule`; `ACLRule.__post_init__`'s own `deny` + `approval: required` rejection is left in place rather than duplicated.

### Changed

- **Required `apcore` floor raised to `>=0.28.0`.** Brings in `Executor.governance_state()` (used above), `ACLRule.approval` (used above), and several ACL-evaluation hardening fixes (an unevaluable `deny` condition now denies instead of silently passing through; a rule's `effect` outside `allow`/`deny` now fails at construction instead of being silently read as `deny`). Neither required an `apcore_mcp` code change: `acl_builder.build_acl_from_config` already rejected a non-list `callers`/`targets` and an `effect` outside `{"allow", "deny"}` at the Config Bus YAML layer before constructing `ACLRule`, ahead of apcore's own hardening. Also brings in apcore 0.28.0's now-enforced dict-schema input validation for the nine `system.*` modules and the `system.usage.*` `period` pattern constraint — both apcore-side runtime behavior changes with no `apcore_mcp` code required.
- **Required `apcore-toolkit` floor raised to `>=0.10.2`** (`[markdown]` extra and `dev` extra). Confirmed against the apcore-toolkit 0.10.2 changelog: no code or API changes to `format_module`, `format_csv`, `format_jsonl`, or `ScannedModule` — this package's `markdown.py` and `router.py` call sites are unaffected.
- **`acl_builder.py`'s module docstring example replaced.** The prior example used the wrong `sys.*` / `sys.reload` / `sys.toggle` namespace (aiperceivable/apcore-mcp#14) — the real namespace is `system.*` (`system.health.*`, `system.usage.*`, `system.manifest.*`, `system.control.*`). Replaced with a three-rule worked example (read-only allow, control allow, catch-all deny) plus the two load-bearing mechanics it depends on: every MCP call's `caller_id` is `null` and normalizes to `@external`, so `callers` alone can never distinguish a console user from an agent — only `conditions` reading JWT `identity_types`/`roles` can; and rules evaluate first-match-wins, so a narrower `allow` must precede a broader `deny`.
- **`README.md` and `acl_builder.py`'s module docstring now document the unprotected-management-surface gap explicitly**: enabling apcore's `sys_modules.enabled` without a configured `mcp.acl` (or `acl=`) leaves every `system.*` module — including `system.control.*` — reachable by any caller, because `ACL.discover()` returns `None` for a missing `acl/` directory, indistinguishable from "no ACL was ever configured."

### Fixed

- **`system.usage.module`'s resource template was missing the `{?period}` query-expansion suffix.** `register_resource_handlers()` built every `.module` template's `uriTemplate` from one generic f-string, so `system.usage.module` came out as `apcore://system.usage.module/{module_id}` — the same shape as the two templates that take no query parameter — instead of the `apcore://system.usage.module/{module_id}{?period}` aiperceivable/apcore-mcp#15's URI-convention table specifies and apcore-mcp-typescript's `systemResourceUriTemplate()` already emitted. Caught by the new cross-language `system_surface.json` conformance fixture below, which is exactly what it exists to catch: TypeScript had it right and Python (and Rust) had silently drifted. `period` was already parsed and forwarded correctly at read time (`_read_system_resource`) — only the *declared* template string was wrong.

### Tests

- New `tests/test_management_surfaces.py`: unit tests for the surface-detection scan and the governance-state warning (including "no `governance_state()` method" and "`governance_state()` raises" fallback paths), plus end-to-end cases against a real `apcore.Registry` / `apcore.Executor` / `register_sys_modules()`.
- `tests/server/test_factory.py`: new coverage for `is_readonly_system_module()`, `build_tools()` excluding the read-only three, the full `apcore://` resource/template lifecycle (list, read, missing-`module_id`, unknown-resource, router-error, no-router, module-absent-from-registry cases), and the `com.aiperceivable/management` capability (single/partial/all-false/stable-order cases).
- `tests/test_acl.py`: new coverage for the `approval` rule field (accepted, default-preserving, invalid-value rejection, `deny`+`required` rejection) and a regression test asserting every `targets` prefix in the acl_builder docstring's worked example matches at least one module id actually registered by `apcore.sys_modules.registration.register_sys_modules`.
- New `tests/test_system_surface_conformance.py`: drives the shared `apcore-mcp/conformance/fixtures/system_surface.json` fixture against a real `register_sys_modules()`-populated registry, asserting `build_tools()` / `register_resource_handlers()` output matches byte-for-byte — the TypeScript and Rust bridges run the identical fixture, operationalizing aiperceivable/apcore-mcp#15's cross-language parity acceptance criterion as a regression test instead of a one-time manual check.
- **New `tests/test_acl_approval_gating_e2e.py`.** Every other `approval` test in this repo stopped at the Config Bus parsing layer — they proved the key was *accepted*, not that it *did* anything, leaving the argument-scoped-approval feature unverified at the MCP boundary. These drive a real `apcore.Executor` (real `ACL`, real approval handler, real module) through `ExecutionRouter.handle_call`, with the module's own `requires_approval` annotation set to **false** so the ACL rule is the only possible source of the requirement, and an argument-scoped condition (`conditions.arguments.has_key`) deciding it: a call carrying `recursive` reaches the approval handler, an otherwise identical call without it does not.
- The shared `acl_config.json` fixture gained `approval` contract cases (see the spec repo's 0.19.0 entry). This bridge already accepted apcore's full value set (`"required"` / `"not_required"`) and needed no change; the fixture is what now prevents the Rust and TypeScript bridges from independently narrowing it again, as both had done.

## [0.18.1] - 2026-08-20

Patch release. Bumps the required `mcp-embedded-ui` floor to `>=0.5.0` (was `>=0.4.0`). No `apcore_mcp` code changes — `create_mount` is called exactly as before. All 920 tests pass unmodified against mcp-embedded-ui 0.5.0.

### Changed

- **Required `mcp-embedded-ui` floor raised to `>=0.5.0`.** A consumer who mounts the Explorer inherits mcp-embedded-ui 0.5.0's Try-It editor prefill change (spec F6/FR-1): the prefill now emits only the keys listed in `inputSchema.required`, using each property's declared `default` when present and `null` otherwise, instead of inventing a type-based value (`""`, `0`, ...) for every property.

### Fixed

- Inherited from mcp-embedded-ui 0.5.0: `/validate` (F7) no longer returns HTTP 500 for a tool whose `inputSchema` cannot be compiled — it now reports a single `keyword: "schema"` validation failure at HTTP 200.
- Inherited from mcp-embedded-ui 0.5.0: `project_url` is now scheme-checked (`http://`, `https://`, `mailto:`, or a leading `/`) before being placed in `href` on the Explorer page.

## [0.18.0] - 2026-08-19

### Security

- **`__apcore_module_preview` no longer discloses module introspection to a caller the ACL denied.** The bridge serialises `Executor.validate()`'s `PreflightResult` verbatim (`_preflight_to_dict` copies `predicted_changes` and `checks` straight through), and apcore `<=0.26.0` gated `Module.preflight()` / `Module.preview()` on module lookup alone — pipeline Step 3 — while the ACL check is Step 4. A denied caller therefore ran module-authored code and received what it returned: for a command-wrapping module the resolved binary and its argv, for a writer the target of the side effect. Raising the `apcore` floor to `>=0.27.0` closes it at the layer that owns the gate (PROTOCOL_SPEC §12.8.5.1, spec v1.13.0, apcore#96); no bridge code changed. A denied caller still receives the failed `acl` check, so it still learns why. Pinned by `tests/server/test_preflight_disclosure.py`, which drives a real `Executor` over a real `Registry` and a real `ACL` and asserts a sentinel binary path and argv appear nowhere in the denied envelope — 5 of its 8 cases fail against apcore 0.26.0.

### Changed

- **Required `apcore` floor raised to `>=0.27.0`.** Of the 0.27 breaking changes, only the `validate()` disclosure gate above reaches this package: the bridge builds no pipeline from YAML, does not use `SchemaValidator`'s coercion knob, does not configure `obs.redaction`, and registers no `StepMiddleware`.

## [0.17.2] - 2026-07-14

Patch release. Fixes the MCP elicitation approval flow and bumps the required `apcore` floor to `0.26.0` (Execution Policy §7.9 / governance events / no-handler fail-loud — additive, no breaking changes). All 856 tests pass (3 new).

### Fixed

- **`ElicitationApprovalHandler` now sends a non-empty elicitation `requestedSchema`.** The approval elicitation was previously sent with an empty `{}` schema; minimal SDK clients tolerate this, but clients that render an approval form (Cursor, Codex, ...) ignore or reject an empty schema, so the request returned no response and the gate failed closed ("Elicitation returned no response"). The handler now sends an object schema with a boolean `approve` field and honors an explicit `approve: false` from the form.
- **Elicitation failures are no longer swallowed silently.** The `ExecutionRouter` elicit callback and the approval handler now log at `warning` (was `debug`) with the traceback, so a failing elicitation surfaces instead of silently denying.

### Changed

- **Required `apcore` floor raised to `>=0.26.0`** to align the ecosystem on the 0.26.0 governance layer. Additive on the apcore side; all existing tests pass unmodified.

## [0.17.1] - 2026-07-07

Patch release. Bumps the required `apcore-toolkit` floor to `0.10.0` (which adds the shared annotation-preservation conformance verifier and centralizes the Python `RegistryWriter` — additive, no breaking changes). No code or API changes; all 853 tests pass unmodified against apcore-toolkit 0.10.0.

## [0.17.0] - 2026-06-23

Audit-driven hardening of the serve/embed entry points and the Phase B approval
chain, plus the apcore 0.25 / apcore-toolkit 0.9.1 dependency uplift.

### Added

- **Top-level `serve()` / `async_serve()` now forward `approval_store` /
  `approval_notify`** to `APCoreMCP`, so Phase B async approval is configurable
  from the top-level entry points (previously only via the `APCoreMCP` class).
- **Non-blocking `MCPServer` reached parity with `serve()` / `APCoreMCP`**: it now
  forwards `output_formatter` / `output_format`, `strategy`, `observability`,
  `redact_output`, `trace`, explorer branding, approval (`handler` / `store` /
  `notify`), `middleware`, `acl`, `dynamic`, and shared input validation, via a
  new shared `APCoreMCP._build_serve_coro()` assembly helper (also used by the
  blocking `serve()`, removing the duplicated pipeline).

### Fixed

- **Approval-bridge gating was too strict**: `ApprovalBridge` (and the
  `__apcore_approval_check` meta-tool) is now registered whenever an
  `approval_handler` is present, no longer requiring `approval_store` as well.
  Passing a `StorageBackedApprovalHandler(store)` directly as `approval_handler`
  now registers the meta-tool and starts/stops the store lifecycle end-to-end.
- **TM-4 session-scoped task cancellation was dead code**: `_scoped_session` and
  `set_async_task_bridge` were never invoked by the transports, so
  `transport_session_var` stayed `None` and client disconnects never fired
  `cancel_session_tasks`. Wired `_run_scoped()` into all four transport entry
  points (`run_stdio`, `run_streamable_http`, `run_sse`,
  `build_streamable_http_app`); disconnect-cancellation now actually fires.

### Changed

- Unified explorer-branding defaults between the top-level and instance
  `serve()` / `async_serve()`; added `dynamic` to top-level `async_serve()` for
  parity with `serve()`.
- Raised dependency floors to `apcore>=0.25.0` and `apcore-toolkit>=0.9.1`
  (drop-in; no consumed API changed). All 853 tests pass.


## [0.16.0] - 2026-06-12

### Added

- **Approval Phase B: async polling via `__apcore_approval_check` meta-tool**.
  apcore-mcp now supports out-of-band human approvals that do not block the MCP connection.

  New public API:
  - `ApprovalStore` (Protocol) — pluggable persistence interface; three async methods:
    `save_pending`, `get_result`, `resolve`.
  - `InMemoryApprovalStore` — in-process implementation for testing/local dev.
    Ships with bounded memory management: per-record TTL via `call_later`, a background
    sweep task, and a `max_records` hard cap with oldest-pending eviction.
    **Not suitable for production** (no persistence, no cross-process sharing).
  - `StorageBackedApprovalHandler` (implements `apcore.ApprovalHandler`) — writes
    pending records on `request_approval()`, reads them on `check_approval()`.
    Optional `notify_callback` lets callers fan out to Slack/email/webhooks.
  - `ApprovalBridge` — registers `__apcore_approval_check` as an MCP meta-tool,
    symmetric with `AsyncTaskBridge`.

  Usage::

      from apcore_mcp import APCoreMCP, InMemoryApprovalStore

      store = InMemoryApprovalStore()
      mcp = APCoreMCP("./extensions", approval_store=store)

      # External system approves out-of-band:
      await store.resolve(approval_id, approved=True)

  Phase A (synchronous `ElicitationApprovalHandler`) is unchanged. All 841 tests pass.

Closes [issue #70](https://github.com/aiperceivable/apcore/issues/70): remove bridge-level `user_fixable` stamping now that apcore 0.24.0 resolves it at construction time via `_USER_FIXABLE_BY_CODE`.

### Changed

- **Raised apcore floor to `>=0.24.0`** (`pyproject.toml`). apcore 0.24.0 introduced `_USER_FIXABLE_BY_CODE`, which auto-populates `user_fixable` on `ModuleError` at construction time for all user-actionable codes (`SCHEMA_VALIDATION_ERROR`, `GENERAL_INVALID_INPUT`, `MODULE_NOT_FOUND`, `VERSION_CONSTRAINT_INVALID`, `BINDING_SCHEMA_INFERENCE_FAILED`, `BINDING_SCHEMA_MODE_CONFLICT`, `BINDING_STRICT_SCHEMA_INCOMPATIBLE`, `DEPENDENCY_NOT_FOUND`, `DEPENDENCY_VERSION_MISMATCH` → `True`; governance/system codes → `False`; `MODULE_EXECUTE_ERROR` and unlisted → `None`).
- **Removed bridge-level `_USER_FIXABLE_CODES` constant and stamping block** from `ErrorMapper._handle_apcore_error` (`src/apcore_mcp/adapters/errors.py`). The bridge no longer overrides or duplicates apcore's own policy; `user_fixable` flows through the existing `_attach_ai_guidance` path unchanged. Removed the fast-path `isinstance` branches for `DependencyNotFoundError` / `DependencyVersionMismatchError` that hardcoded `"userFixable": True`. All 806 tests pass.

## [0.15.0] - 2026-05-29

Audit-driven consistency work from `/apcore-skills:audit --scope mcp`. Eight per-repo fixes land here; the docs/spec repo (`apcore-mcp/`) remains at 0.15.0 because no spec contracts changed, so SDK versions also stay at 0.15.0 pending an explicit release decision. The entries below describe changes already committed on `main`.

### Changed

- **Upgraded required runtime to apcore 0.22.0 and apcore-toolkit 0.8.0** (`pyproject.toml`: `apcore>=0.22.0`, `apcore-toolkit>=0.8.0`). Adopts the apcore 0.22.0 `Context.create()` signature unification (D-24): the cancel token registered for each MCP tool call is now passed as the first-class `cancel_token=` parameter at both `ExecutionRouter._dispatch` context-creation sites, replacing the prior post-hoc `context.cancel_token = …` assignment that the apcore 0.22.0 changelog explicitly flagged in `apcore-mcp-python`. No public API change; full suite green (807 passed).

### Breaking Changes

- **`OpenAIConverter.convert_descriptor(strict=...)` default flipped from `False` to `True`** ([D11-5] / OC-1). Cross-SDK parity with `apcore-mcp-typescript` 0.14.0+, which already defaults to strict mode. Callers that previously relied on the lax default (`additionalProperties` allowed, original `required` ordering preserved) must now pass `strict=False` explicitly. Strict mode injects `additionalProperties: false`, hoists all properties into `required` (sorted alphabetically, with optionals widened to nullable), and emits `"strict": true` on the function definition — the format OpenAI Structured Outputs expects. Note: the top-level `to_openai_tools()` and `APCoreMCP.to_openai_tools()` wrappers (and `OpenAIConverter.convert_registry`) still default to `strict=False` and pass that through; this change only affects callers using `convert_descriptor` directly with no `strict` argument.

### Fixed

- **[D10-001] `ErrorMapper.to_mcp_error` emits canonical `"Schema validation failed"` for `SCHEMA_VALIDATION_ERROR` even when `details` is `None`.** Previously Python's `if code == SCHEMA_VALIDATION_ERROR and details is not None` guard fell through to passthrough and emitted the raw `error.message`, while Rust+TS unconditionally emitted `"Schema validation failed"`. Cross-SDK callers grouping logs or doing i18n on the canonical string no longer silently miss Python.
- **[D10-002] `MCPServerFactory.create_server(name)` now validates non-empty + max 255 chars per spec.** Previously the spec-declared `ValueError` was silently absent; empty / oversized names propagated to the underlying MCP server constructor. Fix applied across all three SDKs.
- **[D11-6] Router-fallback async-bridge dispatch now extracts `_meta.traceparent` and forwards `transport_session_var.get()` as `session_key`.** Previously the factory layer (`register_handlers`) extracted both, but the defensive router-layer fallback path (`_dispatch`) silently lost W3C trace propagation + session mass-cancel indexing. Direct-router consumers (custom test harnesses) now get parity with the factory path.

### Refactored

- **[D9-001] Collapsed parallel `serve()` / `async_serve()` / `to_openai_tools()` pipelines** into a single canonical implementation on `APCoreMCP`. Prior to 0.16.0 the same pipeline was assembled twice — once in the module-level functions in `apcore_mcp/__init__.py` and once in `APCoreMCP`, which then *delegated back* to the module-level functions. Every new feature had to be wired in two places, which had already produced latent bugs in `extra_routes` typing (`list[Mount]` vs. `list[Route | Mount]`) and `metrics_collector` type narrowing. Post-refactor:
  - `APCoreMCP` owns Config Bus loading (via the relocated `_load_config_bus_overrides` helper), observability auto-wiring, async-task bridge construction, explorer routes, auth middleware, and transport selection.
  - Module-level `serve()`, `async_serve()`, and `to_openai_tools()` are now thin delegators that construct an `APCoreMCP` and forward to its instance methods. Their public signatures are unchanged.
  - `APCoreMCP.__init__` gained five new kwargs (`strategy`, `redact_output`, `trace`, `dynamic`, plus internal `_load_pipeline_from_config`) so it can absorb every option the legacy `serve()` signature exposed.
  - `APCoreMCP._build_server_components` now resolves `MCPServerFactory` / `ExecutionRouter` via the `apcore_mcp` package namespace so that existing tests patching `apcore_mcp.MCPServerFactory` / `apcore_mcp.ExecutionRouter` intercept both legacy and class-based entry points.
  - Net source LOC reduction: ~180 lines deleted across the two files. The buggy code paths around `metrics_collector` narrowing (formerly at `__init__.py:442/444/450/557/564/568`) and `extra_routes` typing (formerly at `__init__.py:743/745/751`) are gone, taking the nine pre-existing pyright errors with them.
- **[D9-004] Removed `apcore_mcp.to_mcp_error_any` free function.** The body was effectively `del error; return internal_error_response()` — a no-op delegator that was asymmetric with TypeScript / Rust (method-only on `ErrorMapper`). Callers should use `internal_error_response()` directly (identical observable behavior) or `ErrorMapper().to_mcp_error_any(error)` for the typed-error path.
- **[D9-007] Deleted `apcore_mcp.inspector` stub package.** A 7-line placeholder with module docstring describing future F-039 scope; zero importers. Will be re-created when the F-039 implementation begins.

### Changed

- **[D5-002] Migrated to `apcore.observability.context_logger.ObsLoggingMiddleware`.** The legacy `LoggingMiddleware` emits a `DeprecationWarning` targeting removal in apcore 1.0.0. Public surface (`log_inputs` / `log_outputs` constructor parameters) is preserved. Suite warnings dropped from 9 to 0.

Leverages **apcore 0.21.0 + apcore-toolkit 0.7.0**. Promotes three new
upstream capabilities into MCP-facing surface area: `Module.preview()`
(PROTOCOL_SPEC §5.6), `CircuitBreakerOpenError` (sync alignment A-001),
and `apcore_toolkit.format_module(style="markdown")`. Cross-SDK byte-
equivalent with `apcore-mcp-typescript` and `apcore-mcp-rust` 0.15.0.

### Changed

- **Dependency bump**: `apcore >= 0.21.0` (was `>= 0.19.0`); `apcore-toolkit >= 0.7.0` (was `>= 0.5.0`, optional `[markdown]` extra).

### Added

- **Built-in output format support**: Added `--output-format` (`json`, `csv`, `jsonl`) to CLI and `output_format` parameter to `serve()`. Leverages `apcore-toolkit` 0.7 for standard tabular formatting.
- **`__apcore_module_preview` meta-tool** (apcore 0.21 PROTOCOL_SPEC §5.6 / §12.8) — fifth reserved meta-tool alongside the four `__apcore_task_*` ones. Drives `executor.validate(module_id, inputs, context)` and returns a structured `{valid, requires_approval, predicted_changes, checks}` envelope WITHOUT executing the module. Lets AI orchestrators answer "what would change in the world if I called this?" before invoking destructive or stateful modules. `arguments: null` is preserved verbatim — the calling business decides whether null is a valid input. Structurally-impossible shapes (arrays, scalars) return a typed validation error.
- **`AsyncTaskBridge(executor=...)` constructor kwarg** — explicit Executor reference for the preview meta-tool. When omitted, falls back to the manager's bound executor. The `with_limits` factory wires this automatically.
- **`MCPServerFactory(rich_description=True)`** and **`OpenAIConverter.convert_descriptor(rich_description=True)` / `convert_registry(rich_description=True)`** — render `Tool.description` / OpenAI `function.description` as canonical apcore-toolkit Markdown (`format_module(style="markdown")`) instead of the plain one-line description. Includes title, description, parameters list, returns list, behavior table (only fields differing from defaults — toolkit 0.6 alignment), tags, and examples. LLMs select tools primarily from this string; Markdown packs more decision-relevant signal per token. Display-overlay `mcp.description` overrides still win first. One-shot WARN log when `apcore-toolkit` is not installed (recommend `pip install 'apcore-mcp[markdown]'`).
- **`apcore_mcp.markdown` module** — public helpers: `is_available()`, `descriptor_to_scanned_module(descriptor)`, `render_module_markdown(descriptor, *, display=True)`. The descriptor adapter is forwards-compatible across toolkit minor versions (introspects `dataclasses.fields(ScannedModule)` to drop unknown kwargs).
- **`CIRCUIT_BREAKER_OPEN` error mapping** (apcore 0.20 sync alignment A-001) — `ErrorMapper.to_mcp_error` now dispatches `apcore.errors.CircuitBreakerOpenError` to a retryable=True envelope with the per-module `aiGuidance` mirrored from the apcore error class. New constant `ERROR_CODES["CIRCUIT_BREAKER_OPEN"]`. Best-effort import shim keeps the mapper compatible with pre-0.20 apcore builds.

### Fixed

- **`AsyncTaskBridge` async-API alignment with apcore 0.20+** — adapts to apcore's `AsyncTaskManager.{submit,cancel,shutdown}` becoming async (D10-003 / D10-004). Bridge methods now `await` upstream calls; sync transport-layer cancel handlers route through `tokio`-style fire-and-forget patterns where applicable.
- **`__apcore_task_status` redactor try/except symmetry** — wraps the redactor call so a buggy redactor does not bring down the meta-tool; falls back to the unredacted result with a DEBUG log.

### Tests

- +9 new tests covering `__apcore_module_preview` (basic predict, missing module_id, `arguments: null` preserved, missing arguments preserved, array rejection, meta-tool registration), `CIRCUIT_BREAKER_OPEN` mapping (retryable + aiGuidance), and `rich_description` (Markdown rendering, display-overlay override, toolkit-missing fallback, `convert_registry` propagation, factory build_tool integration).
- Total suite: **771 passed** (was 758).

## [0.14.0] - 2026-05-01

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

### Cross-language sync (deferred-modules round, 2026-04-28)

- **Dependency bump**: `mcp-embedded-ui >= 0.4.0` (was `>= 0.3.1`). The new release ships `POST /tools/{name}/validate` (F7) — read-only schema validation, ungated by `allow_execute` or `auth_hook`. The route flows automatically through `create_explorer_mount`. **Resolves EUI-1.**
- **JWT-1 — `Authenticator.authenticate` is now `async`.** Existing sync implementations continue to work via the new `apcore_mcp.auth.protocol.call_authenticator(auth, headers)` helper, which inspects the return value and awaits if it's a coroutine. Aligns with TS+Rust on the unified `(headers: HeaderMap) -> Awaitable<Identity | None>` contract. Tests for `JWTAuthenticator` are now `async def`.
- **TM-4 — transport-disconnect cancellation forwarding.** `TransportManager.set_async_task_bridge(bridge)` matches TS `setAsyncTaskBridge` and Rust `set_cancel_handler`. The transport scopes a per-connection session id via the new `transport_session_var` `ContextVar`; `factory.handle_call_tool` forwards it as `session_key` to `bridge.submit(...)`, and on transport teardown the manager calls `bridge.cancel_session_tasks(session_id)`. Wired automatically by `serve()`, `async_serve()`, and `APCoreMCP.serve` / `async_serve` when an async bridge is present. 6 regression tests.
- **EB-2 — adapter-hook kwargs.** `serve()` and `async_serve()` accept `schema_converter`, `annotation_mapper`, `error_mapper` kwargs that override the factory's built-in adapters.
- **EM-1 — `McpErrorFormatter` canonical class name.** Added as the preferred PascalCase name (matches TS+Rust). The pre-existing `MCPErrorFormatter` (all-caps) is kept as a backwards-compatible alias. Both are exported from `apcore_mcp` and `apcore_mcp.adapters`.
- **EM-3 — `userFixable=true` stamp.** `ErrorMapper` now hardcodes `userFixable: true` for `DEPENDENCY_NOT_FOUND`, `DEPENDENCY_VERSION_MISMATCH`, `VERSION_CONSTRAINT_INVALID`, and the four `BINDING_*` codes (matches TS). apcore 0.19's error classes don't yet set `user_fixable=true` themselves, so the bridge stamps the hint to give MCP clients a consistent self-healing signal. 9 regression tests.
- **MID-5 — `ModuleIDNormalizer.try_denormalize`.** New bijection-guarded variant validates the dash→dot-replaced result against `MODULE_ID_PATTERN`, returning `None` for inputs that aren't valid pre-images of `normalize`. Plain `denormalize` stays lenient. 8 regression tests.
- **JWT-2 — case-insensitive `Authorization` header lookup.** `JWTAuthenticator.authenticate` now tries both `headers["authorization"]` and `headers["Authorization"]`. ASGI lower-cases header names but direct callers may pass the capitalised form; RFC 7230 §3.2 mandates case-insensitive header names. Matches TS+Rust behaviour. 1 regression test.
- **AM-L1 — F-041 annotation extras format aligned with TS+Rust.** `mcp_*` extras are now appended after the `[Annotations: ...]` block separated by a single newline (was each extra as its own `\n\n`-separated section). 1 regression test.
- TC-011 integration tests added in `tests/explorer/test_explorer.py::TestTC011Validate` pinning the `/validate` wire-up.

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
