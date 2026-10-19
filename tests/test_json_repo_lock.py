import threading
from fieldwork_agent.kernel.repository.json_repo import JsonRepository


def test_concurrent_claim_and_context_update_preserves_owner(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1", owner="anon:tok")
    barrier = threading.Barrier(2, timeout=5)
    errors = []

    def claim():
        try:
            barrier.wait()
            repo.set_owner("p1", "user:u1")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def update_ctx():
        try:
            barrier.wait()
            for i in range(50):
                repo.update_research_context("p1", {"topic": f"t{i}"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=claim), threading.Thread(target=update_ctx)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert not errors
    # 认领成功后，任何并发 update_research_context 都不得把 owner 写回 anon:
    assert repo.get_state("p1")["owner"] == "user:u1"
