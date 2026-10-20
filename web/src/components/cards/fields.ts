import { asArray, asRecord, strOf } from "./data";

/**
 * fields.ts — 搜索条目的「用户友好摘要」层。
 *
 * 为什么需要这一层（用户反馈）：结果卡原先直接把 `extra` 的键值对原样打出来
 * （`extraLine`），实测线上看到的是
 *   住宿  「address 美中路77号 · commissionMoneyYuan 0.00 · decorationTime 2023-01-01」
 *   高德  「keytag 卫生院 · type 医疗保健服务;综合医院;卫生院」
 *   火车  「未命名条目 · totalDuration 224」（车次/车站/时间全在 extra.journeys 里没被读）
 * 既外露内部字段（佣金），又是英文键名，普通用户读不懂。
 *
 * 本层的三条规则：
 * 1. **语义化**：按数据形状分族（行程 / 高德地点 / 住宿 / keyword-info），只输出
 *    对选点有意义的信息，键名不外露；
 * 2. **不造假**：只读 payload 里真实存在的字段，缺就不显示（不补 0、不猜测、不换算）；
 * 3. **不丢数据**：未归入摘要的标量进 `more`（气泡「更多信息」折叠），
 *    内部/商业敏感字段（佣金、商户 id、坐标副本、图片数组）一律不进。
 */

/**
 * 摘要层的输入：只需要可选的 name 与 extra。SearchItem 天然满足；行程卡的
 * `leg.chosen`（name/price/price_raw/extra）这类局部条目也能直接复用，不必先补全字段。
 */
export interface DigestInput {
  name?: string;
  extra?: Record<string, unknown>;
}

/** 内部或技术字段：对选点无意义、或属商业信息，摘要与「更多信息」都不展示。 */
const HIDDEN_KEYS = new Set([
  // 飞猪内部
  "commissionMoneyYuan", "skuCommissionStruct", "rate", "shId", "pk",
  "latitude", "longitude", "jumpUrl", "picUrl", "info",
  // 高德内部
  "id", "parent", "childtype", "typecode", "shopinfo", "biz_ext", "photos",
  // 归一化时已消费的结构
  "journeys", "segments", "stopInfos",
]);

/** 「更多信息」里的中文标签（未登记的键回落原名，不臆造语义）。 */
const LABELS: Record<string, string> = {
  address: "地址",
  star: "档次",
  interestsPoi: "周边",
  brandName: "品牌",
  decorationTime: "装修",
  distance: "距离",
  keytag: "关键词",
  type: "类型",
  adname: "区县",
  cityname: "城市",
  pname: "省份",
  location: "坐标",
  facility_kind: "设施",
  totalDuration: "总时长",
  journeyType: "行程",
  title: "标题",
  tags: "标签",
  scoreDesc: "评分",
  open_time: "营业时间",
  rating: "评分",
};

/** 标量 → 文本；对象/数组/null 一律空串（不 JSON 化、不显示 [object Object]）。 */
function scalar(v: unknown): string {
  if (typeof v === "string") return v.trim();
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  if (typeof v === "boolean") return v ? "是" : "否";
  return "";
}

/** 高德 `type` 是 "医疗保健服务;综合医院;卫生院" 形式，取最末一级最有辨识度。 */
function typeTail(raw: string): string {
  const parts = raw.split(";").map((s) => s.trim()).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

/** "2026-11-15 14:16:00" / "14:16" → "14:16"；无时间形态返回 "". */
function hhmm(raw: string): string {
  const m = /(\d{1,2}):(\d{2})/.exec(raw);
  return m ? `${m[1].padStart(2, "0")}:${m[2]}` : "";
}

/** 分钟 → "3 小时 44 分"；null → ""（不按 0 兜底）。 */
function durationText(minutes: number | null): string {
  if (minutes === null) return "";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} 分钟`;
  return m === 0 ? `${h} 小时` : `${h} 小时 ${m} 分`;
}

function numberOf(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && Number.isFinite(Number(v))) return Number(v);
  return null;
}

/** 距底距离（高德 around_search 的 distance，单位米）。 */
function distanceText(raw: string): string {
  const n = numberOf(raw);
  if (n === null) return "";
  return n >= 1000 ? `${(n / 1000).toFixed(1)} 公里` : `${Math.round(n)} 米`;
}

/** 行程族：search_trains / search_flights 的 extra.journeys[0]（火车实测落在这里）。 */
function journeyDigest(item: DigestInput): { title: string; summary: string; used: string[] } | null {
  const extra = item.extra ?? {};
  const journeys = asArray(extra.journeys);
  if (journeys.length === 0) return null;
  const journey = asRecord(journeys[0]);
  const segs = asArray(journey.segments);
  if (segs.length === 0) return null;
  const first = asRecord(segs[0]);
  const last = asRecord(segs[segs.length - 1]);

  const no = strOf(first.marketingTransportNo) || strOf(first.flightNo) || strOf(first.trainNo);
  const kind = strOf(first.marketingTransportName) || strOf(first.transportType);
  const depPlace = strOf(first.depStationName) || strOf(first.depAirportName) || strOf(first.depStationShortName);
  const arrPlace = strOf(last.arrStationName) || strOf(last.arrAirportName) || strOf(last.arrStationShortName);
  const depTime = hhmm(strOf(first.depDateTime) || strOf(first.depTime));
  const arrTime = hhmm(strOf(last.arrDateTime) || strOf(last.arrTime));
  const seat = strOf(first.seatClassName);
  const minutes = numberOf(journey.totalDuration) ?? numberOf(first.duration);
  const transfers = Math.max(0, segs.length - 1);

  const timing = depPlace || arrPlace
    ? `${depPlace}${depTime ? ` ${depTime}` : ""} → ${arrPlace}${arrTime ? ` ${arrTime}` : ""}`.trim()
    : "";
  const summary = [
    timing,
    durationText(minutes),
    transfers > 0 ? `换乘 ${transfers} 次` : strOf(journey.journeyType),
    seat,
  ].filter(Boolean).join(" · ");
  return {
    title: [no, kind].filter(Boolean).join(" "),
    summary,
    used: ["totalDuration", "journeyType"],
  };
}

/** 高德地点族：extra 里有 location（"lng,lat"）/ type——类型末级 · 地址 · 距离。 */
function amapDigest(item: DigestInput): { title: string; summary: string; used: string[] } | null {
  const extra = item.extra ?? {};
  if (!strOf(extra.location) && !strOf(extra.type)) return null;
  const kind = typeTail(strOf(extra.type)) || strOf(extra.keytag);
  const address = strOf(extra.address);
  const distance = distanceText(strOf(extra.distance));
  const hours = strOf(extra.open_time);
  return {
    title: "",
    summary: [kind, address, distance, hours && `营业 ${hours}`].filter(Boolean).join(" · "),
    used: ["type", "address", "distance", "keytag", "open_time"],
  };
}

/** 住宿族：star/interestsPoi/address（星级 · 周边地标 · 地址）。 */
function hotelDigest(item: DigestInput): { title: string; summary: string; used: string[] } | null {
  const extra = item.extra ?? {};
  const starRaw = strOf(extra.star);
  const nearby = strOf(extra.interestsPoi);
  const address = strOf(extra.address);
  if (!starRaw && !nearby && !address) return null;
  const starNum = numberOf(starRaw);
  const star = starNum !== null ? (starNum > 0 ? `${starNum} 星` : "") : starRaw;
  const brand = strOf(extra.brandName);
  return {
    title: "",
    summary: [star, brand, nearby, address].filter(Boolean).join(" · "),
    used: ["star", "interestsPoi", "address", "brandName"],
  };
}

/** keyword 族：条目被包在 extra.info 里（标题/标签/评分/星级）。 */
function infoDigest(item: DigestInput): { title: string; summary: string; used: string[] } | null {
  const info = asRecord((item.extra ?? {}).info);
  if (Object.keys(info).length === 0) return null;
  const starNum = numberOf(info.star);
  const star = starNum !== null ? (starNum > 0 ? `${starNum} 星` : "") : strOf(info.star);
  const tags = scalar(info.tags);
  const score = strOf(info.scoreDesc) || strOf(info.rating);
  return {
    title: "",
    summary: [star, tags, score && `评分 ${score}`].filter(Boolean).join(" · "),
    used: [],
  };
}

export interface ItemDigest {
  /** name 缺失时的可读标题（如车次「D236 动车」）；无替代返回 "" */
  title: string;
  /** 语义化副行（键名不外露）；无可读信息返回 "" */
  summary: string;
  /** 「更多信息」：未归入摘要的可展示标量（已剔除内部字段） */
  more: Array<[string, string]>;
}

/** 条目 → 用户友好摘要。按数据形状分族，识别不到就只给「更多信息」。 */
export function itemDigest(item: DigestInput): ItemDigest {
  const extra = item.extra ?? {};
  const hit = journeyDigest(item) ?? amapDigest(item) ?? hotelDigest(item) ?? infoDigest(item);
  const used = new Set([...HIDDEN_KEYS, ...(hit?.used ?? [])]);

  const more: Array<[string, string]> = [];
  const push = (key: string, value: unknown) => {
    if (used.has(key) || more.length >= 8) return;
    const text = scalar(value);
    if (!text) return;
    more.push([LABELS[key] ?? key, text]);
  };
  for (const [key, value] of Object.entries(extra)) push(key, value);
  // 行程族的深层字段（出发/到达城市等）补进「更多信息」，便于逐条复核
  const journey = asRecord(asArray(extra.journeys)[0]);
  const segment = asRecord(asArray(journey.segments)[0]);
  if (Object.keys(segment).length > 0) {
    for (const key of ["depCityName", "arrCityName", "depStationName", "arrStationName", "depDateTime", "arrDateTime", "seatClassName", "duration"]) {
      push(key, segment[key]);
    }
  }

  return { title: hit?.title ?? "", summary: hit?.summary ?? "", more };
}
