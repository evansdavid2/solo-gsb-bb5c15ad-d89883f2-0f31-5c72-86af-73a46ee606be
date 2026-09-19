"""进程级配置：从环境变量读取，在模块导入（服务启动）时完成解析。

非法配置（如非正整数的预算上限）在导入本模块时即抛 RuntimeError，
让进程启动失败，而不是延迟到处理请求时才暴露配置错误。
"""

from __future__ import annotations

import os

from .resolver import DEFAULT_SHARED_ATTEMPT_BUDGET

# /resolve-matrix 与 /lock-groups 的请求级共享尝试预算上限的环境变量名。
SHARED_ATTEMPT_BUDGET_ENV = "RESOLVER_SHARED_ATTEMPT_BUDGET"


def load_shared_attempt_budget() -> int:
    """读取请求级共享尝试预算上限。

    环境变量未设置时返回默认值 DEFAULT_SHARED_ATTEMPT_BUDGET；值不是
    十进制整数、或小于 1 时抛 RuntimeError（启动即失败）。
    """
    raw = os.environ.get(SHARED_ATTEMPT_BUDGET_ENV)
    if raw is None:
        return DEFAULT_SHARED_ATTEMPT_BUDGET
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(
            f"环境变量 {SHARED_ATTEMPT_BUDGET_ENV} 的值 {raw!r} 不是合法整数；"
            "请设置为正整数（候选版本尝试次数上限）"
        ) from None
    if value < 1:
        raise RuntimeError(
            f"环境变量 {SHARED_ATTEMPT_BUDGET_ENV} 必须为正整数，当前为 {value}"
        )
    return value


# 导入时解析一次：非法配置在此即失败（uvicorn 加载应用失败，进程退出）。
SHARED_ATTEMPT_BUDGET = load_shared_attempt_budget()
