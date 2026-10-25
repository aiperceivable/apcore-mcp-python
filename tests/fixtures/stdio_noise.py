"""Real subprocess fixture: protocol calls with deliberately noisy dependencies."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from apcore.events.emitter import ApCoreEvent
from apcore.events.subscribers import StdoutSubscriber
from apcore.observability.tracing import Span, StdoutExporter

from apcore_mcp import APCoreMCP, serve
from tests.conftest import ModuleDescriptor
from tests.test_serve_params import StubRegistry

host_logger = logging.getLogger("held-stdout-host")
host_logger.setLevel(logging.INFO)
host_logger.addHandler(logging.StreamHandler(sys.stdout))


class NoisyExecutor:
    def __init__(self) -> None:
        self.registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id=name, description=name, input_schema={"type": "object"}, output_schema={"type": "object"}
                )
                for name in ("stdio_noise", "stdio_error")
            ]
        )

    async def call_async(self, module_id: str, inputs: dict, context: object = None, **kwargs: object) -> dict:
        print("business-print")
        sys.stdout.write("business-write\n")
        os.write(1, b"raw-fd-write\n")
        host_logger.info("held-host-log")
        StdoutExporter().export(Span(trace_id="test-trace", name="trace-noise", start_time=0.0))
        await StdoutSubscriber().on_event(ApCoreEvent("event-noise", module_id, "now", "info", {}))

        async def background() -> None:
            await asyncio.sleep(0)
            print("background-diagnostic")

        await asyncio.create_task(background())
        if module_id == "stdio_error":
            raise ValueError("error-diagnostic")
        return {"ok": True}


# Constructor work is deliberately noisy, exercising the public convenience
# entry point's outer scope as well as APCoreMCP.serve's preparation scope.
original_constructor = APCoreMCP.__init__


def noisy_constructor(self: APCoreMCP, *args: object, **kwargs: object) -> None:
    print("constructor-diagnostic")
    original_constructor(self, *args, **kwargs)


APCoreMCP.__init__ = noisy_constructor
serve(
    NoisyExecutor(),
    async_tasks=False,
    on_startup=lambda: print("startup-diagnostic"),
    on_shutdown=lambda: print("shutdown-diagnostic"),
)
