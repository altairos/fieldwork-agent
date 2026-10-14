"""pytest 共享 fixture。

用 FakeConfig 把项目状态目录指向 tmp_path，避免测试读写真实的
`~/.fieldwork_agent`，保证隔离与可重复。
"""
from pathlib import Path

import pytest
from fieldwork_agent.core.dialogue_engine import DialogueEngine
from fieldwork_agent.core.state import ProjectState


class FakeConfig:
    """最小可用的 Config 替身，仅提供 ProjectState 所需的目录接口。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.projects_dir = self.root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.dashscope_api_key = ""
        self.flyai_api_key = ""

    def get_project_dir(self, project_id: str) -> Path:
        pdir = self.projects_dir / project_id
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir


@pytest.fixture
def fake_config(tmp_path) -> FakeConfig:
    return FakeConfig(tmp_path)


@pytest.fixture
def make_project(fake_config):
    """工厂：创建带指定 project_id 的 ProjectState。"""

    def _make(project_id: str = "test_project") -> ProjectState:
        return ProjectState(fake_config, project_id)

    return _make


@pytest.fixture
def project(make_project) -> ProjectState:
    return make_project()


@pytest.fixture
def make_engine(project):
    """工厂：基于给定 ProjectState 创建 DialogueEngine。"""

    def _make(state: ProjectState | None = None) -> DialogueEngine:
        return DialogueEngine(state or project)

    return _make
