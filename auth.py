#!/usr/bin/env python3
"""
财会助手 —— 访问密码校验（PBKDF2-SHA256 哈希，恒定时间比对）

密码配置（二者取其一，均不允许为空）：
  - FINLEDGER_PASSWORD_HASH  生产环境：必须设置，格式 pbkdf2_sha256$迭代次数$盐$哈希
  - FINLEDGER_PASSWORD       本地开发：明文（启动时明确警告，正式部署不建议使用）

登录比对用 secrets.compare_digest，避免时序侧信道；明文密码在启动时立即派生为哈希，
后续比对不再接触明文。
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys

ALGO = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 120_000


def _derive(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """生成自描述密码哈希：pbkdf2_sha256$迭代次数$盐$哈希（盐随机生成）。"""
    if not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_hex(16)
    return f"{ALGO}${iterations}${salt}${_derive(password, salt, iterations)}"


def verify_password(password: str, encoded: str) -> bool:
    """校验密码。格式非法 / 密码错误一律返回 False，不抛异常。"""
    if not password or not encoded:
        return False
    try:
        algo, iterations_s, salt, expected = encoded.split("$", 3)
        iterations = int(iterations_s)
    except (ValueError, AttributeError):
        return False
    if algo != ALGO or iterations <= 0 or not salt or not expected:
        return False
    derived = _derive(password, salt, iterations)
    # compare_digest 恒定时间；长度不同直接返回 False，不抛异常
    return secrets.compare_digest(derived, expected)


def load_password_hash() -> str:
    """解析密码配置，返回用于比对的哈希字符串。

    优先级：FINLEDGER_PASSWORD_HASH（生产）> FINLEDGER_PASSWORD（本地开发，警告）。
    两者都未设置 → 拒绝启动（RuntimeError）。
    """
    env_hash = os.getenv("FINLEDGER_PASSWORD_HASH", "").strip()
    if env_hash:
        return env_hash

    env_plain = os.getenv("FINLEDGER_PASSWORD", "")
    if env_plain:
        print(
            "警告：正在使用明文环境变量 FINLEDGER_PASSWORD，仅限本地开发；"
            "正式部署请改用 FINLEDGER_PASSWORD_HASH（生成方式：python password_tool.py 你的密码）。",
            file=sys.stderr,
        )
        # 明文立即派生为哈希，后续比对不再接触明文
        return hash_password(env_plain)

    raise RuntimeError(
        "未配置访问密码，拒绝启动。\n"
        "生产环境必须设置 FINLEDGER_PASSWORD_HASH（生成：python password_tool.py 你的密码）；\n"
        "本地开发可临时设置 FINLEDGER_PASSWORD（明文，启动会警告，不用于正式部署）。"
    )
