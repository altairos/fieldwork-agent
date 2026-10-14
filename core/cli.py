"""
田野旅行规划 Agent — CLI 交互界面
"""
import sys
from prompt_toolkit import PromptSession
from datetime import datetime

from .config import Config
from .orchestrator import FieldAgent


def main():
    config = Config()
    check_dependencies(config)

    agent = FieldAgent(config)

    print("=" * 60)
    print("  田野旅行规划 Agent (Field Research Travel Planner)")
    print("  基于飞猪 FlyAI 全品类旅行搜索能力")
    print("=" * 60)
    print()

    # 检查并提示可用工具
    available_tools = []
    if config.flyai_available:
        available_tools.append("flyai(8 tools)")
    if config.dashscope_available:
        available_tools.append("dashscope(qwen-max)")
    else:
        available_tools.append("[降级模式] 规则回复")

    print(f"🔧 已加载能力: {', '.join(available_tools)}")
    print()

    session = PromptSession(history_file="~/.fieldwork_agent/history.txt")

    while True:
        try:
            if not agent.project:
                prompt_text = "fieldwork > "
            else:
                phase_names = {
                    "research_positioning": "📍 研究定位",
                    "field_site_profiles": "🗺️ 田野点画像",
                    "itinerary_planning": "🗓️ 行程规划",
                    "proposal_generation": "📝 方案生成",
                    "finished": "✅ 已完成",
                }
                phase_label = phase_names.get(agent.project.phase, agent.project.phase)
                prompt_text = f"fieldwork [{agent.project.project_id}] ({phase_label}) > "

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
                project = agent.create_project(args or None)
                print(f"✅ 新项目已创建: {project.project_id}")
                print(f"   阶段: {project.phase}")
                print(f"   存储路径: {project.project_dir}")
                print()
                print("现在可以开始对话，描述您的研究需求。我会引导您逐步完善方案。")
                print()
                print("💡 当前可用的飞猪搜索工具:")
                print("   • search-flight     - 航班搜索（跨省/跨国交通）")
                print("   • search-train      - 火车搜索（高铁备选）")
                print("   • search-hotel      - 酒店搜索（民宿/客栈优先）")
                print("   • search-poi        - 景点/文化场所搜索（30+分类）")
                print("   • keyword-search    - 关键词搜索（签证/保险/租车等）")
                print("   • ai-search         - AI语义搜索（自然语言复杂意图）")
                print("   • marriott-hotel    - 万豪旗下酒店（高端备选）")
                print("   • marriott-package  - 万豪套餐（体验产品）")

            elif cmd == "list":
                projects = agent.list_projects()
                if not projects:
                    print("没有已存在的项目。使用 'new' 创建新项目。")
                else:
                    print(f"{'ID':<40} {'阶段':<15} {'更新时间'}")
                    print("-" * 80)
                    for p in projects:
                        phase_map = {
                            "research_positioning": "研究定位",
                            "field_site_profiles": "田野点画像",
                            "itinerary_planning": "行程规划",
                            "proposal_generation": "方案生成",
                            "finished": "已完成",
                        }
                        phase_cn = phase_map.get(p["phase"], p["phase"])
                        print(f"{p['id']:<40} {phase_cn:<15} {p['updated_at']}")

            elif cmd == "open":
                project_id = args.strip()
                if not project_id:
                    print("❌ 请指定项目ID: open <project_id>")
                    continue
                try:
                    project = agent.load_project(project_id)
                    print(f"✅ 已加载项目: {project.project_id}")
                    print(f"   阶段: {project.phase}")
                except FileNotFoundError as e:
                    print(f"❌ {e}")

            elif cmd == "chat":
                if not agent.project:
                    print("❌ 请先创建一个项目: new")
                    continue
                message = args if args else input("请输入: ")
                reply_data = agent.chat(message)
                print()
                print(reply_data.get("reply", ""))
                if reply_data.get("guiding_questions"):
                    print()
                    print("💡 引导问题:")
                    for q in reply_data["guiding_questions"]:
                        print(f"  • {q}")
                if reply_data.get("tool_results"):
                    print()
                    print(f"🔍 已调用 {reply_data.get('tool_results', [])} 个工具")
                if reply_data.get("phase_transition"):
                    old_p = reply_data["phase_transition"]["from"]
                    new_p = reply_data["phase_transition"]["to"]
                    print(f"\n🔄 阶段转换: {old_p} → {new_p}")

            elif cmd == "generate":
                if not agent.project:
                    print("❌ 请先创建一个项目: new")
                    continue
                print("正在生成研究方案...")
                filepath = agent.generate_full_proposal()
                if filepath:
                    print(f"✅ 方案已生成: {filepath}")
                else:
                    print("❌ 方案生成失败。请确保已配置 DASHSCOPE_API_KEY。")

            elif cmd == "knowledge":
                if not agent.project:
                    print("❌ 请先创建一个项目: new")
                    continue
                subcmd = args.split()[0].lower() if args.split() else ""
                if subcmd == "add":
                    title = args.split(None, 1)[1] if len(args.split()) > 1 else ""
                    if not title:
                        print("用法: knowledge add <标题>")
                        content = input("内容: ")
                        title = args
                    else:
                        content = input("内容: ")
                    entry_id = agent.project.add_knowledge_entry(title, content, source="user")
                    print(f"✅ 知识条目已添加 (ID: {entry_id})")
                elif subcmd == "list":
                    entries = agent.project.get_knowledge_entries()
                    if not entries:
                        print("知识库为空。使用 'knowledge add' 添加资料。")
                    else:
                        for e in entries:
                            print(f"  [{e['id']}] {e['title']} ({e['category']}) — {e['created_at']}")
                else:
                    print("用法: knowledge add/list")

            elif cmd == "status":
                if not agent.project:
                    print("❌ 没有活跃项目。")
                    continue
                p = agent.project
                print(f"项目ID: {p.project_id}")
                print(f"阶段: {p.phase}")
                print(f"研究者信息:")
                for k, v in p.get_all_researcher_info().items():
                    print(f"  • {k}: {v}")
                print(f"知识库条目: {len(p.get_knowledge_entries())}")
                print(f"搜索结果缓存: {len(p.get_cached_searches())}")
                print(f"自由笔记: {len(p.get_notes())}")
                proposal = p.get_latest_proposal()
                print(f"最新方案: {'有' if proposal else '无'}")

            elif cmd == "sync":
                if not agent.project:
                    print("❌ 没有活跃项目。")
                    continue
                if args.lower() == "dingtalk":
                    if agent.config.dingtalk_available:
                        success = agent.sync_to_dingtalk()
                        print(f"{'✅' if success else '❌'} 同步结果: {'成功' if success else '失败'}")
                    else:
                        print("❌ 钉钉未配置。请在 config.json 中设置 dingtalk_webhook 和 dingtalk_enabled=true")

            else:
                if not agent.project:
                    print("❌ 请先创建一个项目: new")
                    continue
                reply_data = agent.chat(user_input)
                print()
                print(reply_data.get("reply", ""))
                if reply_data.get("guiding_questions"):
                    print()
                    print("💡 引导问题:")
                    for q in reply_data["guiding_questions"]:
                        print(f"  • {q}")

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
=== 田野旅行规划 Agent 帮助 ===

【项目管理】
  new [name]        创建新项目
  list              列出所有项目
  open <id>         打开已有项目

【对话交互】
  chat <message>    发送消息（带参数时立即回复）
                     （不带参数时进入逐行输入模式）
  直接输入文本      等同于 chat <文本>

【方案生成】
  generate          基于当前所有信息生成完整研究方案
                     输出为 Markdown 文件

【知识库】
  knowledge add     添加自有研究资料到本地知识库
  knowledge list    查看所有已存储的知识条目

【状态与同步】
  status            查看当前项目的完整状态
  sync dingtalk     将最新方案同步到钉钉群

【工具说明】
  本 Agent 深度集成飞猪 FlyAI 全部 8 个搜索工具：
  • search-flight     航班搜索 — 大交通规划
  • search-train      火车搜索 — 高铁备选方案
  • search-hotel      酒店搜索 — 住宿筛选（支持民宿/客栈）
  • search-poi        景点搜索 — 30+分类文化场所检索
  • keyword-search    关键词搜索 — 签证/保险/租车等广义覆盖
  • ai-search         AI语义搜索 — 自然语言理解复杂意图（核心杀手锏）
  • marriott-hotel    万豪酒店 — 高端备选
  • marriott-package  万豪套餐 — 体验产品推荐

【其他】
  help              显示此帮助
  quit / exit       退出程序""")


if __name__ == "__main__":
    main()
