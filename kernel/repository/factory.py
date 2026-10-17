"""存储后端工厂：按 FA_STORAGE 选 Json/Sqlite 实现（spec §5.1 里程碑2 双实现并行）。"""
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def make_repository(config):
    """config.storage_backend=='sqlite' → SqliteRepository；否则 JsonRepository（默认/未知值）。"""
    if getattr(config, "storage_backend", "json") == "sqlite":
        return SqliteRepository(db_path=config.sqlite_path)
    return JsonRepository(projects_dir=config.projects_dir)
