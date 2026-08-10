"""通知器（stub）：用于注册引擎批量完成/高失败率告警。"""
from __future__ import annotations

from typing import Any


class Notifier:
    """极简告警通知器。当前为桩实现，日志输出为主。"""

    async def on_batch_done(self, stats: dict[str, Any]) -> None:
        pass

    async def on_high_fail_rate(self, stats: dict[str, Any]) -> None:
        pass


notifier = Notifier()