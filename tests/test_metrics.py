"""B15: Prometheus 指标导出测试"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_metrics_endpoint(client: TestClient):
    """验证 /metrics 端点返回 Prometheus 格式数据"""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "gpt_register_" in resp.text
    assert "register_total" in resp.text


def test_metrics_contains_counters(client: TestClient):
    """验证 /metrics 包含注册计数相关指标"""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "register_total" in resp.text
    assert "register_duration_seconds" in resp.text


def test_metrics_contains_gauges(client: TestClient):
    """验证 /metrics 包含邮箱库 gauge 指标"""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "emails_total" in resp.text
    assert "emails_pending" in resp.text
    assert "accounts_success" in resp.text
    assert "accounts_failed" in resp.text


def test_metrics_contains_engine_state(client: TestClient):
    """验证 /metrics 包含引擎状态 gauge 指标"""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "engine_running" in resp.text
    assert "engine_queue_depth" in resp.text
    assert "engine_concurrency" in resp.text
    assert "proxy_pool_size" in resp.text
    assert "db_file_size_bytes" in resp.text
    assert "last_vacuum_timestamp" in resp.text