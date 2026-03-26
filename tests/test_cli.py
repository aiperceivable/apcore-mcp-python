"""Tests for the apcore-mcp CLI entry point (__main__.py)."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from apcore_mcp.__main__ import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_main(*args: str) -> None:
    """Invoke main() with the given CLI arguments."""
    with patch.object(sys, "argv", ["apcore-mcp", *args]):
        main()


def _make_patches(stub_registry_cls=None, discover_return=5):
    """Create standard patches for Registry and serve.

    Returns a dict of patch context managers.
    """
    if stub_registry_cls is None:
        mock_registry_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.discover.return_value = discover_return
        mock_registry_cls.return_value = mock_instance
    else:
        mock_registry_cls = stub_registry_cls

    return {
        "registry_patch": patch("apcore_mcp.__main__.Registry", mock_registry_cls),
        "serve_patch": patch("apcore_mcp.__main__.serve"),
        "mock_registry_cls": mock_registry_cls,
    }


# ===========================================================================
# Test: Default values
# ===========================================================================


class TestDefaults:
    """Verify default values when only --extensions-dir is provided."""

    def test_defaults_are_applied(self, tmp_path):
        """When only --extensions-dir is given, defaults fill the rest."""
        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"] as mock_serve:
            _run_main("--extensions-dir", str(tmp_path))

            mock_serve.assert_called_once()
            call_kwargs = mock_serve.call_args
            assert call_kwargs.kwargs["transport"] == "stdio"
            assert call_kwargs.kwargs["host"] == "127.0.0.1"
            assert call_kwargs.kwargs["port"] == 8000
            assert call_kwargs.kwargs["name"] == "apcore-mcp"


# ===========================================================================
# Test: Argument parsing with all options
# ===========================================================================


class TestAllOptions:
    """Verify all CLI options are parsed and forwarded correctly."""

    def test_all_options_forwarded(self, tmp_path):
        """All explicit options are forwarded to serve()."""
        patches = _make_patches()
        with (
            patches["registry_patch"] as mock_reg,
            patches["serve_patch"] as mock_serve,
        ):
            _run_main(
                "--extensions-dir",
                str(tmp_path),
                "--transport",
                "streamable-http",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--name",
                "my-server",
                "--version",
                "2.0.0",
                "--log-level",
                "DEBUG",
            )

            # Registry constructed with correct extensions_dir (converted to str)
            mock_reg.assert_called_once_with(extensions_dir=str(tmp_path))

            # serve() called with all forwarded options
            mock_serve.assert_called_once()
            kw = mock_serve.call_args.kwargs
            assert kw["transport"] == "streamable-http"
            assert kw["host"] == "0.0.0.0"
            assert kw["port"] == 9000
            assert kw["name"] == "my-server"
            assert kw["version"] == "2.0.0"


# ===========================================================================
# Test: Missing --extensions-dir
# ===========================================================================


class TestMissingExtensionsDir:
    """Missing required --extensions-dir should cause exit code 2."""

    def test_missing_extensions_dir_exits_2(self):
        """argparse exits with code 2 when --extensions-dir is missing."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main()  # no arguments at all
        assert exc_info.value.code == 2


# ===========================================================================
# Test: Non-existent extensions dir
# ===========================================================================


class TestNonExistentExtensionsDir:
    """Non-existent --extensions-dir should cause exit code 1."""

    def test_nonexistent_dir_exits_1(self, capsys):
        """A path that doesn't exist produces exit code 1 and stderr message."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", "/no/such/directory/exists")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err or "not a directory" in captured.err

    def test_file_instead_of_dir_exits_1(self, tmp_path, capsys):
        """A file (not a directory) produces exit code 1."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("hello")
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(some_file))
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not a directory" in captured.err


# ===========================================================================
# Test: Port range validation
# ===========================================================================


class TestPortValidation:
    """Port must be in range 1-65535."""

    def test_port_zero_rejected(self, tmp_path):
        """Port 0 is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(tmp_path), "--port", "0")
        assert exc_info.value.code == 2

    def test_port_negative_rejected(self, tmp_path):
        """Negative port is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(tmp_path), "--port", "-1")
        assert exc_info.value.code == 2

    def test_port_too_high_rejected(self, tmp_path):
        """Port 65536 is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(tmp_path), "--port", "65536")
        assert exc_info.value.code == 2

    def test_port_valid_boundaries(self, tmp_path):
        """Ports 1 and 65535 are accepted."""
        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"] as mock_serve:
            _run_main("--extensions-dir", str(tmp_path), "--port", "1")
            assert mock_serve.call_args.kwargs["port"] == 1

        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"] as mock_serve:
            _run_main("--extensions-dir", str(tmp_path), "--port", "65535")
            assert mock_serve.call_args.kwargs["port"] == 65535

    def test_port_non_integer_rejected(self, tmp_path):
        """Non-integer port is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(tmp_path), "--port", "abc")
        assert exc_info.value.code == 2


# ===========================================================================
# Test: Name max length validation
# ===========================================================================


class TestNameValidation:
    """Server name must not exceed 255 characters."""

    def test_name_too_long_exits_1(self, tmp_path, capsys):
        """Name longer than 255 chars exits with code 1."""
        long_name = "x" * 256
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(tmp_path), "--name", long_name)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "255" in captured.err

    def test_name_at_limit_accepted(self, tmp_path):
        """Name with exactly 255 chars is accepted."""
        name_255 = "x" * 255
        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"] as mock_serve:
            _run_main("--extensions-dir", str(tmp_path), "--name", name_255)
            assert mock_serve.call_args.kwargs["name"] == name_255


# ===========================================================================
# Test: Invalid transport rejected
# ===========================================================================


class TestTransportValidation:
    """Only stdio, streamable-http, and sse are valid transports."""

    def test_invalid_transport_rejected(self, tmp_path):
        """An unknown transport exits with code 2 (argparse choices error)."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(tmp_path), "--transport", "websocket")
        assert exc_info.value.code == 2

    def test_all_valid_transports_accepted(self, tmp_path):
        """All three valid transports are accepted."""
        for transport in ("stdio", "streamable-http", "sse"):
            patches = _make_patches()
            with patches["registry_patch"], patches["serve_patch"] as mock_serve:
                _run_main("--extensions-dir", str(tmp_path), "--transport", transport)
                assert mock_serve.call_args.kwargs["transport"] == transport


# ===========================================================================
# Test: Log level configuration
# ===========================================================================


class TestLogLevel:
    """Log level is applied to the logging module."""

    def test_log_level_set(self, tmp_path):
        """--log-level DEBUG configures the root logger."""
        patches = _make_patches()
        with (
            patches["registry_patch"],
            patches["serve_patch"],
            patch("apcore_mcp.__main__.logging") as mock_logging,
        ):
            _run_main("--extensions-dir", str(tmp_path), "--log-level", "DEBUG")
            mock_logging.basicConfig.assert_called_once()
            call_kwargs = mock_logging.basicConfig.call_args
            # level should be the DEBUG constant
            assert call_kwargs.kwargs.get("level") == mock_logging.DEBUG

    def test_default_log_level_is_info(self, tmp_path):
        """Default log level is INFO."""
        patches = _make_patches()
        with (
            patches["registry_patch"],
            patches["serve_patch"],
            patch("apcore_mcp.__main__.logging") as mock_logging,
        ):
            _run_main("--extensions-dir", str(tmp_path))
            call_kwargs = mock_logging.basicConfig.call_args
            assert call_kwargs.kwargs.get("level") == mock_logging.INFO

    def test_invalid_log_level_rejected(self, tmp_path):
        """An invalid log level exits with code 2."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--extensions-dir", str(tmp_path), "--log-level", "TRACE")
        assert exc_info.value.code == 2


# ===========================================================================
# Test: Full wiring (main creates Registry, calls discover, calls serve)
# ===========================================================================


class TestFullWiring:
    """Verify the full wiring: Registry created, discover called, serve called."""

    def test_main_creates_registry_discovers_and_serves(self, tmp_path):
        """main() creates Registry(extensions_dir=...), calls discover(), then serve()."""
        mock_registry_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.discover.return_value = 3
        mock_registry_cls.return_value = mock_instance

        with (
            patch("apcore_mcp.__main__.Registry", mock_registry_cls) as mock_reg,
            patch("apcore_mcp.__main__.serve") as mock_serve,
        ):
            _run_main("--extensions-dir", str(tmp_path))

            # Registry created with the correct extensions_dir (converted to str)
            mock_reg.assert_called_once_with(extensions_dir=str(tmp_path))

            # discover() called on the instance
            mock_instance.discover.assert_called_once()

            # serve() called with the registry instance as first positional arg
            mock_serve.assert_called_once()
            assert mock_serve.call_args.args[0] is mock_instance

    def test_zero_modules_still_starts_server(self, tmp_path):
        """If discover returns 0, server still starts (with a warning)."""
        mock_registry_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.discover.return_value = 0
        mock_registry_cls.return_value = mock_instance

        with (
            patch("apcore_mcp.__main__.Registry", mock_registry_cls),
            patch("apcore_mcp.__main__.serve") as mock_serve,
        ):
            _run_main("--extensions-dir", str(tmp_path))

            # serve() should still be called even with 0 modules
            mock_serve.assert_called_once()

    def test_serve_exception_exits_2(self, tmp_path, capsys):
        """If serve() raises, main() exits with code 2."""
        mock_registry_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.discover.return_value = 1
        mock_registry_cls.return_value = mock_instance

        with (
            patch("apcore_mcp.__main__.Registry", mock_registry_cls),
            patch("apcore_mcp.__main__.serve", side_effect=RuntimeError("bind failed")),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run_main("--extensions-dir", str(tmp_path))
            assert exc_info.value.code == 2


# ===========================================================================
# Test: --help flag
# ===========================================================================


class TestHelp:
    """--help prints usage and exits with code 0."""

    def test_help_flag(self, capsys):
        """--help prints usage information and exits with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main("--help")
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--extensions-dir" in captured.out
        assert "--transport" in captured.out
        assert "--host" in captured.out
        assert "--port" in captured.out
        assert "--name" in captured.out
        assert "--version" in captured.out
        assert "--log-level" in captured.out


# ===========================================================================
# Test: Version default from package
# ===========================================================================


class TestVersionDefault:
    """When --version is not provided, serve() receives version=None (uses package default)."""

    def test_version_defaults_to_none(self, tmp_path):
        """Without --version, serve() is called with version=None."""
        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"] as mock_serve:
            _run_main("--extensions-dir", str(tmp_path))
            # version not specified -> should pass None so serve() uses its own default
            assert mock_serve.call_args.kwargs["version"] is None


# ===========================================================================
# Test: --jwt-key-file
# ===========================================================================


class TestJWTKeyFile:
    """Verify --jwt-key-file reads the PEM key from a file."""

    def test_jwt_key_file_reads_content(self, tmp_path):
        """--jwt-key-file reads key content from the file and passes it to JWTAuthenticator."""
        key_file = tmp_path / "public.pem"
        key_file.write_text("my-pem-key-content\n")

        mock_auth_cls = MagicMock()
        patches = _make_patches()
        auth_module = MagicMock(JWTAuthenticator=mock_auth_cls)
        with (
            patches["registry_patch"],
            patches["serve_patch"] as mock_serve,
            patch.dict("sys.modules", {"apcore_mcp.auth": auth_module}),
        ):
            _run_main(
                "--extensions-dir",
                str(tmp_path),
                "--jwt-key-file",
                str(key_file),
            )

            mock_auth_cls.assert_called_once()
            assert mock_auth_cls.call_args.kwargs["key"] == "my-pem-key-content"
            mock_serve.assert_called_once()
            assert mock_serve.call_args.kwargs["authenticator"] is not None

    def test_jwt_key_file_not_found_exits_1(self, tmp_path, capsys):
        """--jwt-key-file with non-existent file exits with code 1."""
        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"]:
            with pytest.raises(SystemExit) as exc_info:
                _run_main(
                    "--extensions-dir",
                    str(tmp_path),
                    "--jwt-key-file",
                    str(tmp_path / "nonexistent.pem"),
                )
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "does not exist" in captured.err

    def test_jwt_key_file_takes_priority_over_secret(self, tmp_path):
        """--jwt-key-file takes priority over --jwt-secret."""
        key_file = tmp_path / "key.pem"
        key_file.write_text("file-key\n")

        mock_auth_cls = MagicMock()
        patches = _make_patches()
        auth_module = MagicMock(JWTAuthenticator=mock_auth_cls)
        with (
            patches["registry_patch"],
            patches["serve_patch"],
            patch.dict("sys.modules", {"apcore_mcp.auth": auth_module}),
        ):
            _run_main(
                "--extensions-dir",
                str(tmp_path),
                "--jwt-key-file",
                str(key_file),
                "--jwt-secret",
                "inline-secret",
            )
            # Should use the file key, not the inline secret
            assert mock_auth_cls.call_args.kwargs["key"] == "file-key"


# ===========================================================================
# Test: APCORE_JWT_SECRET env var fallback
# ===========================================================================


class TestJWTEnvVarFallback:
    """Verify APCORE_JWT_SECRET environment variable is used as fallback."""

    def test_env_var_used_when_no_flags(self, tmp_path):
        """APCORE_JWT_SECRET env var is used when neither --jwt-secret nor --jwt-key-file is set."""
        mock_auth_cls = MagicMock()
        patches = _make_patches()
        auth_module = MagicMock(JWTAuthenticator=mock_auth_cls)
        with (
            patches["registry_patch"],
            patches["serve_patch"],
            patch.dict("sys.modules", {"apcore_mcp.auth": auth_module}),
            patch.dict(os.environ, {"APCORE_JWT_SECRET": "env-secret"}, clear=False),
        ):
            _run_main("--extensions-dir", str(tmp_path))
            mock_auth_cls.assert_called_once()
            assert mock_auth_cls.call_args.kwargs["key"] == "env-secret"

    def test_jwt_secret_flag_takes_priority_over_env(self, tmp_path):
        """--jwt-secret takes priority over APCORE_JWT_SECRET env var."""
        mock_auth_cls = MagicMock()
        patches = _make_patches()
        auth_module = MagicMock(JWTAuthenticator=mock_auth_cls)
        with (
            patches["registry_patch"],
            patches["serve_patch"],
            patch.dict("sys.modules", {"apcore_mcp.auth": auth_module}),
            patch.dict(os.environ, {"APCORE_JWT_SECRET": "env-secret"}, clear=False),
        ):
            _run_main(
                "--extensions-dir",
                str(tmp_path),
                "--jwt-secret",
                "flag-secret",
            )
            assert mock_auth_cls.call_args.kwargs["key"] == "flag-secret"

    def test_no_authenticator_when_no_key_source(self, tmp_path):
        """No authenticator created when no --jwt-secret, --jwt-key-file, or APCORE_JWT_SECRET."""
        patches = _make_patches()
        with (
            patches["registry_patch"],
            patches["serve_patch"] as mock_serve,
            patch.dict(os.environ, {}, clear=False),
        ):
            # Ensure APCORE_JWT_SECRET is not set
            env_backup = os.environ.pop("APCORE_JWT_SECRET", None)
            try:
                _run_main("--extensions-dir", str(tmp_path))
                assert mock_serve.call_args.kwargs["authenticator"] is None
            finally:
                if env_backup is not None:
                    os.environ["APCORE_JWT_SECRET"] = env_backup


# ===========================================================================
# Test: --approval flag
# ===========================================================================


class TestApprovalFlag:
    """Verify --approval CLI flag creates the correct handler."""

    def test_approval_elicit_creates_handler(self, tmp_path):
        """--approval elicit creates ElicitationApprovalHandler."""
        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"] as mock_serve:
            _run_main("--extensions-dir", str(tmp_path), "--approval", "elicit")

            mock_serve.assert_called_once()
            handler = mock_serve.call_args.kwargs["approval_handler"]
            assert handler is not None
            from apcore_mcp.adapters.approval import ElicitationApprovalHandler

            assert isinstance(handler, ElicitationApprovalHandler)

    def test_approval_auto_approve_creates_handler(self, tmp_path):
        """--approval auto-approve creates AutoApproveHandler."""
        mock_auto_cls = MagicMock()
        patches = _make_patches()
        with (
            patches["registry_patch"],
            patches["serve_patch"] as mock_serve,
            patch("apcore_mcp.__main__.AutoApproveHandler", mock_auto_cls, create=True),
            patch.dict("sys.modules", {"apcore.approval": MagicMock(AutoApproveHandler=mock_auto_cls)}),
        ):
            _run_main("--extensions-dir", str(tmp_path), "--approval", "auto-approve")

            mock_serve.assert_called_once()
            assert mock_serve.call_args.kwargs["approval_handler"] is not None

    def test_approval_always_deny_creates_handler(self, tmp_path):
        """--approval always-deny creates AlwaysDenyHandler."""
        mock_deny_cls = MagicMock()
        patches = _make_patches()
        with (
            patches["registry_patch"],
            patches["serve_patch"] as mock_serve,
            patch("apcore_mcp.__main__.AlwaysDenyHandler", mock_deny_cls, create=True),
            patch.dict("sys.modules", {"apcore.approval": MagicMock(AlwaysDenyHandler=mock_deny_cls)}),
        ):
            _run_main("--extensions-dir", str(tmp_path), "--approval", "always-deny")

            mock_serve.assert_called_once()
            assert mock_serve.call_args.kwargs["approval_handler"] is not None

    def test_approval_off_no_handler(self, tmp_path):
        """Default (no --approval flag) creates no handler."""
        patches = _make_patches()
        with patches["registry_patch"], patches["serve_patch"] as mock_serve:
            _run_main("--extensions-dir", str(tmp_path))

            mock_serve.assert_called_once()
            assert mock_serve.call_args.kwargs["approval_handler"] is None
