"""请求级共享尝试预算（/resolve-matrix 与 /lock-groups）测试。

共享预算语义：同一请求内全部环境的候选尝试次数累计计数，环境按 id 稳定
顺序求解；预算耗尽即停止后续环境求解，整体返回 409（status=limit，附触发
环境、已用次数与上限）。单环境 /resolve 不读取该预算。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

client = TestClient(app)


def _set_budget(monkeypatch, value):
    """把请求级共享预算上限临时调整为 value（路由层每次请求读取模块全局）。"""
    monkeypatch.setattr(main_module, "SHARED_ATTEMPT_BUDGET", value)

ENV_A = {
    "id": "a-env",
    "python_version": "3.11",
    "sys_platform": "linux",
    "platform_machine": "x86_64",
}
ENV_B = {
    "id": "b-env",
    "python_version": "3.9",
    "sys_platform": "win32",
    "platform_machine": "AMD64",
}


def _backtrack_repo():
    """每次求解恰好进行 4 次候选尝试后无解的快照。

    app==1.0（尝试 1）→ dep 的 3/2/1（尝试 2/3/4）各自依赖不存在的
    ghost，全部回溯后以 ghost 的 no_candidates 冲突告终。
    """
    return {
        "app": [
            {"version": "1.0",
             "dependencies": [{"name": "dep", "specifier": ""}]}
        ],
        "dep": [
            {"version": str(v),
             "dependencies": [{"name": "ghost", "specifier": ""}]}
            for v in (3, 2, 1)
        ],
    }


def _solvable_repo():
    """每次求解恰好进行 2 次候选尝试即成功的快照（app==1.0、dep==2）。"""
    return {
        "app": [
            {"version": "1.0",
             "dependencies": [{"name": "dep", "specifier": ""}]}
        ],
        "dep": [{"version": "2"}, {"version": "1"}],
    }


def matrix_payload(environments, repository=None, **overrides):
    base = {
        "repository": repository if repository is not None else _backtrack_repo(),
        "root": "app",
        "specifier": "",
        "allow_prereleases": False,
        "target_environments": environments,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------- #
# /resolve-matrix
# --------------------------------------------------------------------- #
def test_matrix_budget_exhausted_in_first_env(monkeypatch):
    """预算在第一个（按 id 排序）环境求解中耗尽：触发环境即首环境。"""
    _set_budget(monkeypatch, 3)
    r = client.post("/resolve-matrix",
                    json=matrix_payload([ENV_A, ENV_B]))
    assert r.status_code == 409, r.text
    assert r.json() == {
        "status": "limit",
        "message": (
            "请求级共享尝试预算已耗尽：环境 'a-env' 求解中累计尝试 4 个"
            "候选版本，超过上限 3；已停止后续环境求解"
        ),
        "triggered_by": "a-env",
        "attempts": 4,
        "limit": 3,
    }


def test_matrix_budget_exhausted_in_later_env(monkeypatch):
    """首个环境正常跑完（无解），累计计数带入后续环境并在其中耗尽。"""
    _set_budget(monkeypatch, 5)
    r = client.post("/resolve-matrix",
                    json=matrix_payload([ENV_A, ENV_B]))
    assert r.status_code == 409, r.text
    body = r.json()
    # a-env 用掉 4 次后无解；b-env 第 2 次尝试使累计达到 6，超过 5
    assert body["status"] == "limit"
    assert body["triggered_by"] == "b-env"
    assert body["attempts"] == 6
    assert body["limit"] == 5
    # 整体 limit 响应不携带逐环境 failures
    assert "failures" not in body


def test_matrix_budget_response_stable_under_input_reorder(monkeypatch):
    """环境按 id 稳定顺序求解：输入重排后截断位置与响应逐字节一致。"""
    _set_budget(monkeypatch, 5)
    fwd = client.post("/resolve-matrix",
                      json=matrix_payload([ENV_A, ENV_B]))
    rev = client.post("/resolve-matrix",
                      json=matrix_payload([ENV_B, ENV_A]))
    assert fwd.status_code == rev.status_code == 409
    assert fwd.json() == rev.json()


def test_matrix_budget_not_exceeded_keeps_conflict_shape(monkeypatch):
    """预算充足时保持原有 409 冲突形态：逐环境 failures 与冲突来源链。"""
    _set_budget(monkeypatch, 100)
    r = client.post("/resolve-matrix",
                    json=matrix_payload([ENV_A, ENV_B]))
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["status"] == "conflict"
    assert [f["id"] for f in body["failures"]] == ["a-env", "b-env"]
    for failure in body["failures"]:
        assert failure["status"] == "conflict"
        assert failure["conflicts"][0]["subject"] == "ghost"
        assert failure["conflicts"][0]["kind"] == "no_candidates"
        assert failure["conflicts"][0]["constraints"]


def test_matrix_budget_exactly_sufficient_succeeds(monkeypatch):
    """累计尝试恰好等于上限时不触发截断（超过才触发）。"""
    _set_budget(monkeypatch, 4)
    r = client.post("/resolve-matrix",
                    json=matrix_payload([ENV_A, ENV_B],
                                        repository=_solvable_repo()))
    assert r.status_code == 200, r.text
    assert [e["id"] for e in r.json()["results"]] == ["a-env", "b-env"]


def test_matrix_per_env_limit_still_failure_entry(monkeypatch):
    """单环境尝试上限语义不变：超限环境仍作为 failures 条目（status=limit）。

    共享预算充足时，单环境 Resolver 自身的 attempt_limit 先触发，整体响应
    保持 409 conflict + failures 形态，而不是请求级 limit 响应。
    """
    _set_budget(monkeypatch, 1000)
    monkeypatch.setattr("app.resolver.DEFAULT_ATTEMPT_LIMIT", 3)
    r = client.post("/resolve-matrix",
                    json=matrix_payload([ENV_A, ENV_B]))
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["status"] == "conflict"
    assert [f["id"] for f in body["failures"]] == ["a-env", "b-env"]
    for failure in body["failures"]:
        assert failure["status"] == "limit"
        assert failure["conflicts"][0]["subject"] == "ghost"


# --------------------------------------------------------------------- #
# /lock-groups
# --------------------------------------------------------------------- #
def test_lock_groups_budget_exhausted(monkeypatch):
    """逐环境求解阶段共享同一预算：耗尽即整体 409（status=limit）。"""
    _set_budget(monkeypatch, 3)
    r = client.post("/lock-groups", json=matrix_payload([ENV_A, ENV_B]))
    assert r.status_code == 409, r.text
    assert r.json() == {
        "status": "limit",
        "message": (
            "请求级共享尝试预算已耗尽：环境 'a-env' 求解中累计尝试 4 个"
            "候选版本，超过上限 3；已停止后续环境求解"
        ),
        "triggered_by": "a-env",
        "attempts": 4,
        "limit": 3,
    }


def test_lock_groups_budget_response_stable_under_input_reorder(monkeypatch):
    _set_budget(monkeypatch, 5)
    fwd = client.post("/lock-groups", json=matrix_payload([ENV_A, ENV_B]))
    rev = client.post("/lock-groups", json=matrix_payload([ENV_B, ENV_A]))
    assert fwd.status_code == rev.status_code == 409
    assert fwd.json() == rev.json()
    assert fwd.json()["triggered_by"] == "b-env"


def test_lock_groups_budget_exactly_sufficient_succeeds(monkeypatch):
    """两环境各 2 次尝试、预算恰为 4：求解成功并完成锁组选择。"""
    _set_budget(monkeypatch, 4)
    r = client.post("/lock-groups",
                    json=matrix_payload([ENV_A, ENV_B],
                                        repository=_solvable_repo()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    # 两环境锁内容相同，去重后一份锁覆盖全部环境
    assert len(body["groups"]) == 1
    assert body["groups"][0]["environments"] == ["a-env", "b-env"]


def test_lock_groups_budget_not_exceeded_keeps_conflict_shape(monkeypatch):
    _set_budget(monkeypatch, 100)
    r = client.post("/lock-groups", json=matrix_payload([ENV_A, ENV_B]))
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["status"] == "conflict"
    assert [f["id"] for f in body["failures"]] == ["a-env", "b-env"]
    assert body["failures"][0]["conflicts"][0]["subject"] == "ghost"


# --------------------------------------------------------------------- #
# 单环境 /resolve 不读取共享预算
# --------------------------------------------------------------------- #
def test_single_resolve_ignores_shared_budget(monkeypatch):
    """共享预算只作用于多环境端点：/resolve 的现有上限语义不受影响。"""
    _set_budget(monkeypatch, 1)
    r = client.post("/resolve", json={
        "repository": _solvable_repo(),
        "root": "app",
        "specifier": "",
        "allow_prereleases": False,
        "target_environment": {
            "python_version": "3.11",
            "sys_platform": "linux",
            "platform_machine": "x86_64",
        },
    })
    assert r.status_code == 200, r.text
    assert r.json()["locked"] == {"app": "1.0", "dep": "2"}


def test_openapi_documents_both_409_shapes():
    """两个多环境端点的 409 在 OpenAPI 中同时记录 conflict 与 limit 形态。"""
    spec = client.get("/openapi.json").json()
    for path in ("/resolve-matrix", "/lock-groups"):
        r409 = spec["paths"][path]["post"]["responses"]["409"]
        schema = r409["content"]["application/json"]["schema"]
        assert "anyOf" in schema
