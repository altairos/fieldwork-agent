"""kernel/assets.py 加载器契约：公开版默认、整体替换、回落、fail-fast、缓存与深拷贝。"""
import json

import pytest

from fieldwork_agent.kernel import assets

PHASE_KEYS = [
    "research_positioning", "field_site_profiles", "itinerary_planning",
    "proposal_generation", "finished",
]
GENERATION_KEYS = ["suggest_system", "guide_system", "guide_instruction"]

FAKE_LIBRARY = {
    "测试甲": {"type": "测试甲", "approach": "甲式接触", "cautions": "甲注意",
             "question_domains": ["域一"], "ethics": "甲伦理",
             "access_channel": "甲渠道", "preparation": "甲准备",
             "examples": ["问一", "问二", "追问：三"]},
    "测试乙": {"type": "测试乙", "approach": "乙式接触", "cautions": "乙注意",
             "question_domains": ["域二"], "ethics": "乙伦理",
             "access_channel": "乙渠道", "preparation": "乙准备",
             "examples": ["问一", "问二", "追问：三"]},
}


@pytest.fixture(autouse=True)
def empty_dir(tmp_path):
    """每个测试固定运行在空覆盖目录（公开版）下，收尾同样复位。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    assets.reload(empty)
    yield empty
    assets.reload(empty)


def _write_pkg(root, *, library=None, types=None, methodology=None,
               prompts=None, generation=None):
    """写入部分覆盖包：None 表示不写该文件（该资产回落公开版）。"""
    root.mkdir(parents=True, exist_ok=True)
    if library is not None:
        (root / "informant_library.json").write_text(
            json.dumps(library, ensure_ascii=False), encoding="utf-8")
    if types is not None:
        (root / "example_types.json").write_text(
            json.dumps(types, ensure_ascii=False), encoding="utf-8")
    if methodology is not None:
        (root / "interview_methodology.txt").write_text(
            methodology, encoding="utf-8")
    if prompts is not None:
        (root / "system_prompts.json").write_text(
            json.dumps(prompts, ensure_ascii=False), encoding="utf-8")
    if generation is not None:
        (root / "generation_prompts.json").write_text(
            json.dumps(generation, ensure_ascii=False), encoding="utf-8")
    return root


def test_public_defaults(empty_dir):
    lib = assets.informant_library()
    assert set(lib) == {"村干部", "乡村教师", "返乡务工者"}
    for entry in lib.values():
        assert set(entry) == {"type", "approach", "cautions", "question_domains",
                              "ethics", "access_channel", "preparation", "examples"}
    assert assets.example_types() == ["村干部", "乡村教师", "返乡务工者"]
    assert assets.interview_methodology().startswith("# 访谈方法要点")
    prompts = assets.system_prompts()
    assert set(prompts) == set(PHASE_KEYS)
    for key in GENERATION_KEYS:
        assert assets.generation_prompt(key)


def test_full_package_replace(tmp_path):
    pkg = _write_pkg(
        tmp_path / "pkg", library=FAKE_LIBRARY, types=["测试甲"],
        methodology="# 假方法论",
        prompts={k: f"假-{k}" for k in PHASE_KEYS},
        generation={k: f"假生成-{k}" for k in GENERATION_KEYS})
    assets.reload(pkg)
    assert set(assets.informant_library()) == {"测试甲", "测试乙"}
    assert assets.example_types() == ["测试甲"]
    assert assets.interview_methodology() == "# 假方法论"
    assert assets.system_prompts()["itinerary_planning"] == "假-itinerary_planning"
    assert assets.generation_prompt("guide_system") == "假生成-guide_system"


def test_partial_fallback_to_public(tmp_path):
    pkg = _write_pkg(tmp_path / "pkg", library=FAKE_LIBRARY, types=["测试乙"])
    assets.reload(pkg)
    assert set(assets.informant_library()) == {"测试甲", "测试乙"}
    # 未覆盖的资产回落公开版
    assert assets.interview_methodology().startswith("# 访谈方法要点")
    assert "田野罗盘" in assets.system_prompts()["research_positioning"]


def test_bad_json_raises(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "informant_library.json").write_text("{broken", encoding="utf-8")
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError, match="informant_library.json"):
        assets.informant_library()


def test_example_types_beyond_library_raises(tmp_path):
    pkg = _write_pkg(tmp_path / "pkg", library=FAKE_LIBRARY,
                     types=["测试甲", "幽灵"])
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError, match="幽灵"):
        assets.example_types()


def test_library_replaced_but_default_types_raises(tmp_path):
    # 只换库不换清单：公开版默认清单三键不在新库 -> 越界 fail-fast
    pkg = _write_pkg(tmp_path / "pkg", library=FAKE_LIBRARY)
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError):
        assets.example_types()


def test_env_dir_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDWORK_ASSETS_DIR", str(tmp_path / "nope"))
    assets.reload(None)  # 恢复自动探测，env 目录不存在必须抛
    with pytest.raises(assets.AssetsError, match="FIELDWORK_ASSETS_DIR"):
        assets.informant_library()


def test_reload_explicit_missing_dir_raises(tmp_path):
    assets.reload(tmp_path / "nope")
    with pytest.raises(assets.AssetsError, match="nope"):
        assets.informant_library()


def test_deepcopy_isolation(empty_dir):
    lib = assets.informant_library()
    lib["村干部"]["approach"] = "被污染"
    assert assets.informant_library()["村干部"]["approach"] != "被污染"


def test_reload_switches_package(tmp_path):
    pkg_a = _write_pkg(tmp_path / "a", methodology="# 甲方法")
    pkg_b = _write_pkg(tmp_path / "b", methodology="# 乙方法")
    assets.reload(pkg_a)
    assert assets.interview_methodology() == "# 甲方法"
    assets.reload(pkg_b)
    assert assets.interview_methodology() == "# 乙方法"  # 缓存不串包


def test_system_prompts_missing_phase_raises(tmp_path):
    broken = {k: f"假-{k}" for k in PHASE_KEYS if k != "finished"}
    pkg = _write_pkg(tmp_path / "pkg", prompts=broken)
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError, match="finished"):
        assets.system_prompts()


def test_generation_prompt_unknown_key_raises(empty_dir):
    with pytest.raises(ValueError):
        assets.generation_prompt("nope")
