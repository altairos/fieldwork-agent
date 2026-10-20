import { useEffect, useRef, useState } from "react";
import { rest } from "../../api/rest";
import { Chip } from "../ui/Chip";

/**
 * AmapMapPanel — 高德 JS API 地图面板（N6，PoC2 §5.2）。
 *
 * 设计约束：
 * - loader 动态 import（@amap/amap-jsapi-loader）：地图只在首个面板需要时才拉
 *   JS API 脚本，不拖累首屏；jsdom 无 WebGL，测试全程 vi.mock loader。
 * - JS Key 经 GET /settings/status 下发（settings.py 文档化例外：JS API 2.0
 *   要求前端持有 key + securityJsCode，防滥用靠控制台域名白名单）。
 * - 降级链（不白屏、信息不丢）：无 Key → 坐标 + 点位清单文字卡；loader 失败 →
 *   同款文字卡；geo 缺段（无 center）→ 文字提示，不发 settings 请求。
 * - settings 请求模块级缓存：页面上多块地图面板只查一次。
 */

/** 面板点位（SiteProfileCard 设施点 / ItineraryCard 接驳枢纽与田野点）。 */
export interface MapMarker {
  location: string; // "lng,lat"（高德 geocode/POI 坐标串，原样透传）
  title: string;
  kind?: string; // 内核附着的 facility_kind（五类设施色分预留）
}

type AmapLoaderState = "loading" | "ready" | "unavailable" | "error";

/** 高德命名空间的最小结构面（Map/Marker/Polyline 构造；loader.load 返回 any）。 */
interface AMapNS {
  Map: new (
    el: HTMLElement,
    opts: Record<string, unknown>,
  ) => { add?: (overlay: unknown) => void; destroy?: () => void };
  Marker: new (opts: Record<string, unknown>) => unknown;
  Polyline: new (opts: Record<string, unknown>) => unknown;
}

interface JsKeyConf {
  key: string;
  security: string;
}

let keyCache: Promise<JsKeyConf> | null = null;

/** JS Key 只查一次（多面板共享）；请求失败回空 → 面板降级 unavailable。 */
function loadJsKeyConf(): Promise<JsKeyConf> {
  if (!keyCache) {
    keyCache = rest
      .settingsStatus()
      .then((s) => ({ key: s.amap_js_key ?? "", security: s.amap_js_security ?? "" }))
      .catch(() => ({ key: "", security: "" }));
  }
  return keyCache;
}

/** "lng,lat" → [lng, lat]；不合法（缺段/非数字）返回 null，调用方各自跳过。 */
function parseLngLat(s: string): [number, number] | null {
  const m = /^(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)$/.exec(s.trim());
  return m ? [Number(m[1]), Number(m[2])] : null;
}

/**
 * useAmapLoader — JS Key 获取 + loader 加载的状态机。
 * unavailable（服务端未配 Key）与 error（Key 在位但 loader 失败）分开：
 * 前者是配置事实，后者是网络/Key 无效，提示文案不同。
 */
export function useAmapLoader(): {
  state: AmapLoaderState;
  key: string;
  security: string;
  amap: AMapNS | null;
} {
  const [state, setState] = useState<AmapLoaderState>("loading");
  const [key, setKey] = useState("");
  const [security, setSecurity] = useState("");
  const [amap, setAmap] = useState<AMapNS | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const conf = await loadJsKeyConf();
      if (cancelled) return;
      setKey(conf.key);
      setSecurity(conf.security);
      if (!conf.key) {
        setState("unavailable");
        return;
      }
      try {
        // 包内 d.ts 停留在 1.x 形态（version 必填、无 securityJsCode）：
        // JS API 2.0 运行时支持 securityJsCode、version 可缺省，按实际签名窄化
        const loader = (await import("@amap/amap-jsapi-loader")).default as {
          load: (opts: Record<string, unknown>) => Promise<unknown>;
        };
        const ns = (await loader.load(
          conf.security
            ? { key: conf.key, securityJsCode: conf.security }
            : { key: conf.key },
        )) as AMapNS;
        if (cancelled) return;
        setAmap(ns);
        setState("ready");
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { state, key, security, amap };
}

/** 降级文字卡：坐标 + 点位清单照常可读（红线：信息不因地图缺席而丢失）。
 * `showMarkerChips=false` 供已在卡内自列点位清单的调用方（如田野点画像的
 * 按设施类型分组清单）避免同一批名称渲染两遍。
 */
function DegradedCard({
  reason,
  center,
  markers,
  showMarkerChips = true,
}: {
  reason: string;
  center: string;
  markers: MapMarker[];
  showMarkerChips?: boolean;
}) {
  return (
    <div className="rounded-lg bg-cardsunk hairline p-3 space-y-2">
      <div className="text-[11.5px] text-inksoft">{reason}，以下坐标与点位供参考。</div>
      <div className="num text-[11px] text-inkfaint">{center}</div>
      {showMarkerChips && markers.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {markers.map((mk, i) => (
            <Chip key={`${mk.title}-${i}`} tone="neutral" className={mk.kind ? "" : ""}>
              {mk.title}
            </Chip>
          ))}
        </div>
      )}
    </div>
  );
}

export function AmapMapPanel({
  center,
  markers = [],
  line = null,
  height = 160,
  showMarkerChips = true,
}: {
  center?: string; // 画像点 / 接驳枢纽坐标；缺（geo 缺段）→ 纯文字提示
  markers?: MapMarker[];
  line?: [string, string] | null; // 接驳示意：枢纽 → 田野点两点直线
  height?: number;
  /** 降级态是否列点位名（调用方已自列清单时传 false，避免同一批名称重复渲染） */
  showMarkerChips?: boolean;
}) {
  // 无 center（geo 缺段）：不发 settings 请求、不进 loader（测试钉死 loadMock 不被调）
  if (!center) {
    return (
      <div className="rounded-lg bg-cardsunk hairline p-3 text-[11.5px] text-inkfaint">
        暂无定位坐标（该点位未取得高德 geocode，无法示意）
      </div>
    );
  }
  return (
    <AmapMapStage
      center={center}
      markers={markers}
      line={line}
      height={height}
      showMarkerChips={showMarkerChips}
    />
  );
}

function AmapMapStage({
  center,
  markers,
  line,
  height,
  showMarkerChips,
}: {
  center: string;
  markers: MapMarker[];
  line: [string, string] | null;
  height: number;
  showMarkerChips: boolean;
}) {
  const { state, amap } = useAmapLoader();
  const holder = useRef<HTMLDivElement | null>(null);
  // markers/line 每渲染新引用：ref 同步最新值，effect 只随加载状态重建地图
  const propsRef = useRef({ markers, line });
  propsRef.current = { markers, line };

  useEffect(() => {
    if (state !== "ready" || !amap || !holder.current) return;
    const c = parseLngLat(center);
    if (!c) return;
    const { markers: mks, line: ln } = propsRef.current;
    const map = new amap.Map(holder.current, { center: c, zoom: 13 });
    for (const mk of mks) {
      const pos = parseLngLat(mk.location);
      if (!pos) continue;
      map.add?.(new amap.Marker({ position: pos, title: mk.title }));
    }
    if (ln) {
      const a = parseLngLat(ln[0]);
      const b = parseLngLat(ln[1]);
      if (a && b) {
        map.add?.(
          new amap.Polyline({ path: [a, b], strokeColor: "#e8590c", strokeWeight: 3 }),
        );
      }
    }
    return () => {
      map.destroy?.();
    };
  }, [state, amap, center]);

  if (state === "unavailable" || state === "error") {
    return (
      <DegradedCard
        reason={state === "unavailable" ? "未配置高德 JS API Key" : "地图加载失败"}
        center={center}
        markers={markers}
        showMarkerChips={showMarkerChips}
      />
    );
  }
  // loading（占位防跳动）与 ready 共用容器：ready 后 effect 挂图
  return (
    <div
      ref={holder}
      className="rounded-lg hairline overflow-hidden bg-cardsunk"
      style={{ height }}
    />
  );
}
