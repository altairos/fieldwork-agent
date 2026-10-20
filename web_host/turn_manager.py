"""R4 回合生命周期核心：状态机 / 幂等 / 写锁 / 事件持久化 / 回放 / 订阅。"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fieldwork_agent.web_host.events import EventEnvelope
from pydantic import ValidationError

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})

# A02：终态回合在内存里再留这么久，之后随新回合创建顺手回收。
# 不能更短：SSE 断线重连与幂等重试还要靠 handle 判断终态；
# 事件本体在 events.jsonl 里不删，历史回看走 /messages 而非这个表。
TERMINAL_RETENTION = 30 * 60.0


def _atomic_write_json(path: Path, payload: dict) -> None:
    """同目录临时文件写入后 os.replace 原子替换，避免崩溃留下半写 JSON。

    replace 前 flush+fsync：rename 只保证名字原子、不保证数据落盘，
    掉电后目标文件可能是零字节空壳（审查 1.3 加固）。
    """
    text = json.dumps(payload, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@dataclass
class TurnHandle:
    """单个回合的运行时句柄。"""

    turn_id: str
    pid: str
    status: str = "running"
    idempotency_key: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None      # A02：进入终态的时间，供回收判定
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    seq_counter: int = 0


class TurnManager:
    """管理回合创建、事件追加、持久化、回放、订阅、写锁与恢复。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        # 内存注册表，键 "{pid}:{tid}"
        self._turns: dict[str, TurnHandle] = {}
        # 幂等索引，键 "{pid}:{key}" → tid
        self._idempotency: dict[str, str] = {}
        # 写锁，键 pid
        self._locks: dict[str, asyncio.Lock] = {}
        # 订阅，键 "{pid}:{tid}" → list[Queue]
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    # ------------------------------------------------------------------
    # 回合生命周期
    # ------------------------------------------------------------------

    def lookup_idempotent(self, pid: str,
                          idempotency_key: str | None) -> TurnHandle | None:
        """N11：按幂等键查已有回合（纯查询、无副作用）；无键或未命中返回 None。"""
        if idempotency_key is None:
            return None
        tid = self._idempotency.get(f"{pid}:{idempotency_key}")
        return self._turns.get(f"{pid}:{tid}") if tid is not None else None

    def try_create_turn(self, pid: str,
                        idempotency_key: str | None = None) -> tuple[TurnHandle, bool]:
        """创建回合，返回 ``(handle, created)``。

        N11：``created=False`` 表示幂等命中已有回合——调用方据此只回已有
        turn_id/status、绝不再排 run_turn（消除同键重复执行模型与重复落用户消息）。
        """
        existing = self.lookup_idempotent(pid, idempotency_key)
        if existing is not None:
            return existing, False

        # A02：新回合创建时顺手回收过期终态（摊销成本，不需要后台任务）
        self.prune_terminal()

        tid = uuid.uuid4().hex[:16]
        handle = TurnHandle(turn_id=tid, pid=pid, idempotency_key=idempotency_key)
        registry_key = f"{pid}:{tid}"
        self._turns[registry_key] = handle

        if idempotency_key is not None:
            self._idempotency[f"{pid}:{idempotency_key}"] = tid

        # 持久化 meta.json
        turn_dir = self._root / pid / "turns" / tid
        turn_dir.mkdir(parents=True, exist_ok=True)
        self._write_meta(handle)
        return handle, True

    def create_turn(self, pid: str, idempotency_key: str | None = None) -> TurnHandle:
        """创建回合；幂等键命中时返回已有 handle（兼容旧签名，仅需 handle）。"""
        return self.try_create_turn(pid, idempotency_key)[0]

    def get_turn(self, pid: str, tid: str) -> TurnHandle | None:
        """按 pid+tid 查找内存中的 handle。"""
        return self._turns.get(f"{pid}:{tid}")

    def append_event(
        self, pid: str, tid: str, event: str, data: dict
    ) -> EventEnvelope:
        """追加事件：seq 递增 → 持久化 → 广播订阅者。"""
        handle = self._turns[f"{pid}:{tid}"]
        handle.seq_counter += 1
        envelope = EventEnvelope(
            turn_id=tid, seq=handle.seq_counter, event=event, data=data
        )
        # 追加到 events.jsonl
        events_path = self._root / pid / "turns" / tid / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as f:
            f.write(envelope.model_dump_json() + "\n")
        # 广播
        self._broadcast(pid, tid, envelope)
        return envelope

    def replay(self, pid: str, tid: str, after_seq: int) -> list[EventEnvelope]:
        """从磁盘读取 events.jsonl 并返回 seq > after_seq 的事件列表。"""
        events_path = self._root / pid / "turns" / tid / "events.jsonl"
        if not events_path.exists():
            return []
        results: list[EventEnvelope] = []
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    envelope = EventEnvelope.model_validate_json(line)
                except ValidationError:
                    continue  # 崩溃半写坏行：跳过，保住其余事件
                if envelope.seq > after_seq:
                    results.append(envelope)
        return results

    def finish(self, pid: str, tid: str, status: str) -> None:
        """结束回合：更新状态 → 持久化 meta → 广播 None 哨兵。"""
        handle = self._turns.get(f"{pid}:{tid}")
        if handle is None:
            return
        handle.status = status
        handle.finished_at = time.time()
        self._write_meta(handle)
        self._broadcast(pid, tid, None)

    def prune_terminal(self, *, now: float | None = None,
                       ttl: float = TERMINAL_RETENTION) -> int:
        """回收超期的终态回合（A02），返回回收数量。

        这三个字典原先只在 `purge_project`（删项目）时清理，回合正常结束不移除
        任何条目——长期运行按回合数线性增长。磁盘上的 `events.jsonl` 不动，
        所以 SSE 断线重连超过 TTL 会得到 404，但历史仍可由 `/messages` 读出。
        """
        current = time.time() if now is None else now
        dead = [(key.split(":", 1)[0], handle.turn_id)
                for key, handle in self._turns.items()
                if handle.status in TERMINAL_STATUSES
                and handle.finished_at is not None
                and current - handle.finished_at > ttl]
        if not dead:
            return 0
        stale = set(dead)
        for pid, tid in dead:
            self._turns.pop(f"{pid}:{tid}", None)
            self._subscribers.pop(f"{pid}:{tid}", None)
        self._idempotency = {
            key: tid for key, tid in self._idempotency.items()
            if (key.split(":", 1)[0], tid) not in stale}
        return len(dead)

    def cancel(self, pid: str, tid: str) -> bool:
        """取消回合；已终态则返回 False。"""
        handle = self._turns.get(f"{pid}:{tid}")
        if handle is None or handle.status in TERMINAL_STATUSES:
            return False
        handle.cancel_event.set()
        self.finish(pid, tid, "cancelled")
        return True

    def cancel_all(self, pid: str) -> int:
        """取消该项目全部 running 回合（删除项目前调用，让后台 loop 尽快退出）；
        返回取消数。"""
        cancelled = 0
        for key, handle in list(self._turns.items()):
            if key.startswith(f"{pid}:") and handle.status not in TERMINAL_STATUSES:
                self.cancel(pid, handle.turn_id)
                cancelled += 1
        return cancelled

    def purge_project(self, pid: str) -> None:
        """删除项目的兑底清理：内存注册表（回合/幂等/锁/订阅）与磁盘 turns 目录。

        ``rmtree(ignore_errors=True)``：json 后端下 tm 与 repo 同根，目录可能已被
        ``repo.delete_project`` 先行移除，幂等即可。
        """
        prefix = f"{pid}:"
        self._turns = {k: v for k, v in self._turns.items() if not k.startswith(prefix)}
        self._idempotency = {
            k: v for k, v in self._idempotency.items() if not k.startswith(prefix)}
        self._locks.pop(pid, None)
        self._subscribers = {
            k: v for k, v in self._subscribers.items() if not k.startswith(prefix)}
        shutil.rmtree(self._root / pid, ignore_errors=True)

    # ------------------------------------------------------------------
    # 写锁
    # ------------------------------------------------------------------

    def is_writing(self, pid: str) -> bool:
        """当前 pid 是否有正在执行的写操作。"""
        lock = self._locks.get(pid)
        return lock.locked() if lock is not None else False

    def acquire_write_lock(self, pid: str) -> asyncio.Lock:
        """懒创建并返回 pid 对应的 asyncio.Lock（可直接 async with 使用）。"""
        if pid not in self._locks:
            self._locks[pid] = asyncio.Lock()
        return self._locks[pid]

    # ------------------------------------------------------------------
    # 订阅 / 广播
    # ------------------------------------------------------------------

    def subscribe(self, pid: str, tid: str) -> asyncio.Queue:
        """订阅某回合的实时事件流，返回 Queue。"""
        key = f"{pid}:{tid}"
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(key, []).append(q)
        return q

    def unsubscribe(self, pid: str, tid: str, queue: asyncio.Queue) -> None:
        """取消订阅。空壳 key 一并移除——否则每回合泄一个空列表。"""
        key = f"{pid}:{tid}"
        subs = self._subscribers.get(key, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(key, None)

    def _broadcast(self, pid: str, tid: str, item) -> None:
        """向所有订阅者广播事件或 None 哨兵。"""
        key = f"{pid}:{tid}"
        for q in self._subscribers.get(key, []):
            q.put_nowait(item)

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------

    def recover_interrupted(self, pid_list: list[str]) -> None:
        """扫描各项目 turns 目录，将 running 状态的回合标记为 interrupted。"""
        for pid in pid_list:
            turns_dir = self._root / pid / "turns"
            if not turns_dir.exists():
                continue
            for tid_dir in turns_dir.iterdir():
                if not tid_dir.is_dir():
                    continue
                meta_path = tid_dir / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue  # 坏 meta（半写/不可读）：跳过该回合目录
                if not isinstance(meta, dict):
                    continue  # 合法 JSON 但非 dict（旧格式残留/外部写入）：同样安全跳过
                if meta.get("status") != "running":
                    continue
                # 更新磁盘（原子写，防恢复过程自身半写）
                meta["status"] = "interrupted"
                _atomic_write_json(meta_path, meta)
                # 更新内存 handle（如果存在）
                tid = tid_dir.name
                handle = self._turns.get(f"{pid}:{tid}")
                if handle is not None:
                    handle.status = "interrupted"

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _write_meta(self, handle: TurnHandle) -> None:
        """将 handle 元信息写入 meta.json。"""
        turn_dir = self._root / handle.pid / "turns" / handle.turn_id
        turn_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "turn_id": handle.turn_id,
            "pid": handle.pid,
            "status": handle.status,
            "idempotency_key": handle.idempotency_key,
            "created_at": handle.created_at,
        }
        _atomic_write_json(turn_dir / "meta.json", meta)
