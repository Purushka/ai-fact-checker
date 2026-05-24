"""微博辟谣平台爬虫 — 当前为占位实现。

**为什么是 stub**：
微博辟谣平台（service.account.weibo.com/index?type=5）要求登录账号才能访问详情。
直接 httpx 请求会被 302 重定向到 weibo.com/login.php。

要绕过需要其中一种：
1. **用 cookie 登录**：手动登录后导出 cookie，在 httpx headers 中带 Cookie，但 cookie 30 天左右过期
2. **Playwright 模拟浏览器**：跑无头浏览器登录然后爬，但合规风险大（违反微博服务条款）
3. **微博开放平台 API**：审批严格，普通开发者拿不到辟谣类 API 权限

**当前选择**：暂不爬微博，依赖 piyao.org.cn + 手工补充作为 benchmark 主力。

如果未来需要爬微博辟谣，参考：
- https://service.account.weibo.com/show?rid=<rid>  - 详情页
- 列表按 status (1/2/3/4) 分页：&status=1（待核实）/ 2（已通过）/ 4（已辟谣）
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    print(
        "微博辟谣平台需要登录访问，详见本文件 docstring。\n"
        "当前 benchmark 主力来源：piyao.org.cn + 手工补充。\n"
        "若需爬微博辟谣，请用 Playwright + cookie 方案（合规风险自担）。",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
