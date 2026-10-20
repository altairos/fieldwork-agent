import { describe, expect, test } from "vitest";
import { itemDigest } from "../src/components/cards/fields";

/**
 * 搜索条目摘要层（用户反馈：结果仍以英文字段名呈现，且外露佣金等内部字段）。
 * 断言三件事：语义化顺序、内部字段不外露、数据不丢（进 more 折叠）。
 */

describe("itemDigest 语义化", () => {
  test("住宿：档次 · 周边地标 · 地址；佣金/商户 id/坐标不外露", () => {
    const d = itemDigest({
      name: "美姑瑞祥宾馆",
      extra: {
        address: "美中路77号",
        star: "经济型",
        interestsPoi: "近美姑县人民政府",
        commissionMoneyYuan: "0.00",
        shId: "74910068",
        rate: "0",
        latitude: "28.327618",
        longitude: "103.13259",
        decorationTime: "2023-01-01",
      },
    });
    expect(d.summary).toBe("经济型 · 近美姑县人民政府 · 美中路77号");
    const all = JSON.stringify(d);
    for (const hidden of ["commissionMoneyYuan", "74910068", "28.327618", "0.00"]) {
      expect(all).not.toContain(hidden);
    }
    // 装修时间这类中性字段保留在「更多原始字段」里（数据不丢）
    expect(d.more.map(([k]) => k)).toContain("装修");
  });

  test("高德地点：类型取末级（去掉「;」分级）· 地址 · 距离", () => {
    const d = itemDigest({
      name: "美姑县觉洛乡中心卫生院",
      extra: {
        type: "医疗保健服务;综合医院;卫生院",
        address: "觉洛乡",
        distance: "232",
        location: "103.017809,28.109991",
        typecode: "090102",
      },
    });
    expect(d.summary).toBe("卫生院 · 觉洛乡 · 232 米");
    expect(JSON.stringify(d)).not.toContain("090102");
  });

  test("火车：车次作标题，站点时间/时长/席别成一行（不再「未命名条目 · totalDuration」）", () => {
    const d = itemDigest({
      name: "",
      extra: {
        journeys: [{
          journeyType: "直达",
          totalDuration: "224",
          segments: [{
            depStationName: "昆明站",
            arrStationName: "西昌西站",
            depDateTime: "2026-11-15 14:16:00",
            arrDateTime: "2026-11-15 18:00:00",
            duration: "224",
            marketingTransportName: "动车",
            marketingTransportNo: "D236",
            seatClassName: "二等座",
          }],
        }],
        totalDuration: "224",
      },
    });
    expect(d.title).toBe("D236 动车");
    expect(d.summary).toBe("昆明站 14:16 → 西昌西站 18:00 · 3 小时 44 分 · 直达 · 二等座");
    const all = JSON.stringify(d);
    expect(all).not.toContain("totalDuration");
    expect(all).not.toContain("journeys");
  });

  test("keyword 条目（info 嵌套）：星级/标签/评分可读", () => {
    const d = itemDigest({
      name: "云海大酒店",
      extra: { info: { star: "2", tags: null, scoreDesc: "4.6", pk: "hotel_83231031" } },
    });
    expect(d.summary).toBe("2 星 · 评分 4.6");
    expect(JSON.stringify(d)).not.toContain("hotel_83231031");
  });

  test("识别不到形状时不回落到原始键值堆砌", () => {
    const d = itemDigest({ name: "某条目", extra: { foo: "bar", baz: 1 } });
    expect(d.summary).toBe("");
    expect(d.more).toEqual([["foo", "bar"], ["baz", "1"]]);
  });
});
