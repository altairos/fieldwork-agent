"""TurnPatchBuffer：F05 回合级取消保护。"""
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.runtime.state_tools import build_project_state_tools
from fieldwork_agent.runtime.turn_buffer import TurnPatchBuffer


def _repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def test_stage_then_commit_writes_repo(tmp_path):
    repo = _repo(tmp_path)
    pid = "p"
    repo.create(pid)
    buf = TurnPatchBuffer(repo, pid)
    buf.stage({"topic": "宗族与乡村治理"})
    assert repo.get_research_context(pid).get("topic", "") == ""  # 未提交不落盘
    assert buf.staged_view()["topic"] == "宗族与乡村治理"       # 暂存视图可见
    assert buf.commit() == 1
    assert repo.get_research_context(pid)["topic"] == "宗族与乡村治理"
    assert buf.commit() == 0                                    # 已清空幂等


def test_discard_drops_staged(tmp_path):
    repo = _repo(tmp_path)
    pid = "p"
    repo.create(pid)
    buf = TurnPatchBuffer(repo, pid)
    buf.stage({"topic": "被取消的修改"})
    buf.discard()
    assert repo.get_research_context(pid).get("topic", "") == ""


def test_state_tools_buffered_writes_go_to_staged_view(tmp_path):
    """buffer 注入后：handler 校验基于 staged_view（NA 存量豁免链不断），写暂存不落盘。"""
    repo = _repo(tmp_path)
    pid = "p"
    repo.create(pid)
    repo.update_research_context(pid, {"interviews_na_reason": "文献研究"})
    buf = TurnPatchBuffer(repo, pid)
    _, handlers = build_project_state_tools(repo, pid, buffer=buf)
    env = handlers["update_research_context"](
        patch={"interviews_not_applicable": True})   # 理由走 staged_view 存量豁免
    assert env.ok, env.error
    ctx = repo.get_research_context(pid)
    assert ctx.get("interviews_not_applicable") is not True     # 未落盘
    assert buf.staged_view()["interviews_not_applicable"] is True
