"""
田野罗盘 Agent — 主控制器
协调对话引擎、FlyAI 工具调用、LLM 生成，输出完整研究方案
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..tools.dashscope_tools import DASHSCOPE_AVAILABLE, DashScopeChat
from ..tools.flyai_tools import FlyAITools
from .config import Config
from .dialogue_engine import DialogueEngine
from .state import ProjectState


class FieldAgent:
    """田野罗盘 Agent — 核心控制器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.project: Optional[ProjectState] = None
        self.dialogue = None
        self.flyai = FlyAITools(self.config.flyai_api_key)

        if DASHSCOPE_AVAILABLE:
            self.llm_client = DashScopeChat(
                api_key=self.config.dashscope_api_key,
                model=self.config.chat_model,
            )
        else:
            self.llm_client = None

    # ─── 项目管理 ──────────────────────────────

    def create_project(self, project_id: Optional[str] = None) -> ProjectState:
        if not project_id:
            project_id = f"field_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.project = ProjectState(self.config, project_id)
        self.dialogue = DialogueEngine(self.project)
        return self.project

    def load_project(self, project_id: str) -> ProjectState:
        pdir = self.config.projects_dir / project_id
        if not pdir.exists():
            raise FileNotFoundError(f"Project '{project_id}' not found")
        self.project = ProjectState(self.config, project_id)
        self.dialogue = DialogueEngine(self.project)
        return self.project

    def list_projects(self) -> List[Dict[str, Any]]:
        projects = []
        for pdir in sorted(self.config.projects_dir.iterdir()):
            state_file = pdir / "state.json"
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                projects.append({
                    "id": state.get("project_id", pdir.name),
                    "phase": state.get("phase", ""),
                    "created_at": state.get("created_at", ""),
                    "updated_at": state.get("updated_at", ""),
                })
        return projects

    # ─── 核心对话循环 ──────────────────────────────

    def chat(self, user_message: str, stream: bool = False) -> Dict[str, Any]:
        """处理一轮对话（非流式）"""
        if not self.project:
            return {"error": "No active project. Call create_project() first."}

        # Step 1: 对话引擎分析
        engine_result = self.dialogue.process_user_message(user_message)

        # Step 2: 执行工具调用
        tool_results = []
        if engine_result["tool_calls"]:
            tool_results = self._execute_tool_calls(engine_result["tool_calls"])

        # Step 3: 构建 LLM 消息列表
        llm_messages = list(engine_result["messages_to_llm"])

        # 将工具结果追加到消息历史
        for tr in tool_results:
            llm_messages.append({
                "role": "tool",
                "content": json.dumps(tr, ensure_ascii=False)[:2000],  # 截断过长内容
                "metadata": {"tool": tr.get("_tool_name", "unknown"), "purpose": tr.get("_purpose", "")},
            })

        messages_for_llm = [
            {"role": "system", "content": engine_result["system_prompt"]}
        ] + llm_messages

        # Step 4: 调用 LLM 生成回复
        if self.llm_client:
            try:
                response = self.llm_client.chat(messages=messages_for_llm, temperature=0.7, max_tokens=4096)
                assistant_reply = response.get("content", "")
            except Exception as e:
                assistant_reply = f"LLM 调用失败：{e}。以下是基于搜索结果的回复：\n\n{self._format_tool_results_for_display(tool_results)}"
        else:
            assistant_reply = self._rule_based_reply(user_message)

        # Step 5: 记录助手回复
        self.project.append_chat("assistant", assistant_reply, {
            "phase": engine_result["phase"],
            "tool_results_count": len(tool_results),
            **engine_result.get("msg_meta", {}),
        })

        # Step 6: 缓存搜索结果
        for tr in tool_results:
            if isinstance(tr, dict) and "_search_query" in tr:
                self.project.cache_search_result(
                    tr["_search_query"],
                    tr,
                    source_tool=tr.get("_tool_name", "unknown"),
                )

        return {
            "reply": assistant_reply,
            "phase": engine_result["phase"],
            "guiding_questions": engine_result["guiding_questions"],
            "tool_results": tool_results,
            "tool_calls_executed": len(tool_results),
            "phase_transition": engine_result.get("msg_meta", {}).get("phase_transition"),
        }

    def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """根据工具调用指令执行实际的工具调用"""
        results = []
        method_map = {
            "search_flight": self.flyai.search_flight,
            "search_train": self.flyai.search_train,
            "search_hotel": self.flyai.search_hotel,
            "search_poi": self.flyai.search_poi,
            "keyword_search": self.flyai.keyword_search,
            "ai_search": self.flyai.ai_search,
            "search_marriott_hotel": self.flyai.search_marriott_hotel,
            "search_marriott_package": self.flyai.search_marriott_package,
        }

        for call in tool_calls:
            tool_name = call.get("tool", "")
            args = call.get("args", {})
            purpose = call.get("purpose", "")

            method = method_map.get(tool_name)
            if not method:
                results.append({"_tool_name": tool_name, "error": f"Unknown tool: {tool_name}", "_purpose": purpose})
                continue

            try:
                result = method(**args)
                result["_tool_name"] = tool_name
                result["_purpose"] = purpose
                result["_search_query"] = args.get("query", f"{tool_name}_{args}")
                results.append(result)
            except Exception as e:
                results.append({
                    "_tool_name": tool_name,
                    "error": str(e),
                    "_purpose": purpose,
                    "_search_query": args.get("query", ""),
                })

        return results

    def _format_tool_results_for_display(self, tool_results: List[Dict[str, Any]]) -> str:
        """将工具调用结果格式化为可读文本"""
        display_parts = []

        for tr in tool_results:
            tool_name = tr.get("_tool_name", "unknown")
            purpose = tr.get("_purpose", "")
            status = tr.get("status", -1)

            if status == -1:
                display_parts.append(f"\n❌ [{purpose}] {tool_name}: {tr.get('error', 'failed')}")
                continue

            data = tr.get("data", {})
            items = data.get("itemList", []) if isinstance(data, dict) else []

            title_map = {
                "search_flight": "航班信息",
                "search_train": "火车信息",
                "search_hotel": "住宿信息",
                "search_poi": "景点/文化场所",
                "keyword_search": "搜索结果",
                "ai_search": "AI语义搜索结果",
            }
            title = title_map.get(tool_name, tool_name)

            if items:
                display_parts.append(f"\n✅ [{purpose}] {title} ({len(items)}条):")
                for i, item in enumerate(items[:3]):  # 最多显示前3条
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("title") or ""
                        price = item.get("price") or item.get("adultPrice") or ""
                        pic_url = item.get("mainPic") or item.get("picUrl") or ""
                        jump_url = item.get("jumpUrl") or item.get("detailUrl") or ""

                        line = f"  {i+1}. {name}"
                        if price:
                            line += f" — {price}"
                        if pic_url:
                            line += f"\n     ![image]({pic_url})"
                        if jump_url:
                            line += f"\n     [预订链接]({jump_url})"
                        display_parts.append(line)

        return "\n".join(display_parts)

    # ─── 规则回复（降级方案）─────────────

    def _rule_based_reply(self, message: str) -> str:
        phase = self.project.phase if self.project else "research_positioning"

        replies = {
            "research_positioning": (
                "感谢您的描述。为了帮您制定更精准的田野调查旅行方案，我需要了解几个关键信息：\n\n"
                "1. 您的学科背景和研究取向是什么？\n"
                "2. 初步的研究问题或关注焦点是什么？\n"
                "3. 您对田野地点的确定程度如何？\n\n"
                "请尽量详细地告诉我，我会根据您的回答逐步完善方案。"
            ),
            "field_site_profiles": (
                "好的，我来帮您收集候选田野点的详细信息。\n\n"
                "请告诉我您关注的目标区域或具体地点名称。"
            ),
            "itinerary_planning": (
                "了解了。现在我来为您规划详细的行程安排。\n\n"
                "请告诉我您的出发城市和预期的调研日期。"
            ),
            "proposal_generation": (
                "正在整合所有信息，生成完整的研究方案...\n\n"
                "方案将包含研究背景、方法论、详细行程、预算明细、风险评估等完整章节。"
            ),
        }

        return replies.get(phase, "收到，请继续说明您的需求。")

    # ─── 方案生成 ──────────────────────────────

    def generate_full_proposal(self) -> Optional[str]:
        """基于当前所有状态生成完整研究方案"""
        if not self.project or not self.llm_client:
            return None

        context = self._build_proposal_context()

        proposal_messages = [
            {"role": "system", "content": self.dialogue.SYSTEM_PROMPTS[
                ProjectState.PHASE_PROPOSAL_GENERATION
            ]},
            {"role": "user", "content": f"请根据以下田野调查项目的全部信息，生成一份完整的研究方案：\n\n{context}"},
        ]

        try:
            response = self.llm_client.chat(
                messages=proposal_messages, temperature=0.5, max_tokens=8192
            )
            proposal = response.get("content", "")
            if proposal:
                filepath = self.project.save_proposal(proposal, version="draft")
                return str(filepath)
        except Exception as e:
            return f"方案生成失败：{e}"

        return None

    def _build_proposal_context(self) -> str:
        ctx_parts = []

        info = self.project.get_all_researcher_info()
        if info:
            ctx_parts.append("## 研究者信息\n")
            for k, v in info.items():
                ctx_parts.append(f"- **{k}**: {v}")
            ctx_parts.append("")

        sites = self.project.state.get("field_sites", [])
        if sites:
            ctx_parts.append("## 候选田野点\n")
            for s in sites:
                ctx_parts.append(f"- **{s.get('name', '未知')}**{'(已确认)' if s.get('confirmed') else ''}")
                if s.get("description"):
                    ctx_parts.append(f"  - 简介: {s['description']}")
            ctx_parts.append("")

        searches = self.project.get_cached_searches()
        if searches:
            ctx_parts.append("## 已收集的调研数据\n")
            for s in searches[:10]:
                ctx_parts.append(f"- [{s.get('query', '')}] ({s.get('source_tool', '')})")
            ctx_parts.append("")

        msgs = self.project.get_recent_messages(10)
        if msgs:
            ctx_parts.append("## 近期对话摘要\n")
            for m in msgs[-6:]:
                role_label = "研究者" if m["role"] == "user" else "助手"
                content = m["content"][:200]
                ctx_parts.append(f"- **{role_label}**: {content}...")
            ctx_parts.append("")

        return "\n".join(ctx_parts)

    # ─── 云同步 ──────────────────────────────

    def sync_to_dingtalk(self, message: str = "") -> bool:
        if not self.config.dingtalk_available:
            return False

        proposal = self.project.get_latest_proposal()
        if not proposal:
            return False

        webhook = self.config.dingtalk_webhook
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"田野旅行方案 - {self.project.project_id}",
                "text": f"### 田野旅行方案\n\n{message or '最新研究方案已更新'}\n\n```\n{proposal[:2000]}\n```",
            },
        }

        try:
            import urllib.request
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False
