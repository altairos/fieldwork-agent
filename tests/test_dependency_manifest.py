"""A07：``requirements.txt`` 与 ``pyproject.toml`` 运行时依赖必须一致。

两边都声明运行时依赖是既有事实（文档/容器用 requirements 安装，开发用
pyproject 安装）。少写一个，按 requirements 部署的实例会在启动时
``ModuleNotFoundError``，而 CI 的 editable 安装永远发现不了。审计 A07 当时
就缺了 fastapi / uvicorn / python-docx 三项。

比对口径：名字 + extras + 版本约束（extras 影响安装内容，不能忽略）。
"""

import pathlib

from packaging.requirements import Requirement

try:                       # 项目声明 requires-python >=3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 走此分支
    import tomli as tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _canon(spec: str) -> tuple[str, tuple[str, ...], str]:
    req = Requirement(spec)
    return (req.name.lower(), tuple(sorted(req.extras)), str(req.specifier))


def _pyproject_runtime() -> set:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {_canon(dep) for dep in data["project"]["dependencies"]}


def _requirements_runtime() -> set:
    out = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        out.add(_canon(line))
    return out


def test_requirements_match_pyproject_runtime_deps():
    pyproject = _pyproject_runtime()
    requirements = _requirements_runtime()
    assert requirements == pyproject, (
        "requirements.txt 与 pyproject.toml 运行时依赖不一致：\n"
        f"  仅在 requirements: {sorted(requirements - pyproject)}\n"
        f"  仅在 pyproject:    {sorted(pyproject - requirements)}"
    )
