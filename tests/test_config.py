"""app.config 环境变量配置解析测试。"""

from __future__ import annotations

import importlib

import pytest

import app.config
from app.resolver import DEFAULT_SHARED_ATTEMPT_BUDGET


def test_budget_default_when_env_unset(monkeypatch):
    monkeypatch.delenv(app.config.SHARED_ATTEMPT_BUDGET_ENV, raising=False)
    assert app.config.load_shared_attempt_budget() == DEFAULT_SHARED_ATTEMPT_BUDGET


def test_budget_valid_env_value(monkeypatch):
    monkeypatch.setenv(app.config.SHARED_ATTEMPT_BUDGET_ENV, "250")
    assert app.config.load_shared_attempt_budget() == 250


@pytest.mark.parametrize("raw", ["", " ", "abc", "1.5", "10万", "0", "-3", "+0"])
def test_budget_invalid_env_value_raises(monkeypatch, raw):
    monkeypatch.setenv(app.config.SHARED_ATTEMPT_BUDGET_ENV, raw)
    with pytest.raises(RuntimeError) as excinfo:
        app.config.load_shared_attempt_budget()
    assert app.config.SHARED_ATTEMPT_BUDGET_ENV in str(excinfo.value)


def test_budget_invalid_env_fails_at_import(monkeypatch):
    """非法配置在模块导入（服务启动）时即失败，而不是处理请求时才暴露。"""
    monkeypatch.setenv(app.config.SHARED_ATTEMPT_BUDGET_ENV, "not-a-number")
    with pytest.raises(RuntimeError):
        importlib.reload(app.config)
    # 恢复干净状态，避免模块残留影响其他测试
    monkeypatch.delenv(app.config.SHARED_ATTEMPT_BUDGET_ENV)
    importlib.reload(app.config)
    assert app.config.SHARED_ATTEMPT_BUDGET == DEFAULT_SHARED_ATTEMPT_BUDGET
