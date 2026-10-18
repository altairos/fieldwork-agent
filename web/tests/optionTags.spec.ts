import { stripOptionTags } from "../src/lib/optionTags";

/**
 * [[options]] 标记剥离（B 节问题 4）：剥离语义与后端 loop.py `_extract_quick_replies`
 * 严格对齐——标记前的正文保留并去尾部空白，标记及其后内容丢弃（协议约定标记在末尾）。
 * 前端侧服务于 AssistantStream（流式半截标记防闪烁）与 MessageBubble（历史兜底）。
 */
describe("stripOptionTags", () => {
  test("完整标记：删至闭合，正文保留并去尾部空白", () => {
    expect(
      stripOptionTags("你想先校准哪一项？\n\n[[options]]研究问题|田野地点|时间窗[[/options]]"),
    ).toBe("你想先校准哪一项？");
  });
  test("半截标记（流式期间只有起始）：截断起始处", () => {
    expect(stripOptionTags("正在提问[[options]]研究问题|田")).toBe("正在提问");
  });
  test("无标记：原样返回", () => {
    expect(stripOptionTags("普通回复，开放式提问不用标记")).toBe("普通回复，开放式提问不用标记");
  });
  test("正文尾部空白一并清理（与后端 rstrip 口径一致）", () => {
    expect(stripOptionTags("正文 \n [[options]]甲|乙[[/options]]")).toBe("正文");
  });
  test("标记居中（模型违约）：与后端口径一致——标记及之后全部丢弃", () => {
    expect(stripOptionTags("前半[[options]]甲|乙[[/options]]后半")).toBe("前半");
  });
});
