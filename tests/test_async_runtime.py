import asyncio

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
