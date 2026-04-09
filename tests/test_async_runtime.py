import asyncio
from concurrent.futures import TimeoutError

from src.core.async_runtime import BackgroundAsyncLoop, run_sync


async def _double(value: int) -> int:
    await asyncio.sleep(0)
    return value * 2


def test_run_sync_executes_coroutine():
    assert run_sync(_double(21)) == 42


def test_background_async_loop_executes_coroutines():
    runner = BackgroundAsyncLoop()

    try:
        future = runner.submit(_double(21))
        assert future.result(timeout=2) == 42
        assert runner.run(_double(5), timeout=2) == 10
    finally:
        runner.shutdown()


def test_background_async_loop_cancels_future_on_timeout():
    runner = BackgroundAsyncLoop()

    async def _slow() -> int:
        await asyncio.sleep(0.5)
        return 1

    try:
        try:
            runner.run(_slow(), timeout=0.01)
            assert False, "Expected a timeout"
        except TimeoutError:
            pass

        future = runner.submit(asyncio.sleep(0, result=7))
        assert future.result(timeout=2) == 7
    finally:
        runner.shutdown()
