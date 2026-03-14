"""Launch MCP server with binding.yaml modules — zero code intrusion demo.

Usage:
    PYTHONPATH=./examples/binding_demo python examples/binding_demo/run.py

Then open http://127.0.0.1:8000/explorer/ in your browser.
"""

from apcore import BindingLoader, Registry

from apcore_mcp import serve

# 1. Load modules from binding.yaml files (myapp.py stays untouched)
registry = Registry()
loader = BindingLoader()
modules = loader.load_binding_dir("./examples/binding_demo/extensions", registry)
print(f"Loaded {len(modules)} module(s) from binding files")

# 2. Launch MCP server with Explorer UI
serve(
    registry,
    transport="streamable-http",
    host="127.0.0.1",
    port=8000,
    explorer=True,
    allow_execute=True,
    explorer_title="APCore MCP Explorer",
    explorer_project_name="apcore-mcp",
    explorer_project_url="https://github.com/aipartnerup/apcore-mcp-python",
)
