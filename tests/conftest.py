import pytest

from jobquill.core import render


@pytest.fixture(autouse=True)
def no_background_thumbs(monkeypatch):
    """A warm-up thread outliving its test would render into whatever JOBQUILL_HOME is set next (or the real one)."""
    monkeypatch.setattr(render, "warm_thumbs", lambda: None)


@pytest.fixture(autouse=True)
def own_home(tmp_path, monkeypatch):
    """Every test gets its own data folder: nothing (e.g. the AI ledger) ever lands in the person's real one."""
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def no_real_tasks_or_notifications(monkeypatch):
    """Tests never create, change or delete this computer's scheduled tasks, and never pop up a notification on it."""
    from jobquill.core import alerts, scheduler
    tasks = {}
    monkeypatch.setattr(scheduler, "current", lambda: dict(tasks))
    monkeypatch.setattr(scheduler, "_enable", lambda job, at: tasks.__setitem__(job, scheduler.line(job)))
    monkeypatch.setattr(scheduler, "_disable", lambda job: tasks.pop(job, None))
    monkeypatch.setattr(alerts, "desktop", lambda title, body: None)
