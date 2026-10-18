# 田野罗盘 高德地图接入 PoC2 设计（行程整合 + 地图面板 + 工程加固）

版本：v1.0（PoC2 设计稿）
日期：2026-10-18
适用仓库：Fieldwork_Agent
前置：PoC1（`docs/plans/2026-10-18-amap-poc-design.md`）M1–M4 已全绿交付（六原语 + 画像 geo/facilities + `plan_last_mile`）。
交付范围：三个方向的模块设计、集成点、测试与验收方案；本文不代表相关能力已经实现。

## 1. PoC2 目标与非目标

### 目标（用户已确认三方向）

1. **A 行程级接驳整合**：`compose_itinerary` 吸收 `plan_last_mile` 数据探针成果——行程腿携带末端接驳选项、公交票价进预算条目（`date_hint` 班次日期透传一并落地）。
2. **B Web 地图面板**：高德 JS API 前端可视化——画像坐标/设施标点、接驳两点连线；Key 经后端配置下发，无 Key 优雅降级。
3. **C 工程加固包**：`AMapClient` 实例级 TTL 读缓存（省配额）、`x-amap-key` 请求级多租户（MCP Header + Web 回合级）、amap 结果进 `search_cache` 快照（live ref 平移）、双源路由显式标注。

### 非目标（PoC2 后另行立项）

- 地图生成 / 导航 / 打车唤端（高德 2025-05 新能力，接口形态仍需调研）；
- `AMapMcpClient` 宿主层 MCP Client 替换实现（`mcp` SDK 依赖，PoC1 §2 已裁决搁置）；
- 海外/国内双源路由引擎（显式策略配置、自动重试编排——PoC2 只做"标注显式化"）；
- 缓存跨进程/跨项目共享（实例级内存缓存，不落盘共享；配额监控面板）；
- 公交折线逐段画线（面板只画 hub/site 两点连线，折线属 PoC3）。

## 2. 前置修补 N0：web 回合 dispatch 补 `amap_client` 通道

**现状**：`web_host/agent/dispatch.py` 的 `build_dispatch_fn(flyai_client, llm)` 只注入 `client`/`llm`；CLI（`core/orchestrator.py`，三通道）与 MCP（`mcp_server/server.py` 组装期 `amap_client` 单例）均已分道注入。amap 能力在 Web 会话走 fn 自建 `AMapClient()` 默认路径——服务器级 Key 可用，但**没有回合级替换挂点**（C3 的前置）。

**修补**：签名扩为 `build_dispatch_fn(flyai_client, llm, amap_client=None)`；注入分支对照 orchestrator——`if "amap_client" in params: kwargs["amap_client"] = amap_client`（None 时不下发，fn 自建语义不变）。`web_host/agent/loop.py` 组装处从 `app.state.amap`（`deps.py` 增挂，对照 `flyai`）取实例。钉子：`test_web_agent_loop` 增"amap 工具收到注入实例"用例（对照 `test_orchestrator_dispatch_*` 惯例）。

## 3. 方向 A：行程级接驳整合

### 3.1 `plan_last_mile` 补 `date_hint` 透传（v3 `date` 班次日期）

- v3 `direction/transit/integrated` 的 `date` 参数格式 `YYYY-MM-DD`，**仅支持 3 天内（含当日）**——行程规划常为未来数周，透传必须带边界防护；
- `date_hint` 非空时 `strptime` 解析：非法格式 → 忽略 + `degraded` 附言（对照 itinerary 日期铁律：不拼串、不崩）；解析成功但超出 3 天窗口 → 不透传 + `degraded` 附言"高德班次日期仅支持 3 天内，已按当日/实时班次查询"；
- `AMapClient.direction_transit` 增 `date: str = ""` 形参，空串不进 query 参数（现有调用零影响）；`PlanLastMileArgs.date_hint` description 同步改写（不再是"占位"）。

### 3.2 `compose_itinerary` 接驳腿模型与预算联动

**入参**：签名追加 keyword-only `amap_client: AMapClient | None = None`（与 `client` 对称，不进 schema）。无 Key 部署下首次 amap 调用返回 `amap_invalid_key`（不发请求，零成本）→ 全程短路停用接驳查询，不影响行程主链路。

**接驳查询范围**：对每条行程腿的**到达端**执行"前一城市 → 到达点"的 transit 接驳（`departure→dest₁`、`dest₁→dest₂`、…、`destₙ→departure` 返程同理）。每腿至多 2 次 geocode + 1 次 transit（到达端点若与前一站同城，geocode 结果可复用缓存——与 §5.1 实例缓存天然协同）。

**数据形状**（`ItineraryPlan.legs[i]` 为 dict，加键向后兼容）：

```
leg["last_mile"] = {
    "hub_geo": GeoLocation | None,      # 前一城市坐标
    "site_geo": GeoLocation | None,     # 到达点坐标
    "options": ItemPage | None,         # transit 方案页（price=cost 元，cheapest 已提取）
    "note": str,                        # 降级说明（无 Key / 未收录 / 无方案）
}
```

**预算联动**（R2 口径扩展）：`options.cheapest` 非空 → 预算条目 `接驳（昆明→喜洲）`，amount = cheapest × researcher_count，`source="cheapest"`（语义与既有交通条目一致：取最低价），`unit` 标注"高德公交最低价×人数"；cheapest 缺失 → 不计条目 + `uncovered` 列名（coverage 变 partial，口径不变）。

**失败隔离**（对照单腿无价惯例）：任一端 geocode 失败 / transit 无方案 / amap 异常 → `last_mile={"note": ...}` 其余键 None，预算不计，整体 `ok=True` 不变；amap 相关异常绝不逃逸出聚合层。

**`inputs_hash` 不变**：接驳结果属于"增强数据"而非方案输入（与 alternatives 同层），改动会让历史方案 hash 失配——明确排除。

### 3.3 ItineraryCard 前端接驳行

行程卡腿展开区增"末端接驳"行：有 options 时显示"公交 × N 段 / 最低价 ¥x / 约 y 分钟"（`ItemPage.cheapest` + `schedule.duration_minutes`），无则显示 note 灰字；样式对照 D11 行程卡既有行的链接化模式。

## 4. 方向 C：工程加固包

### 4.1 C1 `AMapClient` 实例级 TTL 读缓存

- 缓存放 **client 实例**（宿主自持对象，非 kernel 模块全局——kernel 无状态约束不破）；键 `(method, tuple(sorted(params.items())))`，值 `(expire_at, envelope)`；`threading.Lock` 保护（site_profile 线程池并发 around_search）；
- TTL 分类常量：`geocode/regeocode` 24h（行政区划稳定）、`weather` 1h、`text/around/transit` 10min；
- 命中返回 `envelope.model_copy(deep=True)`（防下游改写污染缓存）+ `client.stats["cache_hit"]` 计数（宿主日志可见，验证省配额）；
- **Envelope 不加缓存标注字段**（模型稳定性 vs 可核验性取舍：缓存对调用方透明，数据新鲜度由 TTL 语义保证；PoC3 若需展示再议）。测试：同参二连发只打一次 `_get`；TTL 过期重发；参数差异不串味。

### 4.2 C2 `x-amap-key` 请求级多租户（F05 模式平移）

- **MCP**：`http_entry.py` HeaderOverridesMiddleware 白名单增 `x-amap-key` → `request_ctx.overrides()["amap_api_key"]` → `server.py` `_effective_host_ctx` 增分支 `ctx["amap_client"] = AMapClient(api_key=ov["amap_api_key"])`（对照 `flyai_api_key` 分支，注意单例绕过语义一致）；测试对照 `test_mcp_http.py` 的 `X-Flyai-Key` 逐请求用例平移；
- **Web**：`lib/keys.ts`/`ApiKeyCard` 增高德 Key 存储项 → 回合请求携带 → `loop.py` 回合级重建 `AMapClient(api_key=页面key)` 传入 dispatch（N0 通道）→ 无页面 Key 回落 `app.state.amap`（组装期值兜底，Header 只增强不削弱）。

### 4.3 C3 amap 结果进 `search_cache` 快照（live ref 平移）

- R08 §6 规格键机制平移：amap 聚合产出（`plan_last_mile` 的 hub/site/options、画像 `facilities`、行程 `last_mile.options`）data 附 `@live:amap_*:参数` 规格键，宿主轮末既有替换机制自动写 `search_cache`（跨会话可复读，零重复调用）；
- 原语层不附（与 flyai 原语口径一致）；实现时核对轮末替换扫描是否按 `@live:` 前缀通用——若是则宿主零改动，否则补 amap 工具名进白名单并加契约测试。

### 4.4 C4 双源路由显式标注

画像 geocode 失败的隐式回退显式化：`degraded` 文案统一为"地理底座：高德未收录，已回退纯 FlyAI 文本链路"（现有行为只换规范文案）；`SiteProfileCard` 前端"数据路由"徽标（`geo` 有值=高德底座 / None=FlyAI 回退），与 N6 面板同批交付。

## 5. 方向 B：Web 地图面板

### 5.1 Key 链路（Web 端 Key ≠ Web 服务 Key）

高德 JS API 需**另申请"Web 端(JS API)"类型 Key + 安全密钥 jscode**（2021-12 后新建 Key 强制配 jscode；Web 服务 Key 不能复用）。安全边界：JS API Key 本就前端可见，防滥用靠**域名白名单**（本地开发加 localhost）——设计文档钉死这一点，避免误用服务 Key 造成配额泄露。

- 后端 `core/config.py` 增 `amap_js_key` / `amap_js_security`（三级加载平移：`AMAP_JS_KEY` / `AMAP_JS_SECURITY` env + config.json 键覆盖）；`check_dependencies` 增 JS Key 可用性行（软检查）；
- 下发：settings 只读端点增两字段（核对 `ApiKeyCard` 现用端点后平移，不新开路由）；
- 前端 `@amap/amap-jsapi-loader`（npm 新增依赖，仅前端）动态加载；`useAmapLoader` hook：无 Key → 状态 `unavailable`（组件降级），有 Key → 注入 script + `securityJsCode`。

### 5.2 `AmapMapPanel` 组件

- 通用面板：props `{ center?: GeoLocation, markers: {location, title, kind}[], line?: [GeoLocation, GeoLocation] }`；
- 嵌入点一 `SiteProfileCard`：`geo` 居中 + 设施 Marker（`facilities` 五类色分：医疗/警务/金融/物流/集市）+ 画像点定位 Marker；
- 嵌入点二 `ItineraryCard` 接驳腿展开：hub/site 两点连线（**直线示意**，非公交折线——非目标已排除）；
- 降级链：无 Key / loader 失败 / WebGL 不可用 → 折叠为文字卡片（坐标 + 设施清单仍可读，地图是增强非依赖）；
- 组件 lazy 加载（`React.lazy`），不拖累首屏。

## 6. 宿主与能力面影响

| 面 | 影响 |
|---|---|
| kernel 能力面 | 数量不变（无新原语；A 是 `compose_itinerary` 行为增强、`plan_last_mile` 参数语义补全）；`date_hint` description 变更 → schema 文本更新，`>= 14` 钉子不破坏 |
| MCP Server | 工具面 30 不变；`x-amap-key` Header 支持（4.2）；live ref 轮末快照（4.3） |
| Web 宿主 | N0 dispatch 通道修补；回合级 amap Key（4.2）；前端 ItineraryCard 接驳行 + AmapMapPanel + 数据路由徽标 |
| CLI | 无新增改动（orchestrator 三通道已就位，A/C 自动生效） |
| `/ready` | 增 `amap_js_key` 软检查（缺不阻塞，对照 `amap_key`） |

## 7. 测试策略（零网络，对照 DEVELOPMENT.md §2）

| 文件 | 覆盖 |
|---|---|
| `tests/test_last_mile.py`（增） | date 3 天内透传进 query；超窗/非法格式降级附言；同城复用缓存零重复 geocode |
| `tests/test_itinerary.py`（增） | StubAmap 三态：有 cheapest → 预算条目+coverage；无 Key → last_mile=None 隔离+ok=True；异常 → note 不逃逸；`inputs_hash` 不因接驳变更 |
| `tests/test_amap_sources.py`（增） | 缓存命中/过期/参数差异/深拷贝防污染/并发安全（线程池双打） |
| `tests/test_web_agent_loop.py`（增） | N0 注入钉子；回合级 amap Key 重建（对照 F05 用例） |
| `tests/test_mcp_http.py`（增） | `X-Amap-Key` 逐请求生效/回落（对照 `X-Flyai-Key` 平移） |
| `tests/test_config.py`（增） | `AMAP_JS_KEY`/`AMAP_JS_SECURITY` 三级加载 |
| `web/src`（vitest 增） | AmapMapPanel 降级链、useAmapLoader 状态机、ItineraryCard 接驳行 |
| `tools/spike_amap.py`（扩） | 真 Key：date 透传 + 二连发缓存命中日志 + 行程接驳端到端 |

## 8. 里程碑与验收

| 里程碑 | 内容 | 验收标准（可勾选） |
|---|---|---|
| N0（0.25d） | web dispatch amap 通道 + deps 挂载 | 注入钉子绿；全量不回归 |
| N1（0.5d） | `plan_last_mile` date 透传 | 3 天内透传/超窗降级用例绿；真 Key 冒烟"昆明→喜洲"带日期 |
| N2（1d） | `compose_itinerary` 接驳腿 + 预算联动 + ItineraryCard 接驳行 | StubAmap 三态绿；预算条目/uncovered 口径用例绿；前端渲染快照过 |
| N3（0.5d） | AMapClient 实例缓存 | 缓存五用例绿；spike 二连发日志显示第二次 0 请求 |
| N4（1d） | `x-amap-key` 多租户（MCP + Web） | `X-Amap-Key` 逐请求用例绿；Web 回合级 Key 注入用例绿 |
| N5（0.5d） | live ref 快照 + 双源标注 | 轮末 search_cache 含 amap 快照；画像 degraded 规范文案钉子 |
| N6（1.5d） | Web 地图面板 | 有 JS Key：画像/行程卡地图渲染；无 Key：降级卡片；vitest 全绿 |
| N7（0.25d） | 冒烟 + 文档 | 全量 pytest/ruff/vitest 绿；README/DEVELOPMENT 增补 |

PoC2 完成判据：N0–N7 全绿 + 一次"带末端接驳的行程演示 + 地图面板截图"。

## 9. 风险与决策点

| 风险 | 缓解 |
|---|---|
| v3 transit `date` 3 天窗与调研行程周期错配 | N1 边界防护 + degraded 附言（透传是增强非承诺）；官方文档钉死窗口值 |
| JS API 安全密钥前端暴露面 | jscode 配域名白名单（localhost 开发域）；文档明示"Web 端 Key 与服务 Key 分离申请" |
| 缓存致数据陈旧误导决策 | TTL 按数据稳定性分类（§4.1）；transit 10min 窗口短于班次变动粒度 |
| live ref 轮末替换机制非通用 | N5 第一步先核对扫描实现，若白名单制则同步增补 + 契约测试 |
| 接驳查询放大调用量（每腿 2g+1t） | 与实例缓存协同（同城复用）；行程腿数典型 ≤4，单次组合 amap 增量 ≤12 次且命中缓存后趋零 |
| 前端依赖新增（amap-jsapi-loader） | 仅 web/package.json，kernel 约束不受影响；lazy 加载隔离首屏 |

## 10. 参考

- PoC1 设计：`docs/plans/2026-10-18-amap-poc-design.md`（本地工作目录，未入库；选型裁决/六原语/错误码表）
- 高德 JS API 2.0 安全密钥：https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode
- v3 公交路径规划 `date` 参数：https://lbs.amap.com/api/webservice/guide/direction/transit-integrated（3 天窗口）
- 仓库内对照：[kernel/aggregation/itinerary.py](../../../kernel/aggregation/itinerary.py)（R2 预算口径/失败隔离）、[kernel/aggregation/last_mile.py](../../../kernel/aggregation/last_mile.py)（数据探针）、[mcp_server/http_entry.py](../../../mcp_server/http_entry.py)（动态 Header 中间件）、[tests/test_sources_flyai.py](../../../tests/test_sources_flyai.py)（F05 回合级实例用例）、[web_host/agent/dispatch.py](../../../web_host/agent/dispatch.py)（N0 修补点）
