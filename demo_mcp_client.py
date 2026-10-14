"""演示客户端：以标准 MCP 客户端经 stdio 接入本项目 Server（手动运行，不进 CI）。

运行方式（仓库根）::

    .venv\\Scripts\\python.exe demo_mcp_client.py

预期输出（全部打印到 stdout）：

1. ``list_tools`` 返回 **22 个工具**（13 kernel 能力 + 9 状态工具），不足即断言失败；
2. ``create_project → update_research_context → check_phase_advance`` 全链路统一包络
   打印（项目落在 ``~/.fieldwork_agent/projects/demo_<时间戳>``）；
3. ``search_hotels`` 统一包络展示：本机装有 flyai CLI 且配置了 key 时为真实搜索
   结果（ok:true，打印条目摘要）；无 CLI/key 时为体验降级（ok:false，打印
   code/message/hint，如 flyai_unavailable）——两者都算通过；
4. 资源与 prompt 面计数（静态资源 ≥17 条、prompt 恰 2 个）。

本脚本即"标准 MCP 宿主如何接入 fieldwork-mcp"的最小可运行示例：console script
``fieldwork-mcp`` 与 ``python -m fieldwork_agent.mcp_server`` 完全同源。
"""
import asyncio
import json
import sys
from datetime import datetime

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = 22


def _payload(result) -> dict:
    """call_tool 结果 → 统一包络 dict（单条 JSON 文本约定，见 server._text_payload）。"""
    return json.loads(result.content[0].text)


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fieldwork_agent.mcp_server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # [1] 工具面：22 个（13 能力 + 9 状态）
            tools = (await session.list_tools()).tools
            print(f"[1] list_tools: {len(tools)} 个工具")
            for t in tools:
                print(f"    - {t.name}")
            assert len(tools) == EXPECTED_TOOLS, f"工具数 {len(tools)} != {EXPECTED_TOOLS}"

            # [2] 状态工具全链路：create → update_context → check_advance
            pid = f"demo_{datetime.now():%Y%m%d_%H%M%S}"
            created = _payload(await session.call_tool("create_project", {"project_id": pid}))
            print(f"\n[2] create_project({pid}) → ok={created['ok']} data={created.get('data')}")

            updated = _payload(await session.call_tool("update_research_context", {
                "project_id": pid,
                "patch": {"discipline": "人类学", "topic": "城市移民与民族社区研究",
                          "scale": "单城市多点", "budget": "1 万元以内"}}))
            ctx = updated.get("data") or {}
            print(f"[3] update_research_context → ok={updated['ok']} "
                  f"topic={ctx.get('topic')!r} discipline={ctx.get('discipline')!r}")

            advance = _payload(await session.call_tool("check_phase_advance",
                                                       {"project_id": pid}))
            data = advance.get("data") or {}
            print(f"[4] check_phase_advance → ok={advance['ok']} "
                  f"can_advance={data.get('can_advance')} "
                  f"missing_fields={data.get('missing_fields')}")

            # [3] 搜索能力：真实结果或体验降级，都走统一包络
            hotels = _payload(await session.call_tool("search_hotels", {"dest_name": "大理"}))
            if hotels.get("ok"):
                page = hotels.get("data") or {}
                print(f"\n[5] search_hotels(大理) → ok:true total={page.get('total')} "
                      f"cheapest={page.get('cheapest')} dearest={page.get('dearest')}")
                for item in (page.get("items") or [])[:3]:
                    print(f"    - {item.get('name')} {item.get('price_raw') or ''}")
            else:
                err = hotels.get("error") or {}
                print(f"\n[5] search_hotels(大理) → ok:false（体验降级） "
                      f"code={err.get('code')}")
                print(f"    message={err.get('message')}")
                print(f"    hint={err.get('hint')}")

            # [4] 资源与 prompt 面
            resources = (await session.list_resources()).resources
            prompts = (await session.list_prompts()).prompts
            print(f"\n[6] list_resources: {len(resources)} 条；"
                  f"list_prompts: {len(prompts)} 个（{[p.name for p in prompts]}）")


if __name__ == "__main__":
    asyncio.run(main())
