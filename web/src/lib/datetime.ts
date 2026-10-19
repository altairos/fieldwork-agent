/**
 * 日期/时间戳的用户友好格式化（右栏面板等展示后端 ISO 时间戳处共用）。
 *
 * 后端 generated_at / priced_at 等为朴素 ISO 串（如 `2026-10-18T21:42:08.010792`，
 * 无时区后缀、小数秒可达 6 位）。直接 new Date() 解析存在时区归属与小数秒精度歧义，
 * 故优先用正则取日期段，避开 Date 解析；取不到再回落 Date.parse，仍失败则原样返回
 * （绝不臆造、不静默吞成空）。
 */

/** ISO 时间戳 → 用户友好的「YYYY年M月D日」（仅日期，按产品要求不显示时分秒）。
 *  空串返回空串；无法识别时原样返回入参，由调用处决定是否展示。 */
export function formatDate(iso: string): string {
  if (!iso) return "";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}年${Number(m[2])}月${Number(m[3])}日`;
  const t = Date.parse(iso);
  if (!Number.isNaN(t)) {
    const d = new Date(t);
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
  }
  return iso;
}
