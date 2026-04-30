"""Asyncio helpers for Windows-compatible bulb control."""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import Future, TimeoutError
from threading import Event, Thread
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def configure_event_loop_policy() -> None:
    """Use the selector event loop on Windows for UDP-based bulb traffic."""
    if sys.platform != "win32" or not hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        return

    selector_policy = asyncio.WindowsSelectorEventLoopPolicy
    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, selector_policy):
        asyncio.set_event_loop_policy(selector_policy())


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine from sync code with the correct loop policy."""
    configure_event_loop_policy()
    return asyncio.run(coro)


class BackgroundAsyncLoop:
    """Run a long-lived asyncio event loop in a background thread."""

    def __init__(self) -> None:
        configure_event_loop_policy()
        self._loop = asyncio.new_event_loop()
        self._started = Event()
        self._closed = False
        self._thread = Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        if not self._started.wait(timeout=2.0):
            raise RuntimeError("Background asyncio loop failed to start")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def submit(self, coro: Coroutine[Any, Any, T]) -> Future[T]:
        """Schedule a coroutine on the background loop."""
        if self._closed:
            raise RuntimeError("Background asyncio loop is closed")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Schedule a coroutine and wait for its result."""
        future = self.submit(coro)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise

    async def _cancel_pending_tasks(self) -> None:
        current_task = asyncio.current_task()
        pending_tasks = [
            task
            for task in asyncio.all_tasks()
            if task is not current_task and not task.done()
        ]

        for task in pending_tasks:
            task.cancel()

        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the background loop and cancel any remaining tasks."""
        if self._closed:
            return

        self._closed = True
        if self._loop.is_closed():
            return

        if self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._cancel_pending_tasks(),
                    self._loop,
                )
                future.result(timeout=timeout)
            except (RuntimeError, TimeoutError):
                pass
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=timeout)

        if not self._thread.is_alive() and not self._loop.is_closed():
            self._loop.close()

    def close(self) -> None:
        """Alias for shutdown()."""
        self.shutdown()
