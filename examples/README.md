# Examples

Runnable demos of **apcore-mcp** with the Tool Explorer UI.

```
examples/
├── run.py                     # Unified launcher (all 5 modules)
├── extensions/                # Class-based apcore modules
│   ├── text_echo.py
│   ├── math_calc.py
│   └── greeting.py
└── binding_demo/              # Zero-code binding demo
    ├── myapp.py               # Plain business logic (NO apcore imports)
    ├── extensions/
    │   ├── convert_temperature.binding.yaml
    │   └── word_count.binding.yaml
    └── run.py                 # Binding-only launcher
```

## Quick Start (all modules together)

Both class-based modules and binding.yaml modules load into the same Registry and coexist as MCP tools.

```bash
# From the project root
pip install -e .

PYTHONPATH=./examples/binding_demo python examples/run.py
```

Open http://127.0.0.1:8000/explorer/ — you should see all 5 tools.

## Run class-based modules only

```bash
python -m apcore_mcp \
  --extensions-dir ./examples/extensions \
  --transport streamable-http \
  --explorer --allow-execute
```

No `PYTHONPATH` needed. Uses the built-in CLI directly.

## Run binding modules only

```bash
PYTHONPATH=./examples/binding_demo python examples/binding_demo/run.py
```

## All Modules

| Module | Type | Description |
|--------|------|-------------|
| `text_echo` | class-based | Echo text back, optionally uppercase |
| `math_calc` | class-based | Basic arithmetic (add, sub, mul, div) |
| `greeting` | class-based | Personalized greeting in different styles |
| `convert_temperature` | binding.yaml | Celsius / Fahrenheit / Kelvin conversion |
| `word_count` | binding.yaml | Count words, characters, and lines |

## Two Integration Approaches

| | Class-based | Binding YAML |
|---|---|---|
| Your code changes | Write apcore module class | **None** |
| Schema definition | Pydantic `BaseModel` | YAML `input_schema` / `output_schema` |
| Launch | CLI `--extensions-dir` | Python script with `BindingLoader` |
| Best for | New projects | Existing projects with functions to expose |

## JWT Authentication

Enable JWT authentication by setting the `APCORE_JWT_SECRET` environment variable:

```bash
APCORE_JWT_SECRET=my-secret PYTHONPATH=./examples/binding_demo python examples/run.py
```

### Test Token

Pre-generated token (secret: `my-secret`, algorithm: HS256):

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZW1vLXVzZXIiLCJ0eXBlIjoidXNlciIsInJvbGVzIjpbImFkbWluIl19.yOFQMlZnMZwXg6KoJX61sCm2VbCzmqtT8dFRNsOhaZM
```

Payload:

```json
{"sub": "demo-user", "type": "user", "roles": ["admin"]}
```

### Verify with cURL

```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZW1vLXVzZXIiLCJ0eXBlIjoidXNlciIsInJvbGVzIjpbImFkbWluIl19.yOFQMlZnMZwXg6KoJX61sCm2VbCzmqtT8dFRNsOhaZM"

# Health endpoint is exempt from auth
curl http://localhost:8000/health

# Without token → 401
curl http://localhost:8000/mcp

# With token → 200
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/mcp
```

### Explorer UI with JWT

The Explorer UI at http://127.0.0.1:8000/explorer/ is exempt from JWT authentication, so it always loads. To execute tools with identity, paste the Bearer token into the **Authorization** input at the top of the page (similar to Swagger UI). The token will be included in all tool execution requests and generated cURL commands.
