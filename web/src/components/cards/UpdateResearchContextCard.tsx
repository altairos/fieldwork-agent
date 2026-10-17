import { Chip } from "../ui/Chip";
import { IconCompass } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, strOf } from "./data";
import type { ToolCardProps } from "./types";

/**
 * UpdateResearchContextCard — 研究定位状态写入卡（宿主层工具 update_research_context）。
 *
 * 轮 2 症状②：该工具由 web_host/agent/state_tools.py 每回合注入 loop（非内核
 * 14 能力），此前不在注册表 → 落 ToolCardFallback 显示裸 JSON；本卡补齐结构化视图。
 *
 * data 形状（web 宿主版，state_tools.py 轮 2 起）：
 *   { updated: string[], reason?: string } —— updated = 本次实际写入的白名单
 *   字段名（排序后），reason = 一句话依据（缺省省略键）。
 *   注：mcp_server 版同名工具返回完整上下文 dict，服务外部 MCP 客户端，不经
 *   本前端时间线，不在适配范围（不臆造）。
 *
 * 呈现红线：字段名 → 中文标签如实映射（与右栏 KnowledgePanel SLOTS 同口径，
 * 轮 2 方案 B 新增 research_questions 深挖槽），未知键原样呈现不臆造标签；
 * reason 原文呈现，空则整行不渲染。
 */
const LABELS: Record<string, string> = {
  discipline: "学科背景",
  topic: "研究主题",
  research_questions: "研究问题",
  site_status: "田野点确定度",
  scale: "调研规模",
  budget: "经费",
  time_window: "时间窗口",
  departure_city: "出发城市",
  target_location: "目标地点",
  travel_date: "出行日期",
  purpose: "输出用途",
  interviews_not_applicable: "访谈不适用",
  interviews_na_reason: "不适用原因",
};

export function UpdateResearchContextCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const updated = asArray(rec.updated).map((f) => strOf(f)).filter((f) => f !== "");
  const reason = strOf(rec.reason);
  const statusChip =
    updated.length > 0 ? <Chip tone="neutral">{`${updated.length} 项`}</Chip> : undefined;

  return (
    <CardShell
      title="研究定位更新"
      icon={<IconCompass className="w-4 h-4" />}
      degraded={degraded}
      statusChip={statusChip}
    >
      {updated.length === 0 && reason === "" ? (
        <NoData hint="未取得写入字段；本次调用可能失败或未改任何槽位。" />
      ) : (
        <div className="space-y-2">
          {updated.length > 0 && (
            <>
              <div className="text-[12px] text-inksoft">{`研究定位已更新 ${updated.length} 项`}</div>
              <div className="flex flex-wrap gap-1.5">
                {updated.map((f) => (
                  <Chip key={f} tone={f === "research_questions" ? "indigo" : "neutral"}>
                    {LABELS[f] ?? f}
                  </Chip>
                ))}
              </div>
            </>
          )}
          {reason !== "" && (
            <div className="text-[11.5px] text-inkfaint">{`依据：${reason}`}</div>
          )}
        </div>
      )}
    </CardShell>
  );
}
