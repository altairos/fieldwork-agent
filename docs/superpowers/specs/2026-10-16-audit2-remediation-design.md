# 田野罗盘 — 二次审计修复设计（R01–R09）

- 日期：2026-10-16
- 依据：`docs/reviews/2026-10-16-项目开发现状二次审计.md`
- 状态：设计已逐节呈现并获用户条件性认可；用户修订反馈（证据绑定、依赖指纹、备份事务、共享校验、过期保护、边界补充共 6 类）已全部并入本稿
- 范围裁决：全量修 R01–R09；R02/R03/R04 在 SQLite 工作区修复但**本轮不合流**；首轮遗留（F06/F07/F08/F10/F11 运行可靠性）另立一轮
- 执行方案：方案一——契约先行、两工作区分轨、逐项 TDD

## 0. 目标与总体策略

把二次审计的 9 个 P1 发现全部修复到"复现测试转绿 + 基线不回归"。三个总体原则：

1. **契约先行**：R01/R09 引发的 Repository 协议变更先在 Web 工作区定稿，SQLite 工作区随后按定稿契约对齐（R02），避免两工作区各自演化解耦再撞车。
2. **两工作区分轨**：Web 工作区（`F:/Agents_dev/Fieldwork_Agent`，`feat/web-frontend`，保留现有 11 个未提交文件叠加修复）修 R01/R05/R09/R06/R07/R08；SQLite 工作区（`F:/Agents_dev/Fieldwork_Agent-sqlite`，`feat/sqlite-persistence`）修 R02/R03/R04。不合流、不 merge、不 rebase。
3. **逐项 TDD**：每项先写复现测试（命名 `test_r01_*` … `test_r09_*`），确认红，再修复转绿。

共享逻辑一律放 kernel 宿主无关模块，MCP 不得依赖 web_host。

## 1. R01 · 快照 cid 越界读取（安全，最高优先）

**问题**：`web_host/api/projects.py:73` 将未校验的 cid 直接交给 `json_repo.get_cached_search`（`kernel/repository/json_repo.py:336`），后者 `glob("*/search_cache/{cid}.json")` 跨项目搜索；Windows 下 `..\` 编码可读 projects 根外 JSON；跨项目合法 cid 也返回 200。MCP 侧已核查无 `get_cached_search` 外部入口，无需改动。

**设计**：

1. **签名变更（协议层）**：`get_cached_search(cid)` → `get_cached_search(project_id, cid)`，按"项目 + cid"联合定位，废除跨项目 glob。
2. **cid 格式校验**：`re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid)`。现有两类合法格式（新式 `{prefix}_{timestamp}_{uuid8}`、legacy `{timestamp}_{n}`）均满足；路径分隔符、`..`、glob 元字符全部被拒。
3. **路径包含校验**：构造候选路径 `projects_root/project_id/search_cache/{cid}.json`，`Path.resolve()` 后要求：
   - 解析结果仍位于 `projects_root.resolve()` 之下（相对路径可算出），防 symlink/junction 外指；
   - 解析结果的父目录链精确为 `projects_root/<project_id>/search_cache`，即缓存目录本身不得经链接指向别处。
   仅比较两个 resolve 后父目录相等不够，必须做上述受信根包含校验。
4. **API 行为**：`checked_project_id` 先行（未知项目 404 保持现状语义）；cid 格式非法 → 400；格式合法但文件不存在或不在本项目内（含跨项目 cid）→ 404。错误信息不泄露文件系统细节。
5. **调用方同步**（Web 工作区）：`web_host/api/projects.py`、`core/orchestrator.py:357`、`tests/repository_contract.py`、`tests/test_json_repo.py`（`test_get_cached_search_spans_projects` 断言翻转为"跨项目不可读"）。SQLite 工作区的 `core/migrate.py:112` 调用点纳入 R02 一并对齐。

**测试**：`test_r01_*`——`..%5C` 越界 cid → 400 且哨兵文件不可读；跨项目合法 cid → 404；合法 cid 正常读取；legacy cid 兼容；symlink 外指缓存目录被拒（Windows 用 junction，复用既有跳过策略）。

## 2. R05 · 状态工具类型校验与轮末终态

**问题**：`web_host/agent/state_tools.py:44` 白名单只限字段名，任意字段可写 bool、任意非空串可写 bool 字段（`budget=true` 落盘 True）；轮末 `web_host/agent/loop.py:149-167` 的保存/抽取/推进位于 try/except（143-147）之外，`ResearchContext.model_validate` 抛错后 turn 卡 `running`。

**设计**：

1. **共享校验模块（kernel）**：新建 `kernel/context_patch.py`，宿主无关，MCP 与 REST 共用：
   - `sanitize_context_patch(patch: Mapping) -> dict`：逐字段严格类型校验，返回 clean patch；
   - 字段类型表（12 个白名单字段）：`interviews_not_applicable` 为 **bool**（唯一布尔字段）；`interviews_na_reason` 为 str；其余 10 个（discipline/topic/site_status/scale/budget/time_window/departure_city/target_location/travel_date/purpose）均为 str；
   - **严格类型**：不做任何自动转换——bool 字段收到 `"true"`、str 字段收到 bool/数字均判非法；
   - **任一字段非法 → 整次拒绝**（抛 `ValueError`，消息逐字段列出问题），不允许静默跳过；
   - 未知字段 → 明确报错（不静默丢弃）；`None` 与 strip 后空字符串 → 拒绝（本轮不引入"清空字段"语义）；
   - 既有 NA 联动校验（`interviews_not_applicable=True` 需 `interviews_na_reason` 非空）保持并纳入同一入口。
2. **MCP 状态工具**：`state_tools.py` 改为调用共享校验；非法参数返回 `invalid_arguments` 工具错误，**写入前校验**，错误数据不触达 Repository。
3. **REST 侧**：`web_host/api/state.py` 的修正端点用 Pydantic strict 模型（逐字段类型 + `model_config = ConfigDict(strict=True)`），与共享模块同一字段表；非法 → 422 带字段明细。
4. **提交 clean patch**：校验通过后，Repository 收到的是 clean patch 本身，不是调用方在锁外拼好的 merged 字典（合并发生在 Repository 事务内，见第 9 节 R04；JSON 后端语义等同）。
5. **轮末兜底**：`loop.py` 轮末段（149-167）纳入统一 try/except + finally：任何异常 → turn 终态 `error`（带原因），不再卡 `running`；已落盘的部分成果保留，坏数据在下一回合开始前仍需可被人工修正（修正 API 走同一校验）。

**测试**：`test_r05_*`——`{"budget": true}` 返回 invalid_arguments 且不落盘；str→bool、bool→str 全字段矩阵抽样；未知字段拒绝；空值拒绝；轮末抛错时 turn=error 且 SSE 发出终态事件；NA 联动校验。

## 3. R09 · 方案确认绑定与依赖指纹

**问题**：确认 API（`web_host/api/state.py:90`）只查 proposals 非空；Repository（`json_repo.py:474/496`）默认确认最后一个文档不限 kind（仅存提纲也能确认推进 finished）；前端（`ProposalView.tsx:187`）只发 project_id 不发所见 doc_id；确认哈希只含 6 个旅行字段（`json_repo.py:37` `CONFIRM_INPUT_FIELDS`），换 topic 后 stale 仍为 False；新增任何文档（含提纲）一律使旧确认失效。

**设计**：

1. **共享指纹模块（kernel）**：新建 `kernel/repository/confirm_hash.py`，两个 Repository 共用；哈希原语复用现有 `kernel/models.py:299` 的 `compute_inputs_hash`（canonical JSON → sha256），新增的是"依赖收集与规范化"层：
   - `compute_confirm_hash(state) -> str`：对**生成时实际依赖**做稳定序列化哈希；
   - 覆盖范围：9 个研究字段（原 6 旅行字段 + discipline/topic/purpose）+ **田野点全记录**（非仅 id+name；列表按 id 排序后哈希）+ **报道人全记录**（同上）+ 最新一次行程产物的 cid（行程经 `repo.cache_search` 以 `tool=compose_itinerary` 落盘，取该类最新记录 id）+ 知识资料记录；
   - 数值规范化：统一转字符串序列化，避免 int/float 表示差异造成假 stale。
2. **生成时存指纹**：`save_proposal` 时计算当前依赖的 `dep_hash` 存入文档记录（JSON 侧存文档元数据内；SQLite 侧 R02 建列）。
3. **确认绑定**：
   - `confirm_proposal(project_id, doc_id)`（签名变更：现 `subject` 参数并入 `doc_id`，改为必填并收紧语义）：校验 doc 存在、`kind == "proposal"`、doc 的 `dep_hash == compute_confirm_hash(当前状态)`；
   - 不一致 → 拒绝（REST 409，消息提示"研究输入已变化，请重新生成方案后再确认"），**不得**确认旧正文；
   - REST：`POST /projects/{pid}/confirm` 请求体带 `doc_id`；前端 `doConfirm` 发送当前查看的 `doc.id`（查看历史版本/提纲时按钮对所看文档负责，提纲 kind 不符 → 按钮禁用或明确提示）。
4. **stale 语义（按类型判定）**：
   - 保存新 `kind=proposal` 文档 → 旧确认失效（现行为保留）；
   - 保存 `kind=interview_guide` → **不**使 proposal 确认失效（作用域分开）；
   - 研究依赖变化 → `is_proposal_confirmation_stale` 为 True（哈希范围已扩至全依赖）；
   - **stale 的确认不构成有效确认**：不能凭 stale 确认进入或保持 `finished`；历史确认记录原样保留（审计数据不删），仅失去"当前有效"资格。
5. **旧行程适配**：不新增"缓存中曾有行程"式判定；行程 cid 进入指纹，行程产物变化自然触发 stale。

**测试**：`test_r09_*`——仅提纲时 confirm 拒绝（doc 不存在 → 404；kind≠proposal → 400）且不推进 finished；doc_id 绑定（确认非所看/不存在文档被拒）；生成后改 topic → 确认 409；改田野点全记录任一字段 → stale；新增提纲不使 proposal 确认失效、新增 proposal 使其失效；stale 确认不算有效确认。

## 4. R06 · turn_done 后快照回读与过期保护

**问题**：回合结束前端不回读快照（`web/src/hooks/useTurn.ts` 只派发事件）；新画像无持久化 site_id，前端补 `id:"", liveOnly:true`（`FieldSitesPanel.tsx:281`），组件对 liveOnly 只显示"待确认"无按钮（`:134`）；`stores/project.ts` 的 `loadSnapshot` 全量覆盖无保护。

**设计**：

1. **turn_done 触发回读**：`useTurn` 在收到 `turn_done`（且该 seq 未见过，防 SSE 重放重复触发）后对当前项目调用 `getProject` 刷新 store（快照含已持久化的田野点/研究字段/确认状态，画像记录带回真实 site_id）。
2. **过期响应保护**：回读写入 store 前核对：
   - 响应 projectId === store 当前 projectId（用户已切换项目则丢弃）；
   - 请求代次（turn 序号/generation token）：较早回合的回读晚返回时丢弃，不覆盖新状态。
3. **失败路径**：回读失败 → 设置可重试的 error 提示（UI toast 或面板内提示），保留 liveOnly 兜底渲染（不闪空、不清现有数据）；提供手动重试（复用刷新按钮语义）。
4. liveOnly 记录语义收敛：仅当快照回读失败或记录确实未落盘时保留"待确认（未持久化）"标注；回读成功后以快照记录替换 live 记录。

**测试**：`test_r06_*`（Vitest + jsdom 渲染真实组件）——画像 tool_completed + turn_done 后按钮出现且带真实 id；切换项目后晚到的回读不覆盖；重放 seq 不重复回读；回读失败显示错误提示且 liveOnly 兜底仍在；重试成功后按钮恢复。

## 5. R07 · 时窗缺价预算语义

**问题**：`kernel/aggregation/window.py:111` 某类报价缺失按 0 计入 estimate，`budget_score` 照常奖励，`:298` 用不完整小计得出 `budget_feasible=True`；`Envelope.degraded` 为空；前端 WindowCard 显示"预算可行"。

**设计**：

1. **窗口级 coverage**：每个候选窗口逐类（flight/hotel/体验等）判定报价覆盖：全齐 = `full`；任一必要类缺失 = `partial`/`missing`（单点缺失也算 partial）。判定在窗口粒度，非全局。
2. **数值命名**：coverage 非全时，小计字段命名为 `estimate_known`（"已知小计"），不再冒充完整 estimate；缺失类目在 `uncovered` 明细列出（对齐行程 BudgetBreakdown 的 coverage/uncovered 语义）。
3. **budget_feasible 三态**：类型改为 `bool | None`——coverage=full → true/false 照常；缺必要报价 → `None`（未知/待核验），不输出肯定结论；`budget_score` 对缺价窗口不再给预算奖励分。
4. **往返估算标注**："去程价×2"的返程估算在理由文本中明确标注为估计值、非真实返程报价。
5. **degraded 透传**：缺价信息写入 `Envelope.degraded`（顶层），措辞与既有 degraded 风格一致。
6. **全栈贯通**：Python 模型（window.py 出参）→ API 序列化 → 前端 TS 类型（`budget_feasible: boolean | null`）→ WindowCard 三态渲染：true"预算内" / false"超预算" / null"待核验（仅已知小计）"。

**测试**：`test_r07_*`——单类全缺失（复现用例：航班无价+酒店 11 元+预算 1000 → feasible=None、estimate_known、无"预算内"理由）；部分日期缺价；单点缺失；体验模式脱敏；完整报价时行为与现状一致（无回归）。

## 6. R08 · 报价证据引用绑定实际调用

**问题**：live refs 按目的地单键保存被覆盖（`loop.py:281-307` `dest_refs[dest] = ...`）；compose 收到的是轮初浅拷贝（`dispatch.py:37` `dict(live_snapshot_refs)`），把旧 cid 贴到新报价上（复现：旧酒店 11 元、新酒店 22 元，住宿条目证据仍是 11 元快照）；行程内部 `search_hotels` 用位置参数传目的地（`itinerary.py:315` 附近），不被 `_drain_snapshots` 捕获。

**设计**（按用户裁决：同规格 ≠ 同一次报价，绑定本次实际调用）：

1. **报价一律来自本次调用**：`compose_itinerary` 计算预算使用的价格全部来自本次回合发起的实时搜索结果，不从旧快照/旧 refs 取价。
2. **`@live:` 临时标记**：组装预算条目时 `source_ref` 写 `@live:{method}:{规格键}`（如 `@live:search_hotels:{dest}|{check_in}|{check_out}`、`@live:search_flights:{origin}>{dest}|{dep_date}`），明确标注"本次实时调用、暂无 cid"。
3. **轮末升级**：`_drain_snapshots` 落盘后，把快照记录与预算条目里的 `@live:` 标记按 method+规格键精确匹配，升级为**本次调用**的 cid。匹配键含工具、路线/目的地、日期/入住退房与相关过滤参数。
4. **旧 cid 仅限真实复用**：只有逻辑上真正从缓存读取旧快照报价参与计算时才引用旧 cid（本轮 compose 无此路径，即移除按裸 dest 匹配旧引用的逻辑）；轮初 `dispatch.py:37` 浅拷贝传入的旧 refs 不再作为价格或引用匹配来源，错配路径自然消解。
5. **位置参数 kwargs 化**：`itinerary.py` 内 `client.search_hotels(w["dest"], check_in=...)` 改为 `client.search_hotels(destination=w["dest"], check_in=...)`，使快照链能从 kwargs 捕获规格。
6. **dest 单键覆盖消除**：轮中 refs 按"method+规格键"多维保存，不同工具/日期不互相覆盖。

**测试**：`test_r08_*`——"同规格、不同价格"回归（第一查 11 元、第二查 22 元：预算用 22 元，证据 cid 指向第二次快照，快照内价格=22）；航班+酒店混合条目逐条核对"预算单价×数量 === 引用快照内价格"（不只断言 source_ref 含 cid）；酒店 kwargs 化后进入快照链；轮末升级后无残留 `@live:` 标记。

## 7. R02 · SQLite 契约对齐（SQLite 工作区）

**问题**：SQLite 实现停留旧 23 方法，缺 `confirm_field_site`/`confirm_proposal`/`get_proposal_by_id`/`is_proposal_confirmation_stale`；无 `root` 属性，新版 `/ready` 目录检查不适用；隔离注入当前 create_app 后 `GET /projects/p` 500。R01/R09 定稿后契约还需含 `get_cached_search(project_id, cid)`、`confirm_proposal(project_id, doc_id)`、文档 `dep_hash`。

**设计**：

1. **对齐最新协议**：SQLite 工作区的 `protocol.py` 与 Web 工作区定稿版逐方法对齐（含第 1、3 节的签名变更），`sqlite_repo.py` 补齐全部缺失方法与签名适配；`core/migrate.py:112` 的 `get_cached_search` 调用同步新签名。
2. **dep_hash 存储**：proposals 表加列 `dep_hash`（nullable）。旧文档 `dep_hash` 为 NULL 时，新确认请求按"指纹缺失"拒绝并提示重新生成；已存在的确认记录不受影响（迁移校验对 NULL 计数报告）。
3. **schema 升级测试**：用旧版本 schema 建库 → 触发升级路径 → 断言新列/新表存在且旧数据完整（现有库平滑升级，不要求重建）。
4. **ready 独立探针**：`/ready` 对 SQLite 后端执行专用探针——`SELECT 1` + 对专用探针表（或临时表）的事务性写入/回滚，不触碰业务数据；失败返回 503；不伪造 `root` 目录属性。
5. **factory 接线**：`FA_STORAGE=sqlite` 工厂在该工作区更新为基于最新契约（不引入 Web 工作区，合流时再统一）。
6. **双实现契约**：`tests/repository_contract.py` 定稿版同步到 SQLite 工作区并在其上运行（含 R01/R09 新行为）。

**测试**：`test_r02_*`——缺失 4 方法的行为与 JSON 实现逐项一致（契约测试双跑）；旧 schema 库升级；SQLite 后端 `/ready` 探针成功/失败（503）；`GET /projects/p` 在注入后不再 500。

## 8. R03 · 迁移冲突保护与备份（SQLite 工作区）

**问题**：`core/migrate.py:100` 对未打标记的同名目标项目直接 `_clear_messages` 重导，覆盖已有业务数据且无提示；确认数据（proposal_confirmed/confirmations）迁移后为 None 而 `_verify` 不含这些字段仍报成功。

**设计**：

1. **备份**：迁移前用 **SQLite Backup API**（`sqlite3.Connection.backup()`，WAL 安全）创建完整备份文件，命名 `<db名>.backup-<YYYYMMDD-HHMMSS>.db`、与库同目录，滚动保留最近 3 份（超出删除最旧）；禁止裸拷贝 `.db` 文件作为备份。
2. **恢复前置条件**：从备份恢复整库前必须停止其他写入并关闭相关连接（工具内拒绝在活跃连接存在时执行）。
3. **冲突检测（全类别）**：目标项目无 migrated 标记时，检测以下任一非空即判冲突：messages、field_sites、informants、knowledge、search_cache、proposals、research_context、确认记录/标记。冲突 → 默认拒绝并列出冲突明细（类别 + 行数），不执行覆盖。
4. **`--force` 语义**：定义为**单项目事务内的完整替换**——同事务内 DELETE 该项目全部类别 → 全量重导 → 校验 → 写标记；任一步失败整体回滚（不是只清消息）。非 force 且冲突时零改动。
5. **单项目事务**：每个项目的导入 + 校验 + 标记在同一项目事务内完成（校验从全局后置改为逐项目事务内）；全局 `_mark_migrated` 在全部项目成功后写。
6. **校验升级**：`_verify` 增补确认数据一致性——`proposal_confirmed`、确认记录内容、finished 阶段与确认状态互相印证；行数 + 抽样哈希范围覆盖新字段。

**测试**：`test_r03_*`——非空目标无标记默认拒绝且零改动；`--force` 事务内完整替换；中途失败回滚（用注入异常验证）；确认数据迁移后一致（JSON 侧 confirmed=True → SQLite 侧同为 True 且记录完整）；备份文件可经 Backup API 产生且恢复后数据一致。

## 9. R04 · SQLite 并发原子合并（SQLite 工作区）

**问题**：`sqlite_repo.py:295-307` `update_research_context` 为 SELECT→Python 合并→UPDATE，无写入锁，两连接并发丢更新（复现：budget+topic 双 patch 只剩 budget）。

**设计**：

1. **`BEGIN IMMEDIATE` 事务辅助**：新增 `_tx(immediate=True)` 上下文管理器，读前取得写入锁；覆盖**全部** read-modify-write 点——已知点：`update_research_context`、`set_phase`/phase_history 写入；实现计划中逐一盘点 `sqlite_repo.py` 其余序号/计数分配点并全部纳入。
2. **事务边界统一**：合并发生在事务内——`BEGIN IMMEDIATE` 后 SELECT 当前值 → 应用 clean patch → 用第 2 节共享 `sanitize_context_patch` 校验 merged → UPDATE；不得在锁外预读预合并整份字典再进锁写入。
3. **单项目写锁覆盖状态 API**：Web 状态端点路径与后台 loop 走同一 Repository 事务边界（SQLite 工作区内验证；busy_timeout 已有配置保持）。
4. **并发测试纪律**：屏障放**调用入口**——两个线程都发起调用后再同步放行，由 BEGIN IMMEDIATE 串行化；不得要求两个线程都完成受锁保护的 SELECT 后再继续（否则测试自身死锁）。断言：budget 与 topic 双双保留。

**测试**：`test_r04_*`——审计复现用例转绿（双连接 + 入口屏障 → 两字段都在）；`_tx` 对 phase_history/序号分配的原子性抽样；同一事务内校验失败（非法 patch）时零写入。

## 10. 执行顺序与工作区纪律

1. **Web 工作区**（保留未提交改动，叠加修复）：
   R01 → R05 → R09 → R06 → R07 → R08。
   R01 最先（安全）；R09 在 R05 后（共享校验与指纹都落 kernel，先定输入契约）；R06 依赖 R09 的确认状态语义（快照回读含确认/字段）；R07/R08 为聚合层独立收尾。契约定稿点 = R09 完成。
2. **契约定稿后 → SQLite 工作区**：R02 → R03 → R04（先对齐契约，再迁移保护，再并发）。
3. 每项完成后跑对应工作区全量测试 + 定向 Ruff；Web 侧涉及前端的项加跑 Vitest。
4. 两工作区各自独立提交；**不合流**。合流与联合回归属下一轮。

## 11. 测试策略与验收标准

**TDD 纪律**：每项先写复现测试（`test_r01_*` … `test_r09_*`，前端项为 `r06` Vitest 用例），确认失败后修复。

**基线（只增不减）**：
- Web 工作区：pytest 448 passed / 1 skipped；Vitest 176 tests；业务源码定向 Ruff 通过。
- SQLite 工作区：pytest 383 passed（含新增契约/迁移/并发测试）；Ruff 通过。

**最终验收（新增端到端主线）**：真实 FastAPI 后端 + 浏览器（替身外部服务、替身模型），空项目起步：定位 → **真实工具生成画像 → 当前页面直接确认** → 采纳报道人对象 → **真实工具生成行程** → 生成文档 → 按版本/类型查看 → 确认完成。全程**不通过测试代码直接向 Repository 补写田野点、行程等关键产物**。JSON 后端本轮执行该主线；SQLite 后端同一脚本在合流轮执行。刷新恢复（F07）不在本轮硬性验收内，主线验收不含该项。

## 12. 范围外（明确不做）

- 首轮遗留 F06/F07/F08/F10/F11：幂等执行、历史恢复、取消、SSE 失败终态、工具预算/内部调用额度——另立一轮。
- CLI 宿主完整一致性（`orchestrator.py:284` 事件落盘文档分支、`:311` 阶段推进 proposal_confirmed）：本轮仅同步 R01 签名变更波及的最小调用点，其余不扩。
- SQLite 分支合流、CI 增加 npm build/真实后端浏览器主线（属合流轮）。
- "清空研究字段"语义、旧行程与新日期适配的独立校验器（R09 指纹已覆盖行程变化路径）。
