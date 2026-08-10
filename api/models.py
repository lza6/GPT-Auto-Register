"""Pydantic response models — 供 OpenAPI 文档自动生成。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    """通用 API 响应基类。"""
    success: bool = True
    error: Optional[str] = None


class HealthzResponse(BaseModel):
    """健康检查响应。"""
    status: str
    db: str
    cf_solver: str
    browser_pool_size: int
    auth: str
    auth_enforced: bool
    version: str


class RegisterStatusResponse(BaseModel):
    """注册任务状态。"""
    is_running: bool
    is_paused: bool
    stats: dict
    proxy_count: int
    task: Optional[dict] = None


class AccountListResponse(BaseModel):
    """账号列表响应。"""
    accounts: list[dict]
    total: int


class EmailListResponse(BaseModel):
    """邮箱列表响应。"""
    emails: list[dict]
    total: int


class ClearResponse(BaseModel):
    """清空操作响应。"""
    success: bool
    deleted: Optional[dict] = None
    backup: Optional[str] = None


class LogListResponse(BaseModel):
    """日志列表响应。"""
    logs: list[dict]
    total: int


class SettingsResponse(BaseModel):
    """配置项响应。"""
    success: bool = True


class ProxyListResponse(BaseModel):
    """代理列表响应。"""
    content: str
    count: int
    active_count: int