# tests/test_kernel_guard.py
"""kernel 路径 ID 守卫单元测试（审计 F01 单一实现来源）。"""

from pathlib import Path

import pytest
from fieldwork_agent.kernel.repository.guard import (
    owner_violation,
    path_id_violation,
    project_id_violation,
    turn_id_violation,
)


class _Repo:
    """最小仓库替身：只为提供 root 属性（Protocol 替身无 root 时只靠字符校验）。"""

    def __init__(self, root: Path):
        self.root = root


@pytest.mark.parametrize("bad", [
    "",                                   # 空串
    "../../evil_rel",                     # 相对穿越
    "..\\..\\evil",                       # 反斜杠穿越
    "sub/dir", "sub\\dir",                # 内嵌分隔符
    "C:evil", "c:\\windows",             # 盘符（含盘符相对路径）
    ".", "..",                            # 相对路径段（整值匹配，非子串）
    "a\x00b",                             # null 字节
    "x" * 65,                             # 超长（上限 64）
])
def test_path_id_violation_rejects(bad):
    assert path_id_violation(bad) != ""


@pytest.mark.parametrize("bad_type", [123, None, [], {}])
def test_path_id_violation_rejects_non_string(bad_type):
    assert path_id_violation(bad_type) != ""


@pytest.mark.parametrize("good", [
    "p1",
    "p-1_2",
    "study..2024",                        # 语义修正钉住：整值匹配，合法名含子串 .. 不误拒
    "field_20261016_120000",
    "x" * 64,                             # 恰好上限
])
def test_path_id_violation_accepts(good):
    assert path_id_violation(good) == ""


def test_project_id_violation_uses_repo_root(tmp_path):
    repo = _Repo(tmp_path)
    assert project_id_violation("normal", repo) == ""
    assert project_id_violation("../../evil_rel", repo) != ""


def test_containment_escape_via_symlink(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "lnk"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本环境无法创建符号链接（Windows 需开发者模式/管理员）")
    repo = _Repo(tmp_path)
    # "lnk" 是合法单段名，但 resolve 后指向根外——containment 兜底应拒绝
    assert project_id_violation("lnk", repo) != ""


def test_turn_id_violation():
    assert turn_id_violation("0123456789abcdef") == ""
    assert turn_id_violation("zzz") != ""
    assert turn_id_violation("0123456789ABCDEF") != ""    # 大写拒绝
    assert turn_id_violation("0123456789abcdef\n") != ""   # 尾部换行拒绝（fullmatch）
    assert turn_id_violation(123) != ""

# ---------------------------------------------------------------- owner 归属标识（归属隔离）

def test_owner_violation_accepts_none():
    assert owner_violation(None) == ""


@pytest.mark.parametrize("owner", [
    "anon:abc123",
    "anon:" + "x" * 127,   # 标识上限 127
    "user_01:uid-123",
])
def test_owner_violation_valid(owner):
    assert owner_violation(owner) == ""


@pytest.mark.parametrize("owner", [
    "anon",              # 无冒号
    "anon:",             # 标识为空
    "anon:a b",          # 标识含空白
    "anon:a:b",          # 标识含冒号
    "Anon:abc",          # 命名空间大写
    "1abc:uid",          # 命名空间数字开头
    "a" * 33 + ":uid",   # 命名空间超长（上限 32）
    " anon:abc",         # 前导空白
])
def test_owner_violation_invalid(owner):
    assert owner_violation(owner) != ""
