"""Q06：mutation-check 脚本必须可安全导入，且中断/异常后不留下被改源码。"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "web" / "scripts" / "mutation-check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mutation_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)      # 有 __main__ 保护：导入不得执行变异
    return mod


def _mutation(target: Path, old="const a = 1;", new="const a = 2;"):
    return ("m", str(target), old, new, "dummy.spec.ts")


def test_import_module_does_not_run_mutations(tmp_path, monkeypatch):
    mod = _load_module()
    target = tmp_path / "a.ts"
    target.write_text("const a = 1;\n", encoding="utf-8")
    mod.MUTATIONS = [_mutation(target)]

    def must_not_run(_test_file):
        raise AssertionError("导入模块不应执行变异")

    monkeypatch.setattr(mod, "run", must_not_run)
    assert target.read_text(encoding="utf-8") == "const a = 1;\n"


def test_main_restores_source_when_run_is_interrupted(tmp_path, monkeypatch):
    mod = _load_module()
    target = tmp_path / "a.ts"
    original = "const a = 1;\n"
    target.write_text(original, encoding="utf-8")
    mod.MUTATIONS = [_mutation(target)]

    def interrupted(_test_file):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        mod.main()
    assert target.read_text(encoding="utf-8") == original


def test_main_heals_stale_bak_from_old_runs(tmp_path, monkeypatch):
    """旧版脚本中断会留下 path.bak；新脚本先自愈再跑。"""
    mod = _load_module()
    target = tmp_path / "a.ts"
    target.write_text("mutated", encoding="utf-8")
    (tmp_path / "a.ts.bak").write_text("const a = 1;\n", encoding="utf-8")
    mod.MUTATIONS = [_mutation(target, old="anchor-not-present")]

    def must_not_run(_test_file):
        raise AssertionError("锚点缺失时应跳过，不跑测试")

    monkeypatch.setattr(mod, "run", must_not_run)
    mod.main()
    assert target.read_text(encoding="utf-8") == "const a = 1;\n"
    assert not (tmp_path / "a.ts.bak").exists()
