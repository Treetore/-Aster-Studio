"""proc_cleaner.py 的离线安全测试：owned 登记、保护规则、收尾只杀自己的 encoder。

原则：
* 全部用假进程对象 + 打桩扫描，**不会真杀任何进程**。
* 日志/设置写入 ``tmp_path``，不污染项目 ``data/`` ``logs/``。
* 线程测试用打桩的 ``cleanup``，不启动真实扫描。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from comfy_mcp import proc_cleaner as pc  # noqa: E402

FAKE_PID_BASE = 900000


class FakeProc:
    """够用的 psutil.Process 替身：只暴露 info / kill / environ。"""

    def __init__(self, pid: int, name: str = "ffmpeg.exe", cmdline=None,
                 create_time: float | None = None, cpu: float = 0.0, env=None):
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "cmdline": list(cmdline if cmdline is not None else [name]),
            "create_time": time.time() if create_time is None else create_time,
            "cpu_percent": cpu,
        }
        self._env = dict(env or {})
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    def environ(self) -> dict:
        return dict(self._env)


@pytest.fixture()
def cleaner(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    log = tmp_path / "process_cleanup.log"
    settings.write_text(
        json.dumps({"process_cleanup": {"enabled": True, "auto_stop_encode": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pc, "SETTINGS", settings)
    monkeypatch.setattr(pc, "LOG", log)
    monkeypatch.setattr(pc, "_pid_exists", lambda _pid: False)
    pc.reset_state()
    pc._stop.clear()
    yield {"settings": settings, "log": log, "tmp": tmp_path}
    pc.reset_state()


def patch_scan(monkeypatch, procs):
    """让 process_iter 每次返回同一批假进程（保护扫描与清理扫描共用）。"""

    def fake_iter(_attrs=None):
        return iter(list(procs))

    monkeypatch.setattr(pc.psutil, "process_iter", fake_iter)


def register_fresh(pid: int, kind: str = "encoder", job_id: str | None = None) -> None:
    assert pc.register_owned(pid, kind=kind, job_id=job_id) is True


def make_old(pid: int, seconds: float = 10_000.0) -> None:
    pc._owned[pid]["registered_at"] -= seconds


def test_unowned_ffmpeg_is_never_killed(cleaner, monkeypatch):
    user_ffmpeg = FakeProc(FAKE_PID_BASE + 1, "ffmpeg.exe", ["ffmpeg.exe", "-i", "movie.mp4"],
                           create_time=time.time() - 9999, cpu=0.0)
    patch_scan(monkeypatch, [user_ffmpeg])

    assert pc.cleanup(force_owned=True) == []
    assert user_ffmpeg.killed is False
    assert pc.cleanup() == []
    assert user_ffmpeg.killed is False


def test_owned_encoder_killed_on_finish_and_unregistered(cleaner, monkeypatch):
    owned = FakeProc(FAKE_PID_BASE + 2, "ffmpeg.exe", ["ffmpeg.exe", "-i", "src.mp4"])
    patch_scan(monkeypatch, [owned])
    register_fresh(owned.pid)

    killed = pc.cleanup("finish", force_owned=True, job_id="job-1")
    assert killed == [owned.pid]
    assert owned.killed is True
    assert pc.owned_pids() == set()


def test_fresh_owned_survives_idle_cleanup(cleaner, monkeypatch):
    owned = FakeProc(FAKE_PID_BASE + 3, "ffmpeg.exe", ["ffmpeg.exe", "-i", "src.mp4"])
    patch_scan(monkeypatch, [owned])
    register_fresh(owned.pid)

    assert pc.cleanup() == []
    assert owned.killed is False
    assert pc.is_owned(owned.pid) is True


def test_stale_owned_killed_by_idle_cleanup(cleaner, monkeypatch):
    owned = FakeProc(FAKE_PID_BASE + 4, "ffmpeg.exe", ["ffmpeg.exe", "-i", "src.mp4"], cpu=0.5)
    patch_scan(monkeypatch, [owned])
    register_fresh(owned.pid)
    make_old(owned.pid)

    assert pc.cleanup() == [owned.pid]
    assert owned.killed is True
    assert pc.owned_pids() == set()


def test_owned_busy_encoder_not_idle_killed(cleaner, monkeypatch):
    owned = FakeProc(FAKE_PID_BASE + 5, "ffmpeg.exe", ["ffmpeg.exe", "-i", "src.mp4"], cpu=42.0)
    patch_scan(monkeypatch, [owned])
    register_fresh(owned.pid)
    make_old(owned.pid)

    assert pc.cleanup() == []
    assert owned.killed is False


def test_owned_orphan_from_previous_job_is_cleaned(cleaner, monkeypatch):
    owned = FakeProc(FAKE_PID_BASE + 6, "ffmpeg.exe", ["ffmpeg.exe", "-i", "old.mp4"])
    patch_scan(monkeypatch, [owned])
    register_fresh(owned.pid, job_id="job-old")
    monkeypatch.setattr(pc, "_active_job", "job-new")
    make_old(owned.pid)

    assert pc.cleanup() == [owned.pid]
    assert owned.killed is True


def test_finish_does_not_kill_other_jobs_fresh_encoder(cleaner, monkeypatch):
    """并发场景：给 job-B 收尾时，job-A 刚起的编码器不能被 force 杀掉。"""
    other = FakeProc(FAKE_PID_BASE + 20, "ffmpeg.exe", ["ffmpeg.exe", "-i", "a.mp4"])
    patch_scan(monkeypatch, [other])
    register_fresh(other.pid, job_id="job-A")
    monkeypatch.setattr(pc, "_active_job", "job-A")

    # 切到 job-B 后再收尾：job-A 的进程是「跨任务」但还新鲜，必须放过
    monkeypatch.setattr(pc, "_active_job", "job-B")
    assert pc.cleanup("finish-B", force_owned=True, job_id="job-B") == []
    assert other.killed is False

    # 闲置超过宽限期后才作为孤儿清理
    make_old(other.pid)
    assert pc.cleanup() == [other.pid]


def test_protected_services_are_kept_even_if_owned(cleaner, monkeypatch, tmp_path):
    comfy = FakeProc(FAKE_PID_BASE + 7, "python.exe",
                     ["python.exe", r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\main.py"])
    segment = FakeProc(FAKE_PID_BASE + 8, "python.exe",
                       ["python.exe", r"K:\CONFI\comfy_mcp\tools\segment_film.py"])
    patch_scan(monkeypatch, [comfy, segment])
    register_fresh(comfy.pid)
    register_fresh(segment.pid, kind="tool")

    assert pc.cleanup(force_owned=True) == []
    assert comfy.killed is False and segment.killed is False


def test_project_tagged_python_tool_cleanup_rules(cleaner, monkeypatch):
    tagged_old = FakeProc(
        FAKE_PID_BASE + 9, "python.exe",
        ["python.exe", str(pc.ROOT / "tools" / "auto_film.py")],
        create_time=time.time() - (pc.TAGGED_PYTHON_IDLE_S + 600), cpu=0.1,
    )
    tagged_fresh = FakeProc(
        FAKE_PID_BASE + 10, "python.exe",
        ["python.exe", str(pc.ROOT / "tools" / "auto_film.py")],
        create_time=time.time() - 30, cpu=0.1,
    )
    untagged_old = FakeProc(
        FAKE_PID_BASE + 11, "python.exe",
        ["python.exe", r"C:\Users\someone\job.py"],
        create_time=time.time() - (pc.TAGGED_PYTHON_IDLE_S + 600), cpu=0.1,
    )
    patch_scan(monkeypatch, [tagged_old, tagged_fresh, untagged_old])

    killed = pc.cleanup()
    assert killed == [tagged_old.pid]
    assert tagged_fresh.killed is False
    assert untagged_old.killed is False


def test_environment_tag_marks_project_process(cleaner, monkeypatch):
    tagged = FakeProc(FAKE_PID_BASE + 12, "powershell.exe",
                      ["powershell.exe", "-File", "C:\\somewhere\\tunnel.ps1"],
                      create_time=time.time() - (pc.TAGGED_TUNNEL_IDLE_S + 60),
                      env={pc.OWNED_TAG_ENV: "job-1"})
    patch_scan(monkeypatch, [tagged])

    assert pc.cleanup() == [tagged.pid]


def test_disabled_cleaner_does_nothing(cleaner, monkeypatch):
    cleaner["settings"].write_text(
        json.dumps({"process_cleanup": {"enabled": False}}), encoding="utf-8")
    owned = FakeProc(FAKE_PID_BASE + 13, "ffmpeg.exe", ["ffmpeg.exe"])
    patch_scan(monkeypatch, [owned])
    register_fresh(owned.pid)

    assert pc.cleanup(force_owned=True) == []
    assert owned.killed is False
    assert pc.start_for_job("job-x", job_id="job-x")["enabled"] is False
    assert pc.status()["running"] is False


def test_register_owned_validation(cleaner):
    assert pc.register_owned(0) is False
    assert pc.register_owned(-5) is False
    assert pc.register_owned(os.getpid()) is False
    assert pc.register_owned("not-a-pid") is False

    assert pc.register_owned(FAKE_PID_BASE + 14, kind="encoder", job_id="job-a") is True
    assert pc.is_owned(FAKE_PID_BASE + 14) is True
    assert pc.owned_record(FAKE_PID_BASE + 14)["job_id"] == "job-a"
    assert pc.unregister_owned(FAKE_PID_BASE + 14) is True
    assert pc.unregister_owned(FAKE_PID_BASE + 14) is False


def test_finish_job_only_kills_owned(cleaner, monkeypatch):
    owned = FakeProc(FAKE_PID_BASE + 15, "ffmpeg.exe", ["ffmpeg.exe", "-i", "ours.mp4"])
    user = FakeProc(FAKE_PID_BASE + 16, "ffmpeg.exe", ["ffmpeg.exe", "-i", "user.mp4"],
                    create_time=time.time() - 9999)
    patch_scan(monkeypatch, [owned, user])
    register_fresh(owned.pid, job_id="job-2")

    result = pc.finish_job("job-2", job_id="job-2")
    assert result["ok"] is True and result["stopped"] is True
    assert result["killed"] == [owned.pid]
    assert owned.killed is True and user.killed is False


def test_no_duplicate_cleaner_threads(cleaner, monkeypatch):
    monkeypatch.setattr(pc, "cleanup", lambda *a, **k: [])

    first = pc.start_for_job("job-t", job_id="job-t")
    thread = pc._thread
    second = pc.start_for_job("job-t", job_id="job-t")

    assert first["started"] is True
    assert second["started"] is False and second["already_running"] is True
    assert pc._thread is thread
    assert thread is not None and thread.is_alive()
    assert pc.status()["running"] is True

    finished = pc.finish_job("job-t", job_id="job-t")
    assert finished["stopped"] is True
    assert finished["thread_alive"] is False
    assert pc._thread is None
    assert pc.status()["running"] is False


def test_finish_job_is_idempotent(cleaner, monkeypatch):
    monkeypatch.setattr(pc, "cleanup", lambda *a, **k: [])
    pc.start_for_job("job-i", job_id="job-i")
    first = pc.finish_job("job-i", job_id="job-i")
    second = pc.finish_job("job-i", job_id="job-i")
    assert first["stopped"] is True and second["stopped"] is True
    assert second["killed"] == []


def test_pid_reuse_is_not_killed(cleaner, monkeypatch):
    """登记过 ffmpeg，但 PID 已被系统复用给别的程序：必须放过。"""
    reused = FakeProc(FAKE_PID_BASE + 17, "notepad.exe", ["notepad.exe", "notes.txt"])
    patch_scan(monkeypatch, [reused])
    assert pc.register_owned(reused.pid, kind="encoder", job_id="job-r",
                             cmdline="ffmpeg.exe -i src.mp4") is True

    assert pc.cleanup(force_owned=True) == []
    assert reused.killed is False


def test_owned_without_cmdline_only_kills_encoder_images(cleaner, monkeypatch):
    """没有命令行证据时，只有真正的 ffmpeg/ffprobe 镜像才允许被强杀。"""
    python = FakeProc(FAKE_PID_BASE + 18, "python.exe", ["python.exe", "helper.py"])
    ffmpeg = FakeProc(FAKE_PID_BASE + 19, "ffmpeg.exe", [])
    patch_scan(monkeypatch, [python, ffmpeg])
    register_fresh(python.pid)
    register_fresh(ffmpeg.pid)

    killed = pc.cleanup(force_owned=True)
    assert killed == [ffmpeg.pid]
    assert python.killed is False and ffmpeg.killed is True


def test_log_rotation_size_based(cleaner, tmp_path):
    log = tmp_path / "big.log"
    log.write_text("x" * 5000, encoding="utf-8")

    first = pc.maybe_rotate_log(log, max_bytes=1000, backups=2)
    assert first["rotated"] is True
    assert first["size"] == 5000
    assert (tmp_path / "big.log.1").is_file()
    assert log.exists() is False

    log.write_text("y" * 2000, encoding="utf-8")
    second = pc.maybe_rotate_log(log, max_bytes=1000, backups=2)
    assert second["rotated"] is True
    assert (tmp_path / "big.log.2").is_file()
    assert (tmp_path / "big.log.1").read_text(encoding="utf-8").startswith("y")

    log.write_text("z" * 10, encoding="utf-8")
    third = pc.maybe_rotate_log(log, max_bytes=1000, backups=2)
    assert third["rotated"] is False
    assert log.exists() is True


def test_maybe_rotate_log_missing_file(cleaner, tmp_path):
    result = pc.maybe_rotate_log(tmp_path / "absent.log", max_bytes=10)
    assert result["rotated"] is False
    assert result["size"] == 0


def test_log_helper_writes_and_rotates(cleaner):
    pc._log("first line")
    assert cleaner["log"].is_file()
    assert "first line" in cleaner["log"].read_text(encoding="utf-8")
