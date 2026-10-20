import { useEffect, useState } from "react";
import { rest, type HealthStatus, type SettingsStatus } from "../../api/rest";
import { clearKeys, getKeys, saveKeys } from "../../lib/keys";
import { Button } from "../ui/Button";
import { IconCheck } from "../ui/icons";

/**
 * ApiKeySection — API 密钥配置节：首页「密钥」弹层（KeyDialog）内承载。
 * （「我的」卡解体后，导出格式迁提案页下载区、配色走对话页 Header。）
 * 百炼 Key 必填（无 key 不进入对话，见 Workspace 拦截）；飞猪/高德 Key 为可选高级项——
 * 计费核验：飞猪 AI 开放平台不计费，服务端已配共享正式 Key，开箱即用，
 * 仅需覆盖时（如共享额度耗尽）才自填，默认收进折叠区。
 * key 只存浏览器 localStorage（lib/keys.ts），发送时经 rest.ts 注入请求头，
 * 不落服务端。徽标显示 key 来源：浏览器已存 > 服务端已配（环境变量）> 未配置。
 */

/** 单项 key 的来源徽标（本地已存优先展示——页面 key 优先生效） */
function keyBadge(local: boolean, serverCfg?: boolean): { text: string; cls: string } {
  if (local) return { text: "浏览器已存", cls: "text-bamboo" };
  if (serverCfg) return { text: "服务端已配", cls: "text-indigo" };
  return { text: "未配置", cls: "text-ochre" };
}

/** 访问令牌徽标（审计 F02）：服务端未启用 / 浏览器已存 / 服务端要求且未填 */
function tokenBadge(local: boolean, required?: boolean): { text: string; cls: string } {
  if (!required) return { text: "未启用", cls: "text-inkfaint" };
  if (local) return { text: "浏览器已存", cls: "text-bamboo" };
  return { text: "需要令牌", cls: "text-cinnabar" };
}

export function ApiKeySection() {
  const [dashscope, setDashscope] = useState("");
  const [flyai, setFlyai] = useState("");
  const [appToken, setAppToken] = useState("");
  const [plan, setPlan] = useState(""); // 百炼套餐类型（coding/token）：空 = 默认端点
  const [amap, setAmap] = useState(""); // 高德 Web 服务 Key（N4）：空 = 服务端共享
  const [server, setServer] = useState<SettingsStatus | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [justSaved, setJustSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false); // 飞猪/高德 Key / 套餐端点可选高级项，默认收起

  useEffect(() => {
    const k = getKeys();
    setDashscope(k.dashscope); // password 型回显圆点，可清空重输
    setFlyai(k.flyai);
    setAppToken(k.appToken);
    setPlan(k.plan);
    setAmap(k.amap);
    rest.settingsStatus().then(setServer).catch(() => setServer(null));
    rest.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  function save() {
    saveKeys(dashscope, flyai, appToken, plan, amap); // 留空即移除该项，回落服务端配置
    setJustSaved(true);
  }

  function clear() {
    clearKeys();
    setAppToken("");
    setDashscope("");
    setFlyai("");
    setPlan("");
    setAmap("");
    setJustSaved(false);
  }

  const ds = keyBadge(dashscope.trim() !== "", server?.dashscope_configured);
  const fa = keyBadge(flyai.trim() !== "", server?.flyai_configured);
  const am = keyBadge(amap.trim() !== "", server?.amap_configured);
  const tk = tokenBadge(appToken.trim() !== "", health?.auth_required);

  return (
    <div>
      <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">API 密钥</div>
      <p className="text-[11.5px] text-inkfaint mb-3.5 leading-relaxed">
        百炼 Key 必填，未配置时仅可浏览项目、不可发起对话。行程与地图数据服务端已配飞猪/高德
        共享 Key，无需自填（高级选项可覆盖）。Key 只保存在本浏览器。公网部署时服务端可设
        FIELDWORK_APP_TOKEN，此处填写同一令牌。
      </p>
      <label className="block mb-2.5">
        <span className="flex items-center justify-between text-[11.5px] text-inksoft mb-1">
          百炼 API Key（必填）
          <span className={ds.cls}>{ds.text}</span>
        </span>
        <input
          type="password"
          value={dashscope}
          onChange={(e) => {
            const v = e.target.value;
            setDashscope(v);
            setJustSaved(false);
            // 套餐 Key（sk-sp-）配专属端点：自动展开高级项供选择配套端点；
            // 换回普通 key 则自动回落默认端点（普通 key 配套餐端点同样 401）
            if (v.trim().startsWith("sk-sp-")) setShowAdvanced(true);
            else if (v.trim() !== "") setPlan("");
          }}
          placeholder="sk-…"
          autoComplete="off"
          className="w-full hairline rounded-md bg-paper/60 px-3 h-8 text-[12.5px] pointer-coarse:text-[16px] text-ink outline-none placeholder:text-inkfaint"
        />
      </label>
      {dashscope.trim().startsWith("sk-sp-") && !plan && (
        <p className="text-[11px] text-cinnabar -mt-1.5 mb-2.5 leading-relaxed">
          检测到套餐 Key（sk-sp- 开头）：需在高级选项选择配套端点，否则默认端点将 401
        </p>
      )}
      <label className="block mb-3.5">
        <span className="flex items-center justify-between text-[11.5px] text-inksoft mb-1">
          访问令牌（App Token）
          <span className={tk.cls}>{tk.text}</span>
        </span>
        <input
          type="password"
          value={appToken}
          onChange={(e) => {
            setAppToken(e.target.value);
            setJustSaved(false);
          }}
          placeholder={health?.auth_required ? "服务端已启用，必填" : "服务端未启用，可留空"}
          autoComplete="off"
          className="w-full hairline rounded-md bg-paper/60 px-3 h-8 text-[12.5px] pointer-coarse:text-[16px] text-ink outline-none placeholder:text-inkfaint"
        />
      </label>
      {/* 飞猪 Key 可选高级项：默认收起，来源徽标折叠态仍外显（不展开也能知道服务端已配） */}
      <div className="flex items-center justify-between mb-2">
        <button
          type="button"
          aria-expanded={showAdvanced}
          onClick={() => setShowAdvanced((v) => !v)}
          className="text-[11.5px] text-inksoft hover:text-ink transition"
        >
          {showAdvanced ? "▾" : "▸"} 高级选项
        </button>
        <span className={`${fa.cls} text-[11.5px]`}>{fa.text}</span>
      </div>
      {showAdvanced && (
        <>
          <label className="block mb-3.5">
            <span className="flex items-center justify-between text-[11.5px] text-inksoft mb-1">
              百炼套餐类型（端点）
            </span>
            <select
              value={plan}
              onChange={(e) => {
                setPlan(e.target.value);
                setJustSaved(false);
              }}
              className="w-full hairline rounded-md bg-paper/60 px-2.5 h-8 text-[12.5px] text-ink outline-none"
            >
              <option value="">按量付费（默认端点）</option>
              <option value="coding">Coding 套餐（sk-sp- 专属端点）</option>
              <option value="token">Token 套餐（sk-sp- 专属端点）</option>
            </select>
          </label>
          <label className="block mb-3.5">
            <span className="flex items-center justify-between text-[11.5px] text-inksoft mb-1">
              飞猪 API Key（可选）
            </span>
            <input
              type="password"
              value={flyai}
              onChange={(e) => {
                setFlyai(e.target.value);
                setJustSaved(false);
              }}
              placeholder="可留空——默认用服务端共享 Key"
              autoComplete="off"
              className="w-full hairline rounded-md bg-paper/60 px-3 h-8 text-[12.5px] pointer-coarse:text-[16px] text-ink outline-none placeholder:text-inkfaint"
            />
          </label>
          <label className="block mb-3.5">
            <span className="flex items-center justify-between text-[11.5px] text-inksoft mb-1">
              高德 API Key（可选）
              <span className={`${am.cls} text-[11.5px]`}>{am.text}</span>
            </span>
            <input
              type="password"
              value={amap}
              onChange={(e) => {
                setAmap(e.target.value);
                setJustSaved(false);
              }}
              placeholder="可留空——默认用服务端共享 Key"
              autoComplete="off"
              className="w-full hairline rounded-md bg-paper/60 px-3 h-8 text-[12.5px] pointer-coarse:text-[16px] text-ink outline-none placeholder:text-inkfaint"
            />
          </label>
        </>
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save}>
          保存
        </Button>
        <Button size="sm" variant="outline" onClick={clear}>
          清除
        </Button>
        {justSaved && (
          <span className="flex items-center gap-1 text-[11.5px] text-bamboo">
            <IconCheck className="w-3.5 h-3.5" />
            已保存
          </span>
        )}
      </div>
    </div>
  );
}
