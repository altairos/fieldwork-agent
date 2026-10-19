/**
 * 内核数据契约镜像 — 对齐 `kernel/models.py` 的 pydantic 模型。
 *
 * 翻译规则：Python `float|None` → `number|null`；`str` → `string`；
 * `dict[str,Any]` → `Record<string,unknown>`；`list[X]` → `X[]`。
 */

// ---------------------------------------------------------------- 统一返回包络

/** 错误三元组：机器可读 code + 人读 message + 可选 hint（下一步建议）。 */
export interface ToolError {
  code: string;
  message: string;
  hint: string;
}

/** 所有能力函数的统一返回包络。 */
export interface Envelope<T = unknown> {
  ok: boolean;
  data?: T | null;
  error?: ToolError | null;
  degraded?: string | null;
}

// ---------------------------------------------------------------- 检索与行情

/** 单条搜索结果（酒店/机票/门票等），价格已归一化为 number 元。 */
export interface SearchItem {
  name: string;
  price: number | null;
  price_raw: string | null;
  jump_url: string | null;
  pic_url: string | null;
  extra: Record<string, unknown>;
}

/** 一页搜索结果 + 极值摘要，供预算与节奏判断直接取用。 */
export interface ItemPage {
  items: SearchItem[];
  total: number;
  cheapest: number | null;
  dearest: number | null;
}

// ---------------------------------------------------------------- 田野点画像

/** 从田野点资料中抽取的量化提示（分节计数、住宿/交通最低价、降级建议）。 */
export interface SummaryHints {
  section_counts: Record<string, number>;
  accommodation_nightly_min: number | null;
  transport_min: number | null;
  /** 段名 → 人读建议（如"枢纽城市住宿+当地学术接待"）。 */
  hints: Record<string, string>;
}

/** 一个候选田野点的完整画像。 */
export interface FieldSiteProfile {
  location: string;
  transport: ItemPage | null;
  accommodation: ItemPage | null;
  culture: Record<string, unknown>[];
  amenities: ItemPage | null;
  summary: SummaryHints;
  generated_at: string;
}

// ---------------------------------------------------------------- 行程与预算

/** 行程节奏分段（如前期密集访谈 / 后期整理补充）。 */
export interface PhaseSegment {
  label: string;
  days: number;
  note: string;
}

/**
 * 预算条目：金额必须携带可复核的计价口径与来源。
 * source 标注金额来源（search / cheapest / estimate / manual）。
 */
export interface BudgetItem {
  name: string;
  amount: number;
  source: string;
  currency: string;
  unit: string;
  quantity: number;
  people: number;
  priced_at: string;
  source_ref: string;
  amount_low?: number | null;
  amount_high?: number | null;
  basis?: string;
}

/**
 * 预算汇总。coverage：complete = 全部报价已获取，partial = 部分缺失，
 * unknown = 无任何报价；仅 coverage==complete 时 over_budget 才有
 * "完整方案是否超支"的意义。uncovered 列出缺失报价的条目名。
 */
export interface BudgetBreakdown {
  items: BudgetItem[];
  total: number;
  budget_total: number;
  over_budget: boolean;
  coverage: string;
  uncovered: string[];
  total_high?: number | null;
  over_budget_possible?: boolean;
}

/** 多目的地行程方案。 */
export interface ItineraryPlan {
  departure: string;
  destinations: string[];
  start_date: string;
  end_date: string;
  legs: Record<string, unknown>[];
  stays: Record<string, unknown>[];
  rhythm: PhaseSegment[];
  buffer_days: number;
  alternatives: Record<string, unknown>[];
  budget: BudgetBreakdown;
  generated_at: string;
  /** 产出时的关键输入哈希，宿主用于判断行程是否因输入变更而失效。 */
  inputs_hash: string;
}

// ---------------------------------------------------------------- 研究上下文

export interface ResearchNote {
  id: string;
  kind: "question" | "interpretation" | "method" | "literature_gap" | "decision" | "observation";
  content: string;
  origin: "user" | "agent" | "literature";
  status: "proposed" | "adopted" | "rejected" | "unresolved" | "superseded";
  basis: string;
  references: string[];
  scope_fingerprint: string;
}

export interface InterviewState {
  current_focus: string;
  last_user_message: string;
  last_assistant_message: string;
  open_questions: string[];
  deferred_fields: string[];
}

/** 研究定位槽位；空字符串表示未填，extras 收野生字段。 */
export interface ResearchContext {
  /** 项目显示名（任务1：用户可改 / agent 自动生成，≤30 字；空串 = 未命名，显示层回退 topic）。 */
  title: string;
  discipline: string;
  topic: string;
  site_status: string;
  scale: string;
  budget: string;
  time_window: string;
  departure_city: string;
  target_location: string;
  travel_date: string;
  /** 输出用途（global|kaiti|grant|course，缺省视为 global）。 */
  purpose: string;
  /** 用户已明确表达或采纳的核心研究问题。 */
  research_questions: string;
  /** 以下字段为增量扩展，旧项目可以缺省；范围指纹由宿主生成。 */
  research_question_scope?: string;
  research_notes?: ResearchNote[];
  interview_state?: InterviewState;
  /** 非访谈型研究显式跳过访谈对象硬条件（附理由）。 */
  interviews_not_applicable: boolean;
  interviews_na_reason: string;
  extras: Record<string, unknown>;
}

/** 阶段流转检查结果。 */
export interface AdvanceResult {
  can_advance: boolean;
  to_phase: string | null;
  missing_fields: string[];
}

// ---------------------------------------------------------------- 记录与文档

/** 落盘的田野点记录；profile 存 FieldSiteProfile 的 dict 形式。 */
export interface FieldSiteRecord {
  /** repo 记录键，`site_` 前缀；旧快照无此键→undefined。 */
  id?: string;
  name: string;
  confirmed: boolean;
  profile: Record<string, unknown> | null;
}

/** 报道人（访谈对象）档案。 */
export interface InformantRecord {
  /** repo 记录键，`inf_` 前缀；旧快照无此键→undefined。 */
  id?: string;
  code: string;
  type: string;
  relation: string;
  contact: string;
  notes: string;
}

/**
 * 提案/纪要文档，markdown 为唯一真源。
 * requested_mode 记录用户所选体例，mode 记录实际生成体例；
 * inputs_hash 供宿主做确认/失效判定。
 */
export interface ProposalDoc {
  markdown: string;
  kind: string;
  version: string;
  sections: string[];
  requested_mode: string;
  mode: string;
  inputs_hash: string;
}

/** 访谈对象候选档案（六字段，suggest_informants 产物的目标结构）。 */
export interface InformantCandidate {
  type: string;
  approach: string;
  cautions: string;
  question_domains: string[];
  ethics: string;
  access_channel: string;
  preparation: string;
}

// ---------------------------------------------------------------- 项目快照
// （对齐 Plan 3 web_host/api/projects.py 的响应形状）

/** GET /projects/{id} 响应。 */
export interface ProjectSnapshot {
  project_id: string;
  phase: string;
  research_context: Partial<ResearchContext>;
  field_sites: FieldSiteRecord[];
  informants: InformantRecord[];
  proposals: { id: string; kind?: string; version?: string; created_at?: string }[];
  phase_history: { from?: string; to?: string; at?: string }[];
  updated_at?: string;
  /** 方案确认状态（web_host/api/projects.py get_project 新增两键）。 */
  proposal_confirmed?: boolean;
  /** true = 确认后研究信息已变更，需重新确认。 */
  proposal_confirmation_stale?: boolean;
}

/** GET /projects 列表项。 */
export interface ProjectSummary {
  project_id: string;
  phase: string;
  created_at?: string;
  updated_at?: string;
  research_context: Partial<ResearchContext>;
  counts: { sites: number; informants: number; proposals: number };
}
