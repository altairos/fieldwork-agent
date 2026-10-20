"""把真实文献结果绑定到调用时的项目研究范围；只读检索快照可即时保存。"""
import json

from fieldwork_agent.kernel.models import LiteraturePage
from fieldwork_agent.kernel.research_state import RESEARCH_SCOPE_FIELDS, scope_fingerprint
from fieldwork_agent.kernel.sources.flyai import current_call_id


def bind_research_searches(dispatch, repo, pid: str, *, context_provider=None,
                           on_snapshot=None, turn_id: str = ""):
    def bounded(name, args):
        if name != "search_literature":
            return dispatch(name, args)
        ctx = (context_provider() if context_provider else repo.get_research_context(pid))
        fingerprint = scope_fingerprint(ctx)
        env = dispatch(name, args)
        if not isinstance(env.data, LiteraturePage):
            return env
        page = env.data.model_copy(update={
            "scope_fingerprint": fingerprint,
            "scope_basis": {key: str(ctx.get(key) or "") for key in RESEARCH_SCOPE_FIELDS},
        })
        if env.ok:
            label = f"{str(args.get('query', ''))[:80]} | scope={fingerprint}"
            # C04：与其它工具快照同走统一元数据链路——parent_call_id 让实时行
            # 与缓存行跨层配对（证据页单行），turn_id/query_key 支撑时间归属与
            # 同题折叠。current_call_id 由 web loop 的 _exec_tool set（线程内可读），
            # CLI 宿主未 set 时为 ""，行为与旧版一致。
            cid = repo.cache_search(
                pid, label, name, page.model_dump(mode="json"),
                parent_call_id=current_call_id.get(""), turn_id=turn_id,
                query_key=f"{name}|{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}",
            )
            if on_snapshot:
                on_snapshot(cid)
        return env.model_copy(update={"data": page})
    return bounded
