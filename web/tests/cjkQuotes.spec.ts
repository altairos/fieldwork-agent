import { normalizeCjkQuotes } from "../src/lib/cjkQuotes";

/**
 * 中文全角引号归一（问题3前端）：规则与后端 loop.py `_normalize_cjk_quotes`
 * 严格对齐——成对、同行、无嵌套的半角双引号，内容含 CJK 才转全角“”；
 * 纯西文内容保持半角；代码块（``` 围栏内）不动。
 * 渲染级覆盖见 chat.spec.tsx「全角引号归一」describe。
 */
describe("normalizeCjkQuotes", () => {
  test("中文内容成对转全角", () => {
    expect(normalizeCjkQuotes('他说的"银都"作坊集群')).toBe("他说的“银都”作坊集群");
  });
  test("西文内容保持半角", () => {
    expect(normalizeCjkQuotes('译自 "Silver Capital" 的说法')).toBe('译自 "Silver Capital" 的说法');
  });
  test("西文夹中文（引号内含 CJK 即转，与后端口径一致）", () => {
    expect(normalizeCjkQuotes('"银都 Silver Capital"的直译')).toBe("“银都 Silver Capital”的直译");
    expect(normalizeCjkQuotes('"Silver Capital"的直译')).toBe('"Silver Capital"的直译');
  });
  test("跨行不成对：原样", () => {
    expect(normalizeCjkQuotes('上句的"引号\n没有闭合')).toBe('上句的"引号\n没有闭合');
  });
  test("多个引号对逐对判定", () => {
    expect(normalizeCjkQuotes('"银都"与 "hub" 并列')).toBe("“银都”与 \"hub\" 并列");
  });
  test("代码块内不动，块外照常", () => {
    expect(normalizeCjkQuotes('```\nconst s = "银都"\n```\n这就是"银都"'))
      .toBe('```\nconst s = "银都"\n```\n这就是“银都”');
  });
});
