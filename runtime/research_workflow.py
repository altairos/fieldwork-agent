"""把真实文献结果绑定到调用时的项目研究范围；只读检索快照可即时保存。"""
from fieldwork_agent.kernel.models import LiteraturePage
from fieldwork_agent.kernel.research_state import RESEARCH_SCOPE_FIELDS, scope_fingerprint


def bind_research_searches(dispatch, repo, pid: str, *, context_provider=None, on_snapshot=None):
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
            cid = repo.cache_search(pid, label, name, page.model_dump(mode="json"))
            if on_snapshot:
                on_snapshot(cid)
        return env.model_copy(update={"data": page})
    return bounded
