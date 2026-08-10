"""表单交互逻辑（browser_register 子模块）。

提取自 browser_register.py 的邮箱/密码/验证码/姓名/生日/Cookie 等表单交互逻辑，
保持向后兼容（BrowserRegister 保留同名委托方法）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from services.browser_selectors import (
    COOKIE_REJECT_SELECTORS,
    EMAIL_FALLBACK,
    EMAIL_PRIMARY,
    NAME_FALLBACK,
    NAME_PRIMARY,
    NEW_PASSWORD_PRIMARY,
    OTP_FALLBACK,
    OTP_PRIMARY,
    SUBMIT_FALLBACK,
    SUBMIT_PRIMARY,
    SWITCH_OTP_SELECTORS,
)
from services.db import add_log


async def close_cookie_dialog(page) -> None:
    """关闭 cookie 弹窗（如果有的话）。"""
    try:
        cookie_reject = page.locator(", ".join(COOKIE_REJECT_SELECTORS)).first
        if await cookie_reject.count() > 0:
            await cookie_reject.click()
            add_log("info", "已关闭 cookie 弹窗")
            await asyncio.sleep(2)
    except Exception:
        pass


async def fill_email_input(page, email: str, email_input_sel: str = EMAIL_PRIMARY) -> bool:
    """输入邮箱到输入框。返回是否成功。"""
    email_input = page.locator(email_input_sel)
    try:
        await email_input.wait_for(state="visible", timeout=8000)
    except Exception:
        # 尝试兜底选择器
        email_input = page.locator(EMAIL_FALLBACK).first
        try:
            await email_input.wait_for(state="visible", timeout=8000)
        except Exception:
            return False

    await email_input.click()
    await asyncio.sleep(0.3)
    await email_input.fill("")
    await page.keyboard.type(email, delay=50)
    return True


async def press_continue(page, primary_sel: str = SUBMIT_PRIMARY) -> None:
    """点击 Continue / 提交按钮。"""
    continue_btn = page.locator(primary_sel)
    try:
        await continue_btn.wait_for(state="visible", timeout=10000)
        await continue_btn.click(timeout=10000)
    except Exception as e:
        add_log("warning", f"Continue 按钮点击异常: {e}，尝试 Enter...")
        await page.keyboard.press("Enter")


async def switch_to_otp_login(page, email: str) -> bool:
    """密码登录页 → 切换到邮箱验证码登录。返回是否成功。"""
    add_log("info", f"[{email}] 密码登录页，切换邮箱验证码登录...")
    clicked = False
    for sel in SWITCH_OTP_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.count():
                await btn.click(timeout=5000)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        return False
    add_log("info", f"[{email}] 已切换到邮箱验证码登录")
    await asyncio.sleep(3)
    return True


async def set_openai_password(page, email: str) -> str:
    """在新账号注册页设置 OpenAI 密码。返回设置的密码字符串，失败返回空字符串。"""
    openai_password = ""
    from services.browser_register import gen_password
    openai_password = gen_password()
    add_log("info", f"[{email}] 检测到新账号注册页，设置 OpenAI 密码...")
    try:
        new_pw = page.locator(NEW_PASSWORD_PRIMARY)
        await new_pw.wait_for(state="visible", timeout=10000)
        await new_pw.click()
        await new_pw.fill("")
        await page.keyboard.type(openai_password, delay=30)
        await asyncio.sleep(0.5)
        await page.locator(SUBMIT_PRIMARY).first.click(timeout=10000)
        add_log("info", f"[{email}] ✅ 已设置 OpenAI 密码 (可账号密码登录)")
        await asyncio.sleep(3)
    except Exception as e:
        add_log("warning", f"[{email}] 设置密码失败: {e}")
        return ""
    return openai_password


async def fill_otp_code(page, otp_code: str) -> None:
    """输入验证码。"""
    code_input = page.locator('input[name="code"]')
    try:
        await code_input.wait_for(state="visible", timeout=10000)
    except Exception:
        code_input = page.locator(OTP_FALLBACK).first
    await code_input.click()
    await code_input.fill("")
    await page.keyboard.type(otp_code, delay=100)
    add_log("info", "验证码已输入")
    await asyncio.sleep(0.5)

    verify_btn = page.locator(SUBMIT_PRIMARY).first
    if await verify_btn.count() == 0:
        verify_btn = page.locator(SUBMIT_FALLBACK).first
    await verify_btn.click()
    add_log("info", "已提交验证码")


async def fill_name(page, name: str, email: str) -> bool:
    """输入姓名。返回是否成功。"""
    name_input = page.locator(NAME_PRIMARY)
    try:
        await name_input.wait_for(state="visible", timeout=10000)
    except Exception:
        name_input = page.locator(NAME_FALLBACK).first
        try:
            await name_input.wait_for(state="visible", timeout=5000)
        except Exception:
            return False

    await name_input.click()
    await name_input.fill("")
    await page.keyboard.type(name, delay=50)
    add_log("info", f"[{email}] 姓名已输入")
    await asyncio.sleep(0.5)
    return True


async def fill_age(page, birthdate: str) -> None:
    """输入年龄（about-you 页面）。"""
    from datetime import datetime
    birth_year = int(birthdate.split("-")[0])
    current_year = datetime.now().year
    age = current_year - birth_year
    try:
        await page.evaluate(f"""() => {{
            const ageInput = document.querySelector('input[name="age"]');
            if (ageInput) {{
                ageInput.value = '{age}';
                ageInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                ageInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
        }}""")
        add_log("info", f"年龄已输入: {age}")
    except Exception as e:
        add_log("warning", f"年龄输入异常: {e}")


async def set_birthdate(page, birthdate: str) -> None:
    """设置隐藏的生日字段。"""
    try:
        await page.evaluate(f"() => {{ const b = document.querySelector('input[name=\"birthday\"]'); if (b) b.value = '{birthdate}'; }}")
        add_log("info", f"生日已设置: {birthdate}")
    except Exception:
        pass


async def submit_finish(page) -> None:
    """点击 Finish creating account 按钮。"""
    finish_btn = page.locator(SUBMIT_PRIMARY).first
    try:
        await finish_btn.wait_for(state="visible", timeout=10000)
        await finish_btn.click()
    except Exception:
        await page.keyboard.press("Enter")
    add_log("info", "已提交姓名和生日")


async def retry_email_submit(page, email: str) -> None:
    """重试输入邮箱并提交（验证码页跳转失败时）。"""
    add_log("info", f"[{email}] 重试提交邮箱...")
    try:
        email_input2 = page.locator(EMAIL_PRIMARY)
        await email_input2.wait_for(state="visible", timeout=10000)
        await email_input2.click()
        await email_input2.fill("")
        await page.keyboard.type(email, delay=50)
        await asyncio.sleep(0.5)
        continue_btn2 = page.locator(SUBMIT_PRIMARY)
        await continue_btn2.click(timeout=10000)
    except Exception as e2:
        add_log("warning", f"[{email}] 重试提交异常: {e2}")