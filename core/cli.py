"""
田野罗盘 Agent — CLI 交互界面

Task 9 适配：宿主改为能力内核 ``FieldAgent``（repo 中心的持久化）。
phase 标识从 ``kernel.guidance.phases`` 导入；``generate`` 走 compose_proposal +
repo.save_proposal；new/list/help/chat/open/status/knowledge/sync 接线保持可用。
"""
import sys

from fieldwork_agent.kernel.guidance.phases import (
    PHASE_FIELD_SITE_PROFILES,
    PHASE_FINISHED,
    PHASE_ITINERARY_PLANNING,
    PHASE_PROPOSAL_GENERATION,
    PHASE_RESEARCH_POSITIONING,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from .config import CONFIG_DIR, Config
from .orchestrator import FieldAgent

# phase 展示名（键一律用 kernel 常量，杜绝与阶段流转漂移）
_PHASE_LABELS = {
    PHASE_RESEARCH_POSITIONING: "📍 研究定位",
    PHASE_FIELD_SITE_PROFILES: "🗺️ 田野点画像",
    PHASE_ITINERARY_PLANNING: "🗓️ 行程规划",
    PHASE_PROPOSAL_GENERATION: "📝 方案生成",
    PHASE_FINISHED: "✅ 已完成",
}
_PHASE_CN = {
    PHASE_RESEARCH_POSITIONING: "研究定位",
    PHASE_FIELD_SITE_PROFILES: "田野点画像",
    PHASE_ITINERARY_PLANNING: "行程规划",
    PHASE_PROPOSAL_GENERATION: "方案生成",
    PHASE_FINISHED: "已完成",
}


def _phase_label(phase: str) -> str:
    return _PHASE_LABELS.get(phase, phase)


def _ensure_utf8_stdio():
    """Windows 终端默认 GBK，CLI 打印 emoji 会触发 UnicodeEncodeError。
    将 stdout/stderr 切换为 UTF-8（无法编码时替换），保证跨终端可运行。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _print_reply(reply_data: dict):
    print()
    print(reply_data.get("reply", ""))
    events = reply_data.get("tool_events") or []
    if events:
        print(f"\n🔍 本轮调用 {len(events)} 个工具: "
              + "、".join(str(e.get("tool", "")) for e in events))
    transition = reply_data.get("phase_transition")
    if transition:
        old_cn = _PHASE_CN.get(transition["from"], transition["from"])
        new_cn = _PHASE_CN.get(transition["to"], transition["to"])
        print(f"\n🔄 阶段转换: {old_cn} → {new_cn}")


def main():
    _ensure_utf8_stdio()
    config = Config()
    check_dependencies(config)

    agent = FieldAgent(config)

    print("=" * 60)
    print("  田野罗盘 — 田野调查旅行规划 Agent")
    print("  基于飞猪 FlyAI 全品类旅行搜索能力")
    print("=" * 60)
    print()

    available_tools = []
    if config.flyai_available:
        available_tools.append("flyai(8 tools)")
    if config.dashscope_available:
        available_tools.append("dashscope(qwen-max)")
    else:
        available_tools.append("[降级模式] 规则回复")
    print(f"🔧 已加载能力: {', '.join(available_tools)}")
    print()

    session = PromptSession(history=FileHistory(str(CONFIG_DIR / "history.txt")))

    while True:
        try:
            if not agent.project_id:
                prompt_text = "fieldwork > "
            else:
                phase_label = _phase_label(agent.current_phase())
                prompt_text = f"fieldwork [{agent.project_id}] ({phase_label}) > "

            try:
                user_input = session.prompt(prompt_text)
            except KeyboardInterrupt:
                print("\n(Ctrl+C 取消)")
                continue
            except EOFError:
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            parts = user_input.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd in ("quit", "exit", "q"):
                print("再见！祝您田野调查顺利。")
                break

            elif cmd == "help":
                print_help()

            elif cmd == "new":
                pid = agent.create_project(args or None)
                print(f"✅ 新项目已创建: {pid}")
                print(f"   阶段: {_phase_label(agent.current_phase())}")
                print(f"   存储路径: {config.projects_dir / pid}")
                print()
                print("现在可以开始对话，描述您的研究需求。我会引导您逐步完善方案。")
                print()
                print("💡 当前可用的飞猪搜索工具:")
                for line in (
                    "   • search_flights    - 航班搜索（跨省/跨国交通）",
                    "   • search_trains     - 火车搜索（高铁备选）",
                    "   • search_hotels     - 酒店搜索（民宿/客栈优先）",
                    "   • search_pois       - 景点/文化场所搜索（30+分类）",
                    "   • search_keyword    - 关键词搜索（签证/保险/租车等）",
                    "   • search_ai         - AI语义搜索（自然语言复杂意图）",
                    "   • search_marriott_hotels   - 万豪旗下酒店（高端备选）",
                    "   • search_marriott_packages - 万豪套餐（体验产品）",
                ):
                    print(line)
                print("   + 聚合/文档能力: profile_field_site / compose_itinerary /")
                print("     compose_proposal / compose_interview_guide / suggest_informants")

            elif cmd == "list":
                projects = agent.list_projects()
                if not projects:
                    print("没有已存在的项目。使用 'new' 创建新项目。")
                else:
                    print(f"{'ID':<40} {'阶段':<15} {'更新时间'}")
                    print("-" * 80)
                    for p in projects:
                        phase_cn = _PHASE_CN.get(p["phase"], p["phase"])
                        print(f"{p['project_id']:<40} {phase_cn:<15} {p['updated_at']}")

            elif cmd == "open":
                project_id = args.strip()
                if not project_id:
                    print("❌ 请指定项目ID: open <project_id>")
                    continue
                try:
                    agent.load_project(project_id)
                    print(f"✅ 已加载项目: {agent.project_id}")
                    print(f"   阶段: {_phase_label(agent.current_phase())}")
                except FileNotFoundError as e:
                    print(f"❌ {e}")

            elif cmd == "chat":
                if not agent.project_id:
                    print("❌ 请先创建一个项目: new")
                    continue
                message = args if args else input("请输入: ")
                _print_reply(agent.chat(message))

            elif cmd == "generate":
                if not agent.project_id:
                    print("❌ 请先创建一个项目: new")
                    continue
                print("正在生成研究方案...")
                result = agent.generate_proposal()
                if result.get("ok"):
                    print(f"✅ 方案已生成 (ID: {result['id']}, 体例: {result['mode']}, "
                          f"{result['sections']} 章)")
                else:
                    print(f"❌ 方案生成失败：{result.get('error', '未知原因')}")

            elif cmd == "knowledge":
                if not agent.project_id:
                    print("❌ 请先创建一个项目: new")
                    continue
                subcmd = args.split()[0].lower() if args.split() else ""
                if subcmd == "add":
                    title = args.split(None, 1)[1] if len(args.split()) > 1 else ""
                    if not title:
                        print("用法: knowledge add <标题>")
                        continue
                    content = input("内容: ")
                    entry_id = agent.repo.add_knowledge(agent.project_id, title, content,
                                                        source="user")
                    print(f"✅ 知识条目已添加 (ID: {entry_id})")
                elif subcmd == "list":
                    entries = agent.repo.list_knowledge(agent.project_id)
                    if not entries:
                        print("知识库为空。使用 'knowledge add' 添加资料。")
                    else:
                        for e in entries:
                            print(f"  [{e['id']}] {e['title']} ({e['category']}) "
                                  f"— {e['created_at']}")
                else:
                    print("用法: knowledge add/list")

            elif cmd == "status":
                if not agent.project_id:
                    print("❌ 没有活跃项目。")
                    continue
                pid = agent.project_id
                state = agent.repo.get_state(pid)
                ctx = state.get("research_context") or {}
                print(f"项目ID: {pid}")
                print(f"阶段: {_phase_label(state['phase'])}")
                print("研究者信息:")
                for k, v in ctx.items():
                    if k != "extras" and str(v).strip():
                        print(f"  • {k}: {v}")
                for k, v in (ctx.get("extras") or {}).items():
                    print(f"  • {k}: {v}")
                print(f"知识库条目: {len(agent.repo.list_knowledge(pid))}")
                print(f"搜索结果缓存: {len(agent.repo.list_cached_searches(pid))}")
                print(f"候选田野点: {len(state.get('field_sites') or [])}")
                print(f"访谈对象: {len(state.get('informants') or [])}")
                proposal = agent.repo.get_latest_proposal(pid)
                print(f"最新方案: {'有' if proposal else '无'}")

            elif cmd == "sync":
                if not agent.project_id:
                    print("❌ 没有活跃项目。")
                    continue
                if args.lower() == "dingtalk":
                    if config.dingtalk_available:
                        success = agent.sync_to_dingtalk()
                        print(f"{'✅' if success else '❌'} 同步结果: "
                              f"{'成功' if success else '失败'}")
                    else:
                        print("❌ 钉钉未配置。请在 config.json 中设置 "
                              "dingtalk_webhook 和 dingtalk_enabled=true")

            else:
                if not agent.project_id:
                    print("❌ 请先创建一个项目: new")
                    continue
                _print_reply(agent.chat(user_input))

        except KeyboardInterrupt:
            print("\n(Ctrl+C)")
            continue
        except Exception as e:
            print(f"\n❌ 错误: {e}")
            import traceback
            traceback.print_exc()


def check_dependencies(config):
    issues = []
    if not config.dashscope_api_key:
        issues.append("DASHSCOPE_API_KEY 未设置 — LLM 对话将降级为规则回复")
    if not config.flyai_api_key:
        issues.append("FLYAI_API_KEY 未设置 — 旅行搜索功能不可用")
    if issues:
        print("⚠️  以下配置缺失:")
        for issue in issues:
            print(f"  ⚠ {issue}")
        print()


def print_help():
    print("""\
=== 田野罗盘 Agent 帮助 ===

【项目管理】
  new [name]        创建新项目
  list              列出所有项目
  open <id>         打开已有项目

【对话交互】
  chat <message>    发送消息（带参数时立即回复）
                     （不带参数时进入逐行输入模式）
  直接输入文本      等同于 chat <文本>

【方案生成】
  generate          基于当前所有信息生成研究方案草稿
                     输出为 Markdown 文件，投稿/开题前请人工核校补齐

【知识库】
  knowledge add     添加自有研究资料到本地知识库
  knowledge list    查看所有已存储的知识条目

【状态与同步】
  status            查看当前项目的完整状态
  sync dingtalk     将最新方案同步到钉钉群

【工具说明】
  本 Agent 深度集成飞猪 FlyAI 全部 8 个搜索工具：
  • search_flights    航班搜索 — 大交通规划
  • search_trains     火车搜索 — 高铁备选方案
  • search_hotels     酒店搜索 — 住宿筛选（支持民宿/客栈）
  • search_pois       景点搜索 — 30+分类文化场所检索
  • search_keyword    关键词搜索 — 签证/保险/租车等广义覆盖
  • search_ai         AI语义搜索 — 自然语言理解复杂意图（核心杀手锏）
  • search_marriott_hotels   万豪酒店 — 高端备选
  • search_marriott_packages 万豪套餐 — 体验产品推荐
  以及内核聚合/文档能力：profile_field_site / compose_itinerary /
  compose_proposal / compose_interview_guide / suggest_informants

【其他】
  help              显示此帮助
  quit / exit       退出程序""")


if __name__ == "__main__":
    main()
