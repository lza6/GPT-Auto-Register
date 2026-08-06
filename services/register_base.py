"""双引擎统一抽象（B1：ProtocolRegister + BrowserRegister 共同契约）。

用 typing.Protocol 定义鸭子类型契约，两引擎显式声明实现，
未来加第三引擎只需实现该 Protocol，调度方（register_engine）不变。

零运行时开销：Protocol 是结构性类型，不要求显式继承，
运行时不做 isinstance 检查，仅用于类型提示与静态检查。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RegisterEngineProtocol(Protocol):
    """注册引擎统一契约：注册单个账号，返回标准化结果字典。

    实现方：
    - services.protocol_register.ProtocolRegister（curl_cffi + sentinel，纯协议）
    - services.browser_register.BrowserRegister（camoufox，浏览器兜底）

    返回字典字段（两引擎一致）：
    - email: 邮箱
    - status: success / success_no_token / cf_blocked / failed
    - access_token / refresh_token / id_token / openai_password
    - name / birthdate / proxy
    - error / failure_type / fallback_browser（协议引擎特有）
    """

    def register_one(self, email: str, password: str, client_id: str,
                      refresh_token: str) -> Any:
        """注册单个账号，返回 dict[str, Any]（async 结果）。

        约定返回字段：
            email, status, access_token, refresh_token, id_token,
            openai_password, name, birthdate, proxy, error,
            failure_type, fallback_browser
        """
        ...


def is_register_engine(obj: Any) -> bool:
    """运行时检查对象是否符合 RegisterEngineProtocol（鸭子类型）。

    用于 register_engine 调度方可选的防御性校验。
    """
    return hasattr(obj, "register_one") and callable(getattr(obj, "register_one", None))
