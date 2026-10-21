import { beforeEach, describe, expect, test } from "vitest";
import type { SseEvent } from "../src/types/events";
import { useToolsStore } from "../src/stores/tools";
import { useConversationStore } from "../src/stores/conversation";
import { useProjectStore } from "../src/stores/project";
import { useUiStore } from "../src/stores/ui";

const ev = (e: SseEvent) => e;

beforeEach(() => {
  useToolsStore.getState().reset();
  useConversationStore.getState().reset();
  useProjectStore.getState().reset();
  useUiStore.getState().reset();
});

describe("tools store 幂等", () => {
  test("重复 tool_completed 同 call_id 只留一条(覆盖非追加)", () => {
    const s = useToolsStore.getState();
    s.applyEvent(ev({ event: "tool_started", call_id: "c1", tool: "search_hotels", args: { dest_name: "大理" }, purpose: "住宿" }));
    s.applyEvent(ev({ event: "tool_completed", call_id: "c1", ok: true, data: { items: [{ name: "A", price: 180 }] } }));
    s.applyEvent(ev({ event: "tool_completed", call_id: "c1", ok: true, data: { items: [{ name: "A", price: 180 }] } })); // 重复(重连补发)
    const after = useToolsStore.getState();
    expect(Object.keys(after.byCallId)).toEqual(["c1"]);
    expect(after.byCallId["c1"].status).toBe("done");
    expect(after.byCallId["c1"].tool).toBe("search_hotels");
  });
  test("tool_started→running, tool_completed(ok=false)→failed", () => {
    const s = useToolsStore.getState();
    s.applyEvent(ev({ event: "tool_started", call_id: "c2", tool: "search_flights", args: {}, purpose: "" }));
    expect(useToolsStore.getState().byCallId["c2"].status).toBe("running");
    s.applyEvent(ev({ event: "tool_completed", call_id: "c2", ok: false, data: null, degraded: "flyai_unavailable" }));
    const t = useToolsStore.getState().byCallId["c2"];
    expect(t.status).toBe("failed");
    expect(t.degraded).toBe("flyai_unavailable");
  });
  test("tool_completed 携带 error 三元组（配额提示数据源）", () => {
    const s = useToolsStore.getState();
    s.applyEvent(ev({ event: "tool_started", call_id: "c3", tool: "search_pois", args: {}, purpose: "" }));
    s.applyEvent(ev({ event: "tool_completed", call_id: "c3", ok: false, data: null,
      error: { code: "flyai_rate_limited", message: "rate limited", hint: "配额说明" } }));
    const t = useToolsStore.getState().byCallId["c3"];
    expect(t.error?.code).toBe("flyai_rate_limited");
    expect(t.error?.hint).toBe("配额说明");
  });
  test("成功 completed 的 error 归约为 null", () => {
    const s = useToolsStore.getState();
    s.applyEvent(ev({ event: "tool_started", call_id: "c4", tool: "search_hotels", args: {}, purpose: "" }));
    s.applyEvent(ev({ event: "tool_completed", call_id: "c4", ok: true, data: { items: [] } }));
    expect(useToolsStore.getState().byCallId["c4"].error).toBeNull();
  });
});

describe("conversation store 流式拼接", () => {
  test("P2-7：assistant_message_started 立刻置 turnRunning，不等首个 delta", () => {
    const s = useConversationStore.getState();
    s.pushUser("帮我看看");
    expect(useConversationStore.getState().turnRunning).toBe(false);

    s.applyEvent(ev({ event: "assistant_message_started" }));
    const after = useConversationStore.getState();
    expect(after.turnRunning).toBe(true);      // 打字光标从此刻生效
    expect(after.streamBuffer).toBe("");      // 尚未有正文，不伪造内容
    expect(after.messages.at(-1)?.role).toBe("user");
  });

  test("message_delta×3 拼接 + turn_done 定格", () => {
    const s = useConversationStore.getState();
    s.applyEvent(ev({ event: "message_delta", text: "正在" }));
    s.applyEvent(ev({ event: "message_delta", text: "搜索" }));
    s.applyEvent(ev({ event: "message_delta", text: "大理" }));
    expect(useConversationStore.getState().streamBuffer).toBe("正在搜索大理");
    expect(useConversationStore.getState().turnRunning).toBe(true);
    s.applyEvent(ev({ event: "turn_done", reply_id: "t1", usage: { total_tokens: 42 } }));
    const after = useConversationStore.getState();
    expect(after.turnRunning).toBe(false);
    expect(after.messages.at(-1)?.content).toBe("正在搜索大理");
    expect(after.messages.at(-1)?.role).toBe("assistant");
    expect(after.lastUsage).toEqual({ total_tokens: 42 });
  });
});

describe("conversation store error 终态（N14：残缺文字的保存/展示规则）", () => {
  test("error 事件复位 turnRunning + 丢弃残缺 streamBuffer，user 消息保留", () => {
    const s = useConversationStore.getState();
    s.pushUser("帮我排行程");
    s.beginTurn();
    s.applyEvent(ev({ event: "message_delta", text: "正在核" }));   // 半截回复
    expect(useConversationStore.getState().turnRunning).toBe(true);
    s.applyEvent(ev({ event: "error", code: "internal_error", message: "上游超时", hint: "请重试" }));
    const after = useConversationStore.getState();
    expect(after.turnRunning).toBe(false);            // 打字光标停止、发送按钮解禁
    expect(after.streamBuffer).toBe("");              // 残缺文字丢弃（对齐后端：出错回合半截回复不入史）
    expect(after.messages).toEqual([{ role: "user", content: "帮我排行程" }]);  // user 已被接受，保留
  });
  test("error 前无 delta（空 buffer）同样复位运行态，幂等不抛错", () => {
    useConversationStore.getState().beginTurn();
    useConversationStore.getState().applyEvent(ev({ event: "error", code: "internal_error", message: "boom", hint: "" }));
    expect(useConversationStore.getState().turnRunning).toBe(false);
    expect(useConversationStore.getState().streamBuffer).toBe("");
  });
});

describe("conversation store failTurn（回合创建被拒时回滚乐观 UI）", () => {
  test("弹出刚 push 的 user 消息 + turnRunning=false + 清 buffer（光标不再永久脉冲）", () => {
    useConversationStore.getState().pushUser("帮我排行程");
    useConversationStore.getState().beginTurn();
    expect(useConversationStore.getState().turnRunning).toBe(true);
    useConversationStore.getState().failTurn();
    const after = useConversationStore.getState();
    expect(after.messages).toEqual([]);          // 被拒消息不留痕
    expect(after.turnRunning).toBe(false);
    expect(after.streamBuffer).toBe("");
  });
  test("只弹末条 user，历史轮次原样保留", () => {
    useConversationStore.setState({
      messages: [{ role: "user", content: "上一轮" }, { role: "assistant", content: "上一轮回复" }],
      streamBuffer: "", turnRunning: false, lastUsage: {},
    });
    useConversationStore.getState().pushUser("这一轮");
    useConversationStore.getState().beginTurn();
    useConversationStore.getState().failTurn();
    expect(useConversationStore.getState().messages.map((m) => m.content)).toEqual(["上一轮", "上一轮回复"]);
  });
  test("末条是 assistant（消息已被后端接受）时不误弹，仅复位运行态", () => {
    useConversationStore.setState({
      messages: [{ role: "user", content: "问" }, { role: "assistant", content: "答" }],
      streamBuffer: "", turnRunning: true, lastUsage: {},
    });
    useConversationStore.getState().failTurn();
    expect(useConversationStore.getState().messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(useConversationStore.getState().turnRunning).toBe(false);
  });
  test("半截流式缓冲一并清掉（不留残字）", () => {
    useConversationStore.getState().pushUser("问");
    useConversationStore.getState().applyEvent(ev({ event: "message_delta", text: "半截" }));
    useConversationStore.getState().failTurn();
    expect(useConversationStore.getState().streamBuffer).toBe("");
  });
  test("空会话调用幂等（不抛错、不造消息）", () => {
    useConversationStore.getState().failTurn();
    expect(useConversationStore.getState().messages).toEqual([]);
    expect(useConversationStore.getState().turnRunning).toBe(false);
  });
});

describe("conversation store hydrate（刷新回填）", () => {
  test("hydrate 覆盖 messages 且不动流式状态", () => {
    useConversationStore.getState().hydrate([
      { role: "user", content: "你好" },
      { role: "assistant", content: "收到" },
    ]);
    const s = useConversationStore.getState();
    expect(s.messages).toEqual([
      { role: "user", content: "你好" },
      { role: "assistant", content: "收到" },
    ]);
    expect(s.turnRunning).toBe(false);
    expect(s.streamBuffer).toBe("");
  });
  test("reset 清空回填史（跨项目不串台）", () => {
    useConversationStore.getState().hydrate([{ role: "user", content: "x" }]);
    useConversationStore.getState().reset();
    expect(useConversationStore.getState().messages).toEqual([]);
  });
});

describe("conversation store quickReplyGroups 状态机（多组快捷作答）", () => {
  test("turn_done（多组）：正文剥离 [[options]] 入史，各组进 state 与末条消息", () => {
    const s = useConversationStore.getState();
    s.applyEvent(ev({ event: "message_delta", text: "先定两件事：[[options:用途]]开题|申报[[/options]][[options:学科]]人类学|社会学[[/options]]" }));
    s.applyEvent(ev({ event: "turn_done", reply_id: "t1", usage: {}, quick_reply_groups: [
      { label: "用途", options: ["开题", "申报"] },
      { label: "学科", options: ["人类学", "社会学"] },
    ] }));
    const after = useConversationStore.getState();
    expect(after.messages.at(-1)?.content).toBe("先定两件事："); // 与后端入史口径一致
    expect(after.messages.at(-1)?.quickReplyGroups).toEqual([
      { label: "用途", options: ["开题", "申报"] },
      { label: "学科", options: ["人类学", "社会学"] },
    ]);
    expect(after.quickReplyGroups).toHaveLength(2);            // Composer 据此渲染多行 chips
  });
  test("turn_done（旧单组 quick_replies）：向后兼容包成单个无标签组", () => {
    const s = useConversationStore.getState();
    s.applyEvent(ev({ event: "message_delta", text: "先校准研究问题吗？[[options]]是|否[[/options]]" }));
    s.applyEvent(ev({ event: "turn_done", reply_id: "t1", usage: {}, quick_replies: ["是", "否"] }));
    const after = useConversationStore.getState();
    expect(after.messages.at(-1)?.content).toBe("先校准研究问题吗？");
    expect(after.quickReplyGroups).toEqual([{ label: "", options: ["是", "否"] }]);
  });
  test("turn_done 无选项（渐进增强基准）：quickReplyGroups 归空、正文原样", () => {
    const s = useConversationStore.getState();
    s.applyEvent(ev({ event: "message_delta", text: "好的" }));
    s.applyEvent(ev({ event: "turn_done", reply_id: "t1", usage: {} }));
    const after = useConversationStore.getState();
    expect(after.messages.at(-1)?.content).toBe("好的");
    expect(after.messages.at(-1)?.quickReplyGroups).toBeUndefined();     // 无值不加键
    expect(after.quickReplyGroups).toEqual([]);
  });
  test("pushUser 清空（用户已作答，按钮退场）", () => {
    useConversationStore.setState({ quickReplyGroups: [{ label: "", options: ["是", "否"] }] });
    useConversationStore.getState().pushUser("是");
    expect(useConversationStore.getState().quickReplyGroups).toEqual([]);
  });
  test("hydrate（多组）：末条 assistant 带 quick_reply_groups 恢复待作答", () => {
    useConversationStore.getState().hydrate([
      { role: "user", content: "问" },
      { role: "assistant", content: "答", quick_reply_groups: [
        { label: "用途", options: ["开题", "申报"] },
        { label: "学科", options: ["人类学", "社会学"] },
      ] },
    ]);
    const s = useConversationStore.getState();
    expect(s.quickReplyGroups).toHaveLength(2);
    expect(s.messages.at(-1)?.quickReplyGroups).toEqual([
      { label: "用途", options: ["开题", "申报"] },
      { label: "学科", options: ["人类学", "社会学"] },
    ]);
  });
  test("hydrate（旧史 quick_replies）：扁平列表包成单个无标签组", () => {
    useConversationStore.getState().hydrate([
      { role: "user", content: "问" },
      { role: "assistant", content: "答", quick_replies: ["甲", "乙"] },
    ]);
    const s = useConversationStore.getState();
    expect(s.quickReplyGroups).toEqual([{ label: "", options: ["甲", "乙"] }]);
    expect(s.messages.at(-1)?.quickReplyGroups).toEqual([{ label: "", options: ["甲", "乙"] }]);
  });
  test("hydrate：末条 assistant 无选项或末条是 user 时不恢复", () => {
    useConversationStore.getState().hydrate([
      { role: "user", content: "问" },
      { role: "assistant", content: "答" },
    ]);
    expect(useConversationStore.getState().quickReplyGroups).toEqual([]);
    useConversationStore.getState().hydrate([
      { role: "assistant", content: "答" },
      { role: "user", content: "追问" },
    ]);
    expect(useConversationStore.getState().quickReplyGroups).toEqual([]);
  });
  test("reset 清空（跨项目不残留上一项目的待作答选项）", () => {
    useConversationStore.setState({ quickReplyGroups: [{ label: "", options: ["甲"] }] });
    useConversationStore.getState().reset();
    expect(useConversationStore.getState().quickReplyGroups).toEqual([]);
  });
});

describe("conversation store turn_done 查重（hydrate×回放双写保护，轮 4 首页首问）", () => {
  test("hydrate 完整史已含同文 assistant：回放 turn_done 不重复 append", () => {
    useConversationStore.getState().hydrate([
      { role: "user", content: "研究宗族" },
      { role: "assistant", content: "你好，田野罗盘" },
    ]);
    useConversationStore.getState().applyEvent(ev({ event: "message_delta", text: "你好，" }));
    useConversationStore.getState().applyEvent(ev({ event: "message_delta", text: "田野罗盘" }));
    useConversationStore.getState().applyEvent(ev({ event: "turn_done", reply_id: "t1", usage: {} }));
    const after = useConversationStore.getState();
    // adopt 订阅回放与 hydrate 并发：末条同文 assistant 不再追加第二条
    expect(after.messages).toEqual([
      { role: "user", content: "研究宗族" },
      { role: "assistant", content: "你好，田野罗盘" },
    ]);
    expect(after.streamBuffer).toBe("");
    expect(after.turnRunning).toBe(false);
  });
  test("末条不同文（正常流式回合）照常 append", () => {
    useConversationStore.getState().pushUser("问");
    useConversationStore.getState().applyEvent(ev({ event: "message_delta", text: "答" }));
    useConversationStore.getState().applyEvent(ev({ event: "turn_done", reply_id: "t1", usage: {} }));
    expect(useConversationStore.getState().messages).toEqual([
      { role: "user", content: "问" },
      { role: "assistant", content: "答" },
    ]);
  });
});

describe("project + ui 阶段推进(右栏自动切面板)", () => {
  test("phase_transition 改 phase，右栏同步切到该阶段面板", () => {
    useUiStore.getState().setActiveTab("basic-info");
    useProjectStore.getState().applyEvent(ev({ event: "phase_transition", from: "research_positioning", to: "field_site_profiles" }));
    useUiStore.getState().applyEvent(ev({ event: "phase_transition", from: "research_positioning", to: "field_site_profiles" }));
    expect(useProjectStore.getState().phase).toBe("field_site_profiles");
    // 2026-10-20 用户反馈：不再弹建议让用户手点「查看」，直接切到该阶段对应面板
    expect(useUiStore.getState().activeTab).toBe("sites");
  });
  test("error 事件置 degradedBanner，不中断已有内容", () => {
    useUiStore.getState().applyEvent(ev({ event: "error", code: "internal_error", message: "boom", hint: "重试" }));
    expect(useUiStore.getState().degradedBanner?.code).toBe("internal_error");
  });
});

describe("全序列集成", () => {
  test("[tool_started,tool_completed,tool_completed重复,delta×3,turn_done] 后各 store 一致", () => {
    const seq: SseEvent[] = [
      { event: "tool_started", call_id: "c1", tool: "search_hotels", args: {}, purpose: "" },
      { event: "tool_completed", call_id: "c1", ok: true, data: { items: [] } },
      { event: "tool_completed", call_id: "c1", ok: true, data: { items: [] } },
      { event: "message_delta", text: "a" },
      { event: "message_delta", text: "b" },
      { event: "message_delta", text: "c" },
      { event: "turn_done", reply_id: "t1", usage: {} },
    ];
    for (const e of seq) {
      useToolsStore.getState().applyEvent(e);
      useConversationStore.getState().applyEvent(e);
      useProjectStore.getState().applyEvent(e);
      useUiStore.getState().applyEvent(e);
    }
    expect(Object.keys(useToolsStore.getState().byCallId)).toEqual(["c1"]);
    expect(useConversationStore.getState().messages.at(-1)?.content).toBe("abc");
    expect(useConversationStore.getState().turnRunning).toBe(false);
  });
});
