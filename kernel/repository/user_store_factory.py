# kernel/repository/user_store_factory.py
"""账号存储工厂：按 FA_STORAGE 选 Json/Sqlite（与 make_repository 同风格）。"""
from fieldwork_agent.kernel.repository.json_user_store import JsonUserStore
from fieldwork_agent.kernel.repository.sqlite_user_store import SqliteUserStore


def make_user_store(config):
    # 下界校验只在工厂（唯一咽喉点，启动即炸）：T5/6/7 测试直构 store 用 1000 迭代提速，
    # 故不放 hash_password；下界 100_000 挡 0/1/1000 类误配，默认 600_000 不受影响。
    iters = getattr(config, "pbkdf2_iterations", 600_000)
    if iters < 100_000:
        raise ValueError(
            f"pbkdf2_iterations={iters} below safety floor 100_000 "
            "(FA_PBKDF2_ITERATIONS misconfigured?)")
    kw = dict(pbkdf2_iterations=iters,
              session_ttl_days=getattr(config, "session_ttl_days", 30))
    if getattr(config, "storage_backend", "json") == "sqlite":
        return SqliteUserStore(config.sqlite_path, **kw)
    # R1：JsonUserStore 内部断言 user_store_dir 不在 projects_dir 子树内
    return JsonUserStore(config.user_store_dir, projects_dir=config.projects_dir, **kw)
