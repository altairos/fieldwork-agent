"""R4 回合生命周期核心：状态机 / 幂等 / 写锁 / 事件持久化 / 回放 / 订阅。"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fieldwork_agent.web_host.events import EventEnvelope

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


@dataclass
class TurnHandle:
    """单个回合的运行时句柄。"""

    turn_id: str
    pid: str
    status: str = "running"
    idempotency_key: str | None = None
    created_at: float = field(default_factory=time.time)
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

    def create_turn(self, pid: str, idempotency_key: str | None = None) -> TurnHandle:
        """创建回合；幂等键命中时返回已有 handle。"""
        if idempotency_key is not None:
            idem_key = f"{pid}:{idempotency_key}"
            existing_tid = self._idempotency.get(idem_key)
            if existing_tid is not None:
                return self._turns[f"{pid}:{existing_tid}"]

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
        return handle

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
                envelope = EventEnvelope.model_validate_json(line)
                if envelope.seq > after_seq:
                    results.append(envelope)
        return results

    def finish(self, pid: str, tid: str, status: str) -> None:
        """结束回合：更新状态 → 持久化 meta → 广播 None 哨兵。"""
        handle = self._turns.get(f"{pid}:{tid}")
        if handle is None:
            return
        handle.status = status
        self._write_meta(handle)
        self._broadcast(pid, tid, None)

    def cancel(self, pid: str, tid: str) -> bool:
        """取消回合；已终态则返回 False。"""
        handle = self._turns.get(f"{pid}:{tid}")
        if handle is None or handle.status in TERMINAL_STATUSES:
            return False
        handle.cancel_event.set()
        self.finish(pid, tid, "cancelled")
        return True

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
        """取消订阅。"""
        key = f"{pid}:{tid}"
        subs = self._subscribers.get(key, [])
        if queue in subs:
            subs.remove(queue)

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
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("status") != "running":
                    continue
                # 更新磁盘
                meta["status"] = "interrupted"
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False), encoding="utf-8"
                )
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
        meta_path = turn_dir / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
