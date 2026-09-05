#!/usr/bin/env python3
"""生成 FINLEDGER_PASSWORD_HASH 的命令行工具。

用法：
    python password_tool.py 你的密码      # 直接传入（会留在 shell 历史，建议交互式）
    python password_tool.py              # 交互式输入（不回显，推荐）
"""
from __future__ import annotations

import getpass
import sys

from auth import hash_password


def main() -> int:
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("请输入密码（不回显）：")
        confirm = getpass.getpass("请再次输入确认：")
        if password != confirm:
            print("两次输入不一致", file=sys.stderr)
            return 1

    try:
        encoded = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("已生成 FINLEDGER_PASSWORD_HASH，请写入 .env 文件（已被 .gitignore 忽略，勿提交）")
    print("或受权限保护的服务环境配置中。切勿直接粘贴到命令行：")
    print("会进入 shell 历史，且进程启动期间可能被同机用户看到。")
    print()
    print(f"FINLEDGER_PASSWORD_HASH='{encoded}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
