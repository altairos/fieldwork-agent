"""``ProjectRepository`` 协议 — 项目持久化的稳定边界（spec §5.1 里程碑 1）。

约定（JsonRepository 与后续 SqliteRepository 必须逐条一致，由
``tests/repository_contract.py`` 钉死）：

- **dict 进出**：Protocol 不绑定 pydantic 模型，宿主自行与 ``kernel.models`` 转换，
  避免持久层与数据契约互相牵制；
- **未知 ``project_id`` → ``KeyError``**；**非法阶段 → ``ValueError``**；
- **消息单轨制**：全量消息史是唯一真源，``get_messages`` 只读全量史；
  状态里的窗口只是缓存副本，不参与读取（旧 ``get_recent_messages`` 双轨合并已废弃）；
- **缓存 64KB 上限**：``cache_search`` 落盘超限即替换为
  ``{"truncated": True, "preview": ...}``，宿主据 ``truncated`` 判断需重新检索；
- **幂等创建**：``create`` 对已存在项目不得清空存量（宿主可反复调用做"打开"）；
- 记录类方法（田野点/报道人）**同 id 视为更新**：宿主可先存草稿后置 ``confirmed``。
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProjectRepository(Protocol):
    """项目状态与产物读写协议（27 个方法，全部以 dict 进出）。"""

    # ---------------------------------------------------------------- 项目与阶段

    def create(self, project_id: str) -> None:
        """建项目（幂等：已存在则保留存量，顺带完成存量消息史规范化）。"""
        ...

    def list_projects(self) -> list[dict]:
        """项目摘要列表：``{"project_id", "phase", "created_at", "updated_at"}``。"""
        ...

    def get_state(self, pid: str) -> dict:
        """整份项目状态（返回副本，调用方改动不回写磁盘）。"""
        ...

    def get_phase(self, pid: str) -> str:
        """当前阶段名。"""
        ...

    def set_phase(self, pid: str, phase: str) -> None:
        """切阶段：未知阶段 ``ValueError``；同阶段不写历史；转移记入 ``phase_history``。"""
        ...

    # ---------------------------------------------------------------- 研究上下文

    def update_research_context(self, pid: str, patch: dict) -> None:
        """按 patch 浅合并研究上下文（只覆盖 patch 里出现的键）。"""
        ...

    def get_research_context(self, pid: str) -> dict:
        """研究上下文字典（缺位键不存在，而非空字符串占位）。"""
        ...

    # ---------------------------------------------------------------- 消息史

    def append_message(self, pid: str, role: str, content: str, meta: dict | None = None) -> None:
        """追加一条消息到全量史，并同步滚动窗口（窗口上限 50 条）。"""
        ...

    def get_messages(self, pid: str, limit: int = 30) -> list[dict]:
        """按时间顺序取最近 ``limit`` 条（``limit <= 0`` 取全部）；只读全量史。"""
        ...

    # ---------------------------------------------------------------- 田野点与报道人

    def save_field_site(self, pid: str, record: dict) -> str:
        """保存田野点记录，返回记录 id（``record`` 带 ``id`` 则更新，否则新建）。"""
        ...

    def list_field_sites(self, pid: str) -> list[dict]:
        """田野点记录列表（按保存顺序）。"""
        ...

    def save_informant(self, pid: str, record: dict) -> str:
        """保存报道人记录，返回记录 id（同 ``save_field_site`` 的 upsert 语义）。"""
        ...

    def list_informants(self, pid: str) -> list[dict]:
        """报道人记录列表（按保存顺序）。"""
        ...

    # ---------------------------------------------------------------- 搜索结果缓存

    def cache_search(self, pid: str, query: str, tool: str, result: dict) -> str:
        """缓存一次检索结果，返回缓存 id；序列化超 64KB 时 ``result`` 替换为截断标记。"""
        ...

    def list_cached_searches(self, pid: str) -> list[dict]:
        """缓存索引（不含 ``result``）：``{"id", "query", "tool", "cached_at"}``。"""
        ...

    def get_cached_search(self, pid: str, cid: str) -> dict | None:
        """按项目域取完整缓存（含 ``result``）；不存在或跨项目返回 ``None``。

        审计二 R01：cid 过白名单（guard.CACHE_ID_RE）后只在 ``pid`` 的
        search_cache 内定位，绝不做跨项目 glob。
        """
        ...

    # ---------------------------------------------------------------- 知识库

    def add_knowledge(
        self,
        pid: str,
        title: str,
        content: str,
        source: str = "user",
        tags: list[str] | None = None,
        category: str = "general",
    ) -> str:
        """新增知识条目，返回条目 id。"""
        ...

    def list_knowledge(self, pid: str) -> list[dict]:
        """知识条目列表（按创建顺序，含正文）。"""
        ...

    def search_knowledge(self, pid: str, text: str) -> list[dict]:
        """关键词过滤 title + content + tags（大小写不敏感，空白分词 AND）。

        FTS5 版本属里程碑 2，接口不变。
        """
        ...

    def delete_knowledge(self, pid: str, entry_id: str) -> None:
        """删除知识条目（不存在则不报错，保持幂等）。"""
        ...

    # ---------------------------------------------------------------- 提案文档

    def save_proposal(
        self, pid: str, markdown: str, kind: str = "proposal", version: str = "draft"
    ) -> str:
        """落一份文档并登记，返回记录 id；重复保存不覆盖既有文件（版本累积）。"""
        ...

    def get_latest_proposal(self, pid: str) -> dict | None:
        """最近保存的文档（含 ``content`` 正文）；无文档返回 ``None``。"""
        ...

    def list_proposals(self, pid: str) -> list[dict]:
        """全部文档记录（含 ``content``），按保存时间升序；含旧版单值键兼容读出。"""
        ...

    # ---------------------------------------------------------------- 确认与失效（第二批 F04）

    def confirm_field_site(self, pid: str, site_id: str) -> bool:
        """按记录 id 把田野点置为已确认；未知 id 返回 ``False``，已确认幂等返回 ``True``。"""
        ...

    def confirm_proposal(self, pid: str, subject: str = "") -> dict:
        """落一条 ``ConfirmationRecord``（关键输入字段当前值哈希）并置
        ``proposal_confirmed=True``；返回 ``model_dump(mode="json")`` 的记录副本。"""
        ...

    def get_proposal_by_id(self, pid: str, doc_id: str) -> dict | None:
        """按记录 id 取文档（含 ``content`` 正文，天然兼容 ``legacy_`` 前缀）；
        不存在返回 ``None``。"""
        ...

    def is_proposal_confirmation_stale(self, pid: str) -> bool:
        """最近一次方案确认的输入哈希与当前关键输入不一致，或确认后产生了新文档
        （``subject`` 不再是最新提案 id）→ ``True``；无确认记录返回 ``False``。"""
        ...
