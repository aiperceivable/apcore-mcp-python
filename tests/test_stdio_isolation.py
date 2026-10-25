"""Protocol stdout remains independent of diagnostics for the complete lifecycle."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def run_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )


def test_fd_isolation_captures_held_handlers_and_raw_writes_and_restores() -> None:
    completed = run_script("""
        import logging, os, sys
        from apcore_mcp._stdio import stdio_output
        held = logging.StreamHandler(sys.stdout)
        logger = logging.getLogger('host'); logger.addHandler(held); logger.setLevel(20)
        original = sys.stdout
        try:
            with stdio_output() as protocol:
                print('plain diagnostic')
                logger.info('held diagnostic')
                os.write(1, b'fd diagnostic\\n')
                with stdio_output() as nested:
                    assert nested is protocol
                    protocol.write('protocol\\n'); protocol.flush()
                raise ValueError('restore')
        except ValueError:
            pass
        assert sys.stdout is original
        print('restored')
    """)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "protocol\nrestored\n"
    assert all(marker in completed.stderr for marker in ("plain diagnostic", "held diagnostic", "fd diagnostic"))


def test_custom_stream_isolation_and_handler_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    from apcore_mcp._stdio import stdio_output

    protocol = io.StringIO()
    diagnostics = io.StringIO()
    monkeypatch.setattr(sys, "stdout", protocol)
    monkeypatch.setattr(sys, "stderr", diagnostics)
    logger = logging.getLogger("custom-stream-test")
    handler = logging.StreamHandler(protocol)
    logger.addHandler(handler)
    try:
        with stdio_output() as captured:
            assert captured is protocol
            print("diagnostic")
            logger.warning("held diagnostic")
            captured.write("protocol")
        assert sys.stdout is protocol
        assert handler.stream is protocol
        assert protocol.getvalue() == "protocol"
        assert diagnostics.getvalue() == "diagnostic\nheld diagnostic\n"
    finally:
        logger.removeHandler(handler)


async def test_other_async_task_cannot_acquire_stdio() -> None:
    from apcore_mcp._stdio import stdio_output

    ready = asyncio.Event()
    release = asyncio.Event()

    async def owner() -> None:
        with stdio_output():
            ready.set()
            await release.wait()

    task = asyncio.create_task(owner())
    await ready.wait()
    try:
        with pytest.raises(RuntimeError, match="already owned"), stdio_output():
            pass
    finally:
        release.set()
        await task


def test_other_thread_cannot_acquire_stdio() -> None:
    completed = run_script("""
        import threading
        from apcore_mcp._stdio import stdio_output
        failures = []
        def competing():
            try:
                with stdio_output(): pass
            except RuntimeError as exc:
                failures.append(str(exc))
        with stdio_output():
            thread = threading.Thread(target=competing); thread.start(); thread.join()
        assert len(failures) == 1 and 'already owned' in failures[0]
    """)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable-http"])
def test_convenience_serve_guards_constructor_callbacks_and_preparation(transport: str) -> None:
    completed = run_script(f"""
        from unittest.mock import patch
        from apcore_mcp import serve, APCoreMCP
        async def run(): print('runtime')
        def build(self, **kwargs): print('preparation'); return run
        def construct(self, *args, **kwargs):
            print('constructor'); self._registry = object(); self._output_format = None
        with patch.object(APCoreMCP, '__init__', construct), patch.object(APCoreMCP, '_build_serve_coro', build):
            serve(object(), transport={transport!r},
                  on_startup=lambda: print('startup'), on_shutdown=lambda: print('shutdown'))
        print('restored')
    """)
    assert completed.returncode == 0, completed.stderr
    lifecycle = "constructor\npreparation\nstartup\nruntime\nshutdown\n"
    assert completed.stdout == ("restored\n" if transport == "stdio" else lifecycle + "restored\n")
    if transport == "stdio":
        assert completed.stderr == lifecycle


def test_cli_discovery_is_guarded(tmp_path: Path) -> None:
    completed = run_script(f"""
        import sys
        from unittest.mock import patch
        from apcore_mcp.__main__ import main
        class Registry:
            def __init__(self, **kwargs): print('registry constructor')
            def discover(self): print('discovery'); return 1
        sys.argv = ['apcore-mcp', '--extensions-dir', {str(tmp_path)!r}]
        with (patch('apcore_mcp.__main__.Registry', Registry),
              patch('apcore_mcp.__main__.serve', lambda *a, **k: print('serve'))):
            main()
        print('restored')
    """)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "restored\n"
    assert all(marker in completed.stderr for marker in ("registry constructor", "discovery", "serve"))


def test_threaded_server_guards_build_and_runtime() -> None:
    completed = run_script("""
        from unittest.mock import patch
        from apcore_mcp import MCPServer
        class App:
            def _build_serve_coro(self, **kwargs):
                print('thread preparation')
                async def run(): print('thread runtime')
                return run
        def build(self): print('thread constructor'); return App()
        with patch.object(MCPServer, '_build_app', build):
            server = MCPServer(object()); server.start(); server.wait()
        print('restored')
    """)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "restored\n"
    assert completed.stderr == "thread constructor\nthread preparation\nthread runtime\n"


async def test_second_inherited_transport_is_rejected_and_cancellation_restores() -> None:
    from apcore_mcp._stdio import stdio_output

    original_stdout = sys.stdout
    with stdio_output(), stdio_output(transport=True):

        async def competing() -> None:
            with stdio_output(transport=True):
                pass

        with pytest.raises(RuntimeError, match="active transport"):
            await asyncio.create_task(competing())
    assert sys.stdout is original_stdout


async def test_direct_transport_preserves_protocol_and_restores_after_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from apcore_mcp.server.transport import TransportManager

    protocol = io.StringIO()
    diagnostics = io.StringIO()
    monkeypatch.setattr(sys, "stdout", protocol)
    monkeypatch.setattr(sys, "stderr", diagnostics)
    entered = asyncio.Event()

    @asynccontextmanager
    async def fake_stdio(*, stdout):
        print("transport entry")
        await stdout.write("protocol\n")
        try:
            yield object(), object()
        finally:
            print("transport exit")

    async def runtime(*args):
        print("runtime diagnostic")
        entered.set()
        await asyncio.Event().wait()

    manager = TransportManager()
    monkeypatch.setattr("apcore_mcp.server.transport.stdio_server", fake_stdio)
    monkeypatch.setattr(manager, "_run_scoped", AsyncMock(side_effect=runtime))
    task = asyncio.create_task(manager.run_stdio(MagicMock(), MagicMock()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    except TimeoutError:
        await task
        raise
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sys.stdout is protocol
    assert protocol.getvalue() == "protocol\n"
    assert diagnostics.getvalue() == "transport entry\nruntime diagnostic\ntransport exit\n"


def test_failed_fd_acquisition_releases_owner_and_duplicate() -> None:
    completed = run_script("""
        import os
        from unittest.mock import patch
        from apcore_mcp._stdio import stdio_output
        original_dup = os.dup
        duplicated = []
        def duplicate(fd):
            result = original_dup(fd); duplicated.append(result); return result
        with (patch('apcore_mcp._stdio.os.dup', duplicate),
              patch('apcore_mcp._stdio.os.dup2', side_effect=OSError('cannot redirect'))):
            try:
                with stdio_output(): pass
            except OSError: pass
        try:
            os.fstat(duplicated[0])
            raise AssertionError('leaked fd')
        except OSError: pass
        with stdio_output() as protocol:
            protocol.write('reacquired\\n'); protocol.flush()
    """)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "reacquired\n"


def test_configuration_bootstrap_diagnostics_are_guarded() -> None:
    completed = run_script("""
        from unittest.mock import patch
        from apcore_mcp import serve, APCoreMCP
        import apcore
        original_load = apcore.Config.load
        def noisy_load(*args, **kwargs):
            print('config diagnostic')
            return original_load(*args, **kwargs)
        async def run(): pass
        def construct(self, *args, **kwargs): self._registry = object(); self._output_format = None
        with patch.object(apcore.Config, 'load', noisy_load), patch.object(APCoreMCP, '__init__', construct):
            with patch.object(APCoreMCP, '_build_serve_coro', lambda *a, **k: run):
                serve(object())
        print('restored')
    """)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "restored\n"
    assert "config diagnostic" in completed.stderr


def test_real_stdio_protocol_survives_noisy_calls_errors_and_eof() -> None:
    import json
    import queue
    import threading

    root = Path(__file__).parents[1]
    process = subprocess.Popen(
        [sys.executable, str(root / "tests/fixtures/stdio_noise.py")],
        cwd=root,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root)))},
    )
    responses: queue.Queue[str | None] = queue.Queue()
    stdout_lines: list[str] = []

    def read_stdout() -> None:
        for line in process.stdout:
            stdout_lines.append(line)
            responses.put(line)
        responses.put(None)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()

    def send(message: dict) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def request(identifier: int, method: str, params: dict) -> dict:
        send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        while True:
            line = responses.get(timeout=10)
            assert line is not None, f"Server exited before response {identifier}"
            message = json.loads(line)
            assert message["jsonrpc"] == "2.0"
            if message.get("id") == identifier:
                return message

    try:
        assert "result" in request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdio-regression", "version": "1"},
            },
        )
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert "stdio_noise" in [tool["name"] for tool in request(2, "tools/list", {})["result"]["tools"]]
        successful = request(3, "tools/call", {"name": "stdio_noise", "arguments": {}})
        assert not successful["result"].get("isError")
        assert request(4, "tools/call", {"name": "stdio_error", "arguments": {}})["result"]["isError"]
        process.stdin.write("invalid-json\n")
        process.stdin.flush()
        assert "result" in request(5, "tools/list", {})
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        reader.join(timeout=2)
        for line in stdout_lines:
            message = json.loads(line)
            assert message.get("jsonrpc") == "2.0"
            assert "method" in message or ("id" in message and ("result" in message or "error" in message))
        diagnostics = process.stderr.read()
        for marker in (
            "constructor-diagnostic",
            "startup-diagnostic",
            "business-print",
            "business-write",
            "raw-fd-write",
            "held-host-log",
            "trace-noise",
            "event-noise",
            "background-diagnostic",
            "shutdown-diagnostic",
        ):
            assert marker in diagnostics
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("configured", ["stdio", "streamable-http"])
def test_config_bus_transport_override_selects_output_policy(configured: str) -> None:
    requested = "streamable-http" if configured == "stdio" else "stdio"
    completed = run_script(f"""
        from unittest.mock import patch
        import apcore
        from apcore_mcp import serve, APCoreMCP
        class Config:
            def get(self, key): return {configured!r} if key == 'mcp.transport' else None
        async def run(): print('selected runtime')
        def construct(self, *args, **kwargs):
            print('selected constructor'); self._registry = object(); self._output_format = None
        def build(self, **kwargs):
            assert kwargs['transport'] == {configured!r}
            return run
        with (patch.object(apcore.Config, 'load', lambda: Config()),
              patch.object(APCoreMCP, '__init__', construct), patch.object(APCoreMCP, '_build_serve_coro', build)):
            serve(object(), transport={requested!r})
        print('restored')
    """)
    assert completed.returncode == 0, completed.stderr
    lifecycle = "selected constructor\nselected runtime\n"
    assert completed.stdout == ("restored\n" if configured == "stdio" else lifecycle + "restored\n")
    if configured == "stdio":
        assert completed.stderr == lifecycle


def test_failed_fd_restoration_poison_rejects_future_owner() -> None:
    completed = run_script("""
        import os, sys
        from unittest.mock import patch
        from apcore_mcp._stdio import stdio_output
        saved = os.dup(1)
        original_dup2 = os.dup2
        calls = []
        def fail_restore(source, target):
            calls.append((source, target))
            if len(calls) == 2:
                raise OSError('restore failure')
            return original_dup2(source, target)
        try:
            with patch('apcore_mcp._stdio.os.dup2', fail_restore):
                try:
                    with stdio_output(): print('diagnostic')
                except OSError as exc:
                    assert str(exc) == 'restore failure'
                else:
                    raise AssertionError('restoration error was swallowed')
                try:
                    with stdio_output(): pass
                except RuntimeError as exc:
                    assert 'poisoned' in str(exc)
                else:
                    raise AssertionError('stdio reopened on redirected stderr')
                assert len(calls) == 2, 'poisoned owner changed descriptors'
        finally:
            original_dup2(saved, 1)
            os.close(saved)
        print('restored externally')
    """)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "restored externally\n"
    assert "diagnostic" in completed.stderr


def test_failed_flush_with_successful_fd_restore_allows_next_owner() -> None:
    completed = run_script("""
        import sys
        from unittest.mock import patch
        from apcore_mcp._stdio import stdio_output
        original_flush = sys.stdout.flush
        calls = []
        def fail_exit_flush():
            calls.append(None)
            if len(calls) == 2:
                raise OSError('flush failure')
            return original_flush()
        with patch.object(sys.stdout, 'flush', fail_exit_flush):
            try:
                with stdio_output(): pass
            except OSError as exc:
                assert str(exc) == 'flush failure'
            else:
                raise AssertionError('flush error was swallowed')
        with stdio_output() as protocol:
            protocol.write('protocol after flush failure\\n'); protocol.flush()
        print('restored')
    """)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "protocol after flush failure\nrestored\n"
