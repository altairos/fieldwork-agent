import { Chip } from "../ui/Chip";
import { EmptyState } from "../ui/EmptyState";
import { IconBook, IconCompass } from "../ui/icons";
import { asRecord, strOf } from "../cards/data";
import { useProjectStore } from "../../stores/project";

/**
 * KnowledgePanel — 右栏「知识库」面板（研究定位投影 + 学科纵深静态说明）。
 *
 * 设计板同源：右栏知识库面板 lines 661-669
 *   标题 line 663 `font-serif text-[15px] font-600 mb-3`
 *   条目卡 line 665 `hairline rounded-lg bg-card p-3` + 主行 `text-[12.5px] font-medium`
 *     + 副行 `text-[11px] text-inkfaint mt-1`（此处主行 = 槽位值，副行 = 槽位名，不臆造资料标题）
 *   学术纵深卡 lines 379-382 `hairline rounded-lg bg-card p-5` + `font-serif text-[15px] font-600 mb-2`
 *     + `text-[12px] text-inksoft leading-relaxed`（正文逐字取自设计板 line 381）
 *   底部说明行 line 668 `hairline rounded-lg bg-paper/60 p-3 mt-3 flex items-center gap-2
 *     text-[12px] text-inkfaint`
 *
 * 同源红线②：只读 `snapshot.research_context`（REST 初始快照 = 内核 ResearchContext 十槽位），
 * 面板 0 处 fetch、不改写字段值（原样呈现内核抽取结果）；未填槽位如实计入「已填 N / 10」，
 * 不猜、不补默认值。
 * 说明：web_host 当前**无知识库/资料端点**（`GET /projects/{id}` 只回项目快照），故本面板不做
 * 设计板 line 668 的「添加自有资料」入口（避免死按钮），底部改为如实说明面板的投影性质。
 */

/** 十槽位 → 中文行标（阶段 A 清单语汇取设计板 lines 622-627）。 */
const SLOTS: { key: string; label: string }[] = [
  { key: "discipline", label: "学科背景" },
  { key: "topic", label: "研究问题" },
  { key: "site_status", label: "田野点确定度" },
  { key: "scale", label: "调研规模" },
  { key: "budget", label: "经费" },
  { key: "time_window", label: "时间窗口" },
  { key: "departure_city", label: "出发城市" },
  { key: "target_location", label: "目标地点" },
  { key: "travel_date", label: "出行日期" },
  { key: "purpose", label: "输出用途" },
];

/** purpose 枚举 → 中文（判词依据 kernel/guidance/extract.py L127-136）；未知值原样呈现。 */
const PURPOSE_LABEL: Record<string, string> = {
  kaiti: "开题报告",
  grant: "基金申报",
  course: "课程作业",
  global: "海外/留学申请",
};

/** 学术纵深静态说明（设计板 line 381 逐字）。 */
const DEPTH_TEXT =
  "内置 10+ 类访谈对象类型库（村落长老/宗族头人/宗教人士/基层干部/返乡务工者/非遗传承人…），每类含接近策略、注意事项、建议提问域与伦理提示，可自动生成半结构式访谈提纲并嵌入提案第三章。";

export function KnowledgePanel() {
  const snapshot = useProjectStore((s) => s.snapshot);
  const ctx = asRecord(snapshot?.research_context);
  const hasContext = snapshot !== null && Object.keys(ctx).length > 0;

  const slots = SLOTS.map((slot) => {
    const raw = strOf(ctx[slot.key]);
    const value = slot.key === "purpose" && raw ? (PURPOSE_LABEL[raw] ?? raw) : raw;
    return { ...slot, value };
  });
  const filledCount = slots.filter((s) => s.value !== "").length;
  // 野生字段（内核 extras 收口）：只呈现标量，原样文本，不解释、不加工
  const extras = Object.entries(asRecord(ctx.extras)).filter(
    ([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean",
  );

  return (
    <div>
      <div className="font-serif text-[15px] font-semibold mb-3">研究定位 · 知识库</div>

      {!hasContext || filledCount === 0 ? (
        <EmptyState
          icon={<IconCompass className="w-4 h-4" />}
          title="尚未取得研究定位"
          hint="阶段 A 的访谈会逐槽位补齐（学科·问题·规模·经费·时间窗）；本面板只做投影，不改写内核字段"
        />
      ) : (
        <>
          <div className="space-y-2">
            {slots.map((s) =>
              s.value === "" ? null : (
                <div key={s.key} className="hairline rounded-lg bg-card p-3">
                  <div className="text-[12.5px] font-medium text-ink break-words">{s.value}</div>
                  <div className="text-[11px] text-inkfaint mt-1">{s.label}</div>
                </div>
              ),
            )}
          </div>
          <div className="num text-[11px] text-inkfaint mt-2">
            {`已填 ${filledCount} / ${SLOTS.length} 槽位`}
          </div>

          {extras.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {extras.map(([key, value]) => (
                <Chip key={key} tone="neutral">
                  {`${key} ${String(value)}`}
                </Chip>
              ))}
            </div>
          )}
        </>
      )}

      <div className="rule my-4" />

      {/* 学术纵深（设计板 lines 379-382 静态说明，非工具数据，不与同源红线冲突） */}
      <div className="hairline rounded-lg bg-card p-4">
        <div className="font-serif text-[15px] font-semibold mb-2 flex items-center gap-2">
          <IconBook className="w-4 h-4 text-cinnabar" />
          学术纵深
        </div>
        <p className="text-[12px] text-inksoft leading-relaxed">{DEPTH_TEXT}</p>
      </div>

      <div className="hairline rounded-lg bg-paper/60 p-3 mt-3 flex items-center gap-2 text-[12px] text-inkfaint">
        <IconCompass className="w-4 h-4 shrink-0" />
        本面板为状态投影：数值与文本一律来自内核快照与工具返回，不经模型转述。
      </div>
    </div>
  );
}
