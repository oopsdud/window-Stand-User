import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from windows_use.timers import TimerStore


@pytest.fixture
def store(tmp_path):
    return TimerStore(tmp_path / "timers.sqlite3")


def test_timer_persists_and_is_claimed_once(store):
    row = store.create("查看结果", "vm", ["send"], delay_seconds=30, now=100)
    other = TimerStore(store.path)
    assert other.claim(129) is None
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: other.claim(130), range(2)))
    claimed = [job for job in claims if job]
    assert len(claimed) == 1 and claimed[0]["id"] == row["id"]
    assert claimed[0]["environment"] == "vm" and claimed[0]["risks"] == ["send"]
    other.finish(row["id"], "done")
    assert other.list()[0]["status"] == "done" and other.claim(131) is None


def test_missed_and_interrupted_jobs_are_not_replayed(store):
    store.create("过期", "host", delay_seconds=1, now=100)
    assert store.claim(402) is None
    assert store.list()[0]["status"] == "missed"
    row = store.create("执行中", "host", delay_seconds=2, now=500)
    store.claim(502)
    store.recover()
    assert next(job for job in store.list() if job["id"] == row["id"])["status"] == "interrupted"
    assert store.claim(503) is None


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"delay_seconds": 0},
        {"delay_seconds": float("nan")},
        {"run_at": "2026-11-13T18:30:00"},
        {"run_at": "1970-01-01T00:03:00+00:00", "delay_seconds": 20},
        {"delay_seconds": 31 * 86400},
    ],
)
def test_timer_requires_unambiguous_bounded_time(store, params):
    with pytest.raises(ValueError):
        store.create("任务", "host", now=100, **params)


def test_absolute_time_cancel_and_no_credentials_stored(store):
    row = store.create("查看任务", "host", run_at="1970-01-01T08:03:00+08:00", now=100)
    assert row["due"] == 180
    store.cancel(row["id"])
    assert store.claim(180) is None
    result = json.loads(
        store.tool(
            "create",
            {"api_key": "private-key", "execution_environment": "vm"},
            task="查看结果",
            delay_seconds=60,
        )
    )
    assert "private-key" not in store.path.read_bytes().decode("utf-8", errors="ignore")
    assert result["result"]["environment"] == "vm"
    with pytest.raises(ValueError, match="不能自动创建"):
        store.tool("create", {"_scheduled_timer": "id"}, task="继续创建", delay_seconds=60)


def test_dispatch_waits_for_idle_and_records_failure(store):
    from desktop_app.timer_ui import TimerDispatcher

    called = []
    scheduler = TimerDispatcher(store, lambda job: called.append(job) or True)
    row = store.create("定时任务", "host", delay_seconds=10, now=100)
    scheduler.tick(busy=True, now=110)
    assert not called
    scheduler.tick(now=110)
    scheduler.tick(now=111)
    assert len(called) == 1 and scheduler.active == row["id"]
    scheduler.finish("cancelled", "用户停止")
    assert store.list()[0]["status"] == "cancelled"
    scheduler.launch = lambda job: False
    store.create("配置无效", "host", delay_seconds=10, now=200)
    scheduler.tick(now=210)
    assert scheduler.active is None and store.list()[0]["status"] == "failed"


def test_timer_tool_uses_host_scheduler_even_for_vm():
    from types import SimpleNamespace

    from windows_use.agent.registry.service import Registry
    from windows_use.agent.tools.service import timer_tool
    from windows_use.execution import route
    from windows_use.vm.client import dispatch

    calls = []
    desktop = SimpleNamespace(timer_callback=lambda **params: calls.append(params) or "scheduled")
    registry = Registry([timer_tool])
    params = {
        "thought": "用户要求稍后执行",
        "operation": "create",
        "task": "查看结果",
        "delay_seconds": 10,
    }
    result = dispatch(
        SimpleNamespace(),
        "timer_tool",
        params,
        lambda: registry.execute("timer_tool", params, desktop),
    )
    assert route("timer_tool", params) == "background"
    assert result.is_success and len(calls) == 1


def test_scheduled_launch_keeps_environment_and_does_not_expand_permissions():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from desktop_app.app import Companion

    config = {"execution_environment": "host", "allowed_risks": ["send", "delete"]}
    fake = SimpleNamespace(
        config=config,
        worker=Mock(busy=False),
        desk=Mock(),
        observer=Mock(),
        quick=Mock(),
        pet=Mock(),
        _validate=lambda _: True,
        _begin=Mock(),
        _show_overlays=Mock(),
        save=Mock(side_effect=AssertionError("Do not overwrite user settings")),
        _restore_foreground=Mock(side_effect=AssertionError("Do not switch host focus")),
    )
    job = {"id": "timer", "environment": "vm", "risks": ["send", "system"]}
    assert Companion.run(fake, "检查网页", scheduled=job)
    launched = fake.worker.start.call_args.args[0]
    assert launched["execution_environment"] == "vm"
    assert launched["allowed_risks"] == ["send"]
    assert launched["_scheduled_timer"] == "timer"
    assert config["execution_environment"] == "host"
