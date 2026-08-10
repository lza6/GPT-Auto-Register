"""Tests for registration_progress module."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from services.registration_progress import (
    PROGRESS_PATH,
    RegistrationProgress,
    registration_stage,
    track_registration,
)


class TestRegistrationProgress:
    def test_init_defaults(self):
        p = RegistrationProgress()
        assert p.run_id is not None
        assert len(p.run_id) == 32  # uuid4 hex
        assert p.email == ""
        assert p.started_at > 0
        assert len(p.events) == 1
        assert p.events[0]["stage"] == "started"
        assert p.events[0]["status"] == "running"
        assert p.last_stage == "started"

    def test_init_with_email(self):
        p = RegistrationProgress("test@example.com")
        assert p.email == "test@example.com"

    def test_stage_appends_event(self):
        p = RegistrationProgress()
        p.stage("authorize", "running", "sending request")
        assert len(p.events) == 2
        assert p.events[1]["stage"] == "authorize"
        assert p.events[1]["status"] == "running"
        assert p.events[1]["detail"] == "sending request"
        assert p.last_stage == "authorize"

    def test_stage_timestamp(self):
        p = RegistrationProgress()
        before = int(time.time())
        p.stage("phase2")
        after = int(time.time())
        assert before <= p.events[1]["at"] <= after

    def test_stage_detail_truncated(self):
        p = RegistrationProgress()
        long_detail = "x" * 500
        p.stage("test", "running", long_detail)
        # detail is sanitized then truncated to 240 chars
        assert len(p.events[1]["detail"]) <= 240

    def test_stage_detail_sanitized(self):
        p = RegistrationProgress()
        p.stage("test", "running", "api_key=sk-abc123")
        assert "sk-abc123" not in p.events[1]["detail"]
        assert "[REDACTED]" in p.events[1]["detail"]

    def test_snapshot_isolation(self):
        p = RegistrationProgress()
        p.stage("authorize")
        snap = p.snapshot()
        assert snap["run_id"] == p.run_id
        assert snap["last_stage"] == p.last_stage
        assert snap["started_at"] == p.started_at
        assert len(snap["events"]) == 2
        # snapshot should be a copy, not a reference
        p.stage("extra")
        assert len(snap["events"]) == 2  # unchanged

    def test_persist_success_creates_jsonl(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        # Override PROGRESS_PATH for this test
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            p = RegistrationProgress("a@b.com")
            p.stage("authorize", "running")
            result = {"email": "a@b.com", "status": "success", "access_token": "sk-test"}
            p.persist(result)

            assert progress_path.exists()
            lines = progress_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1
            row = json.loads(lines[0])
            assert row["run_id"] == p.run_id
            assert row["email"] == "a@b.com"
            assert row["success"] is True
            assert row["error"] == ""
            assert row["started_at"] == p.started_at
            assert row["finished_at"] >= row["started_at"]
            assert row["last_stage"] == "completed"
        finally:
            rp_mod.PROGRESS_PATH = original_path

    def test_persist_failure_creates_jsonl(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            p = RegistrationProgress("fail@b.com")
            result = {"email": "fail@b.com", "status": "failed", "error": "network error"}
            p.persist(result)

            lines = progress_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1
            row = json.loads(lines[0])
            assert row["run_id"] == p.run_id
            assert row["success"] is False
            assert "network error" in row["error"]
            assert row["last_stage"] == "failed"
        finally:
            rp_mod.PROGRESS_PATH = original_path

    def test_persist_sanitizes_sensitive_data(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            p = RegistrationProgress("secret@b.com")
            result = {
                "email": "secret@b.com",
                "status": "failed",
                "error": "refresh_token=flp_test_abc123 failed",
            }
            p.persist(result)

            row = json.loads(progress_path.read_text(encoding="utf-8").strip())
            # Sensitive patterns should be redacted
            assert "flp_test_" not in row["error"]
            assert "[REDACTED]" in row["error"] or "REDACTED" in row["error"]
        finally:
            rp_mod.PROGRESS_PATH = original_path

    def test_persist_without_result(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            p = RegistrationProgress("noresult@b.com")
            p.persist(None, "unexpected crash")

            row = json.loads(progress_path.read_text(encoding="utf-8").strip())
            assert row["run_id"] == p.run_id
            assert row["success"] is False
            assert "unexpected crash" in row["error"]
        finally:
            rp_mod.PROGRESS_PATH = original_path

    def test_persist_thread_safe(self, tmp_path):
        """多线程并发写入 JSONL，验证不丢失数据且行数正确。"""
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            import concurrent.futures

            def _write_progress(seq: int) -> None:
                p = RegistrationProgress(f"thr{seq}@b.com")
                result = {"email": f"thr{seq}@b.com", "status": "success" if seq % 2 == 0 else "failed"}
                p.persist(result)

            n = 20
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(_write_progress, range(n)))

            lines = progress_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == n
            run_ids = {json.loads(l)["run_id"] for l in lines}
            assert len(run_ids) == n  # 全部唯一
        finally:
            rp_mod.PROGRESS_PATH = original_path


class TestRegistrationStage:
    def test_registration_stage_noop_outside_context(self):
        """在 @track_registration 装饰外调用 registration_stage 应为无操作。"""
        # 不应抛出异常
        registration_stage("test", "running", "detail")
        # 验证通过即为成功

    def test_registration_stage_exception(self, tmp_path):
        """@track_registration 包装的函数抛出异常时，进度仍应持久化。"""
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            @track_registration
            def failing_func(email: str) -> dict:
                registration_stage("phase1", "running")
                msg = "something went wrong"
                raise ValueError(msg)

            with pytest.raises(ValueError, match="something went wrong"):
                failing_func(email="crash@b.com")

            lines = progress_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1
            row = json.loads(lines[0])
            assert row["email"] == "crash@b.com"
            assert row["success"] is False
            assert "something went wrong" in row["error"]
        finally:
            rp_mod.PROGRESS_PATH = original_path


class TestTrackRegistration:
    def test_track_sync_success(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            @track_registration
            def sync_register(email: str, password: str) -> dict:
                registration_stage("authorize", "running", "sending")
                registration_stage("otp", "success", "code received")
                return {"email": email, "status": "success", "access_token": "sk-test"}

            result = sync_register(email="sync@b.com", password="pwd123")
            assert result["status"] == "success"
            assert "registration_progress" in result
            snap = result["registration_progress"]
            assert snap["last_stage"] == "completed"
            assert len(snap["events"]) >= 3  # started + authorize + otp + completed

            # 验证 JSONL 已写入
            lines = progress_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1
            row = json.loads(lines[0])
            assert row["run_id"] == snap["run_id"]
            assert row["success"] is True
        finally:
            rp_mod.PROGRESS_PATH = original_path

    def test_track_sync_failure(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            @track_registration
            def sync_fail(email: str) -> dict:
                registration_stage("authorize", "running")
                return {"email": email, "status": "failed", "error": "rate limited"}

            result = sync_fail(email="fail@b.com")
            assert result["status"] == "failed"
            assert "registration_progress" in result
            snap = result["registration_progress"]
            assert snap["last_stage"] == "failed"

            row = json.loads(progress_path.read_text(encoding="utf-8").strip())
            assert row["success"] is False
            assert "rate limited" in row["error"]
        finally:
            rp_mod.PROGRESS_PATH = original_path

    @pytest.mark.asyncio
    async def test_track_async_success(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            @track_registration
            async def async_register(email: str) -> dict:
                registration_stage("async_step", "running")
                return {"email": email, "status": "success", "access_token": "sk-async"}

            result = await async_register(email="async@b.com")
            assert result["status"] == "success"
            assert "registration_progress" in result
            snap = result["registration_progress"]
            assert snap["last_stage"] == "completed"

            row = json.loads(progress_path.read_text(encoding="utf-8").strip())
            assert row["success"] is True
        finally:
            rp_mod.PROGRESS_PATH = original_path

    @pytest.mark.asyncio
    async def test_track_async_failure(self, tmp_path):
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            @track_registration
            async def async_fail(email: str) -> dict:
                msg = "async network error"
                raise RuntimeError(msg)

            with pytest.raises(RuntimeError, match="async network error"):
                await async_fail(email="async_fail@b.com")

            row = json.loads(progress_path.read_text(encoding="utf-8").strip())
            assert row["success"] is False
            assert "async network error" in row["error"]
        finally:
            rp_mod.PROGRESS_PATH = original_path

    def test_track_registration_stage_inside_decorator(self, tmp_path):
        """验证 @track_registration 内调用 registration_stage 正确关联到当前进度。"""
        progress_path = tmp_path / "registration_progress.jsonl"
        import services.registration_progress as rp_mod

        original_path = rp_mod.PROGRESS_PATH
        try:
            rp_mod.PROGRESS_PATH = progress_path

            @track_registration
            def multi_stage(email: str) -> dict:
                registration_stage("stage_auth", "running")
                registration_stage("stage_otp", "running", "waiting for code")
                registration_stage("stage_otp", "success", "code=123456")
                registration_stage("stage_create", "running")
                return {"email": email, "status": "success", "access_token": "sk-multi"}

            result = multi_stage(email="multi@b.com")
            snap = result["registration_progress"]
            stages = [e["stage"] for e in snap["events"]]
            assert "started" in stages
            assert "stage_auth" in stages
            assert "stage_otp" in stages
            assert "stage_create" in stages
            assert "completed" in stages
        finally:
            rp_mod.PROGRESS_PATH = original_path