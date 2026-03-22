"""Launch MCP server with all example modules — class-based + binding.yaml.

Usage (from the project root):
    PYTHONPATH=./examples/binding_demo python examples/run.py

Then open http://127.0.0.1:8000/explorer/ in your browser.

Enable JWT authentication by setting JWT_SECRET:
    JWT_SECRET=my-secret python examples/run.py

Then test with curl:
    curl http://localhost:8000/health                        # 200 (exempt)
    curl http://localhost:8000/mcp                           # 401 (no token)
    curl -H "Authorization: Bearer <token>" localhost:8000/mcp  # 200
"""

import os

from apcore import BindingLoader, Registry

from apcore_mcp import JWTAuthenticator, serve

# 1. Discover class-based modules from extensions/
registry = Registry(extensions_dir="./examples/extensions")
n_class = registry.discover()

# 2. Load binding.yaml modules into the same registry
loader = BindingLoader()
binding_modules = loader.load_binding_dir("./examples/binding_demo/extensions", registry)

print(f"Class-based modules: {n_class}")
print(f"Binding modules:     {len(binding_modules)}")
print(f"Total:               {len(registry.module_ids)}")

# 3. Build JWT authenticator if JWT_SECRET is set
authenticator = None
jwt_secret = os.environ.get("JWT_SECRET")
if jwt_secret:
    authenticator = JWTAuthenticator(key=jwt_secret)
    print(f"JWT authentication:  enabled (HS256)")
    # Generate a sample token for testing
    import jwt as pyjwt

    sample_token = pyjwt.encode(
        {"sub": "demo-user", "type": "user", "roles": ["admin"]},
        jwt_secret,
        algorithm="HS256",
    )
    print(f"Sample token:        {sample_token}")
else:
    print("JWT authentication:  disabled (set JWT_SECRET to enable)")

# 4. Launch MCP server with Explorer UI
serve(
    registry,
    transport="streamable-http",
    host="127.0.0.1",
    port=8000,
    explorer=True,
    allow_execute=True,
    authenticator=authenticator,
    explorer_title="APCore MCP Explorer",
    explorer_project_name="apcore-mcp",
    explorer_project_url="https://github.com/aiperceivable/apcore-mcp-python",
)
