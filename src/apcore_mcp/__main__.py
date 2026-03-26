"""CLI entry point: python -m apcore_mcp."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from apcore.registry import Registry

from apcore_mcp import serve

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for apcore-mcp CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m apcore_mcp",
        description="Launch an MCP server that exposes apcore modules as tools.",
    )

    # Required
    parser.add_argument(
        "--extensions-dir",
        required=True,
        type=Path,
        help="Path to apcore extensions directory.",
    )

    # Transport options
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help="Transport type (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for HTTP-based transports (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP-based transports (default: 8000, range: 1-65535).",
    )

    # Server options
    parser.add_argument(
        "--name",
        default="apcore-mcp",
        help='MCP server name (default: "apcore-mcp", max 255 chars).',
    )
    parser.add_argument(
        "--version",
        default=None,
        help="MCP server version (default: package version).",
    )

    # Logging
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Logging level (default: INFO).",
    )

    # Explorer options
    parser.add_argument(
        "--explorer",
        action="store_true",
        default=False,
        help="Enable the browser-based Tool Explorer UI (HTTP transports only).",
    )
    parser.add_argument(
        "--explorer-prefix",
        default="/explorer",
        help='URL prefix for the explorer UI (default: "/explorer").',
    )
    parser.add_argument(
        "--allow-execute",
        action="store_true",
        default=False,
        help="Allow tool execution from the explorer UI.",
    )

    # JWT authentication options
    parser.add_argument(
        "--jwt-secret",
        default=None,
        help="JWT secret key for Bearer token authentication (HTTP transports only).",
    )
    parser.add_argument(
        "--jwt-algorithm",
        default="HS256",
        help='JWT algorithm (default: "HS256").',
    )
    parser.add_argument(
        "--jwt-audience",
        default=None,
        help="Expected JWT audience claim.",
    )
    parser.add_argument(
        "--jwt-issuer",
        default=None,
        help="Expected JWT issuer claim.",
    )
    parser.add_argument(
        "--jwt-key-file",
        type=Path,
        default=None,
        help="Path to PEM key file for JWT verification (e.g. RS256 public key).",
    )
    parser.add_argument(
        "--jwt-require-auth",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require JWT authentication (default: True). Use --no-jwt-require-auth for permissive mode.",
    )
    parser.add_argument(
        "--exempt-paths",
        default=None,
        help="Comma-separated paths exempt from auth (default: /health,/metrics).",
    )

    # Approval options
    parser.add_argument(
        "--approval",
        choices=("elicit", "auto-approve", "always-deny", "off"),
        default="off",
        help='Approval handler mode (default: "off"). '
        '"elicit" uses MCP elicitation, '
        '"auto-approve" auto-approves all requests (dev/testing), '
        '"always-deny" rejects all requests.',
    )

    return parser


def _validate_port(port: int, parser: argparse.ArgumentParser) -> None:
    """Validate port is in range 1-65535."""
    if port < 1 or port > 65535:
        parser.error(f"--port must be in range 1-65535, got {port}")


def main() -> None:
    """CLI entry point for launching apcore-mcp server.

    Exit codes:
        0 - Normal shutdown
        1 - Invalid arguments (non-existent directory, invalid port, name too long)
        2 - Startup failure (argparse error, serve() exception)
    """
    parser = _build_parser()
    args = parser.parse_args()

    # Validate port range (argparse only validates type, not range)
    _validate_port(args.port, parser)

    # Validate --extensions-dir exists and is a directory
    extensions_dir: Path = args.extensions_dir
    if not extensions_dir.exists():
        print(
            f"Error: --extensions-dir '{extensions_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not extensions_dir.is_dir():
        print(
            f"Error: --extensions-dir '{extensions_dir}' is not a directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate name length
    if len(args.name) > 255:
        print(
            f"Error: --name must be at most 255 characters, got {len(args.name)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Create Registry and discover modules
    registry = Registry(extensions_dir=str(extensions_dir))
    num_modules = registry.discover()

    if num_modules == 0:
        logger.warning("No modules discovered in '%s'.", extensions_dir)
    else:
        logger.info("Discovered %d module(s) in '%s'.", num_modules, extensions_dir)

    # Resolve JWT key: --jwt-key-file → --jwt-secret → APCORE_JWT_SECRET env var
    jwt_key: str | None = None
    if args.jwt_key_file:
        key_path: Path = args.jwt_key_file
        if not key_path.exists():
            print(f"Error: --jwt-key-file '{key_path}' does not exist.", file=sys.stderr)
            sys.exit(1)
        jwt_key = key_path.read_text().strip()
    elif args.jwt_secret:
        jwt_key = args.jwt_secret
    else:
        jwt_key = os.environ.get("APCORE_JWT_SECRET")

    # Build JWT authenticator if key resolved
    authenticator = None
    if jwt_key:
        from apcore_mcp.auth import JWTAuthenticator

        authenticator = JWTAuthenticator(
            key=jwt_key,
            algorithms=[args.jwt_algorithm],
            audience=args.jwt_audience,
            issuer=args.jwt_issuer,
        )
        logger.info("JWT authentication enabled (algorithm=%s)", args.jwt_algorithm)

    # Parse exempt paths
    exempt_paths_set = None
    if args.exempt_paths:
        exempt_paths_set = set(p.strip() for p in args.exempt_paths.split(","))

    # Build approval handler
    approval_handler = None
    if args.approval == "elicit":
        from apcore_mcp.adapters.approval import ElicitationApprovalHandler

        approval_handler = ElicitationApprovalHandler()
        logger.info("Approval handler: elicit (MCP elicitation)")
    elif args.approval == "auto-approve":
        from apcore.approval import AutoApproveHandler

        approval_handler = AutoApproveHandler()
        logger.info("Approval handler: auto-approve (dev/testing)")
    elif args.approval == "always-deny":
        from apcore.approval import AlwaysDenyHandler

        approval_handler = AlwaysDenyHandler()
        logger.info("Approval handler: always-deny (enforcement)")

    # Launch the MCP server
    try:
        serve(
            registry,
            transport=args.transport,
            host=args.host,
            port=args.port,
            name=args.name,
            version=args.version,
            explorer=args.explorer,
            explorer_prefix=args.explorer_prefix,
            allow_execute=args.allow_execute,
            authenticator=authenticator,
            require_auth=args.jwt_require_auth,
            exempt_paths=exempt_paths_set,
            approval_handler=approval_handler,
        )
    except Exception:
        logger.exception("Server startup failed.")
        sys.exit(2)


if __name__ == "__main__":
    main()
