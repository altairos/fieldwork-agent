import { useEffect, useState } from "react";
import { rest, type HealthStatus, type SettingsStatus } from "../../api/rest";
import { clearKeys, getKeys, saveKeys } from "../../lib/keys";
import { Button } from "../ui/Button";
import { IconCheck, IconLock } from "../ui/icons";

/**
 * ApiKeyCard — 首页侧栏「API 密钥」卡。
 * 百炼 Key 必填（无 key 不进入对话，见 Workspace 拦截）；飞猪 Key 可选（行程规划）。
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

export function ApiKeyCard() {
  const [dashscope, setDashscope] = useState("");
  const [flyai, setFlyai] = useState("");
  const [appToken, setAppToken] = useState("");
  const [server, setServer] = useState<SettingsStatus | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [justSaved, setJustSaved] = useState(false);

  useEffect(() => {
    const k = getKeys();
    setDashscope(k.dashscope); // password 型回显圆点，可清空重输
    setFlyai(k.flyai);
    setAppToken(k.appToken);
    rest.settingsStatus().then(setServer).catch(() => setServer(null));
    rest.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  function save() {
    saveKeys(dashscope, flyai, appToken); // 留空即移除该项，回落服务端配置
    setJustSaved(true);
  }

  function clear() {
    clearKeys();
    setAppToken("");
    setDashscope("");
    setFlyai("");
    setJustSaved(false);
  }

  const ds = keyBadge(dashscope.trim() !== "", server?.dashscope_configured);
  const fa = keyBadge(flyai.trim() !== "", server?.flyai_configured);
  const tk = tokenBadge(appToken.trim() !== "", health?.auth_required);

  return (
    <div className="hairline rounded-lg bg-card p-5">
      <div className="font-serif text-[15px] font-semibold mb-1 flex items-center gap-2">
        <IconLock className="w-4 h-4 text-cinnabar" />
        API 密钥
      </div>
      <p className="text-[11.5px] text-inkfaint mb-3.5 leading-relaxed">
        百炼 Key 必填，未配置时仅可浏览项目、不可发起对话；飞猪 Key 可选，用于行程规划。Key 只保存在本浏览器。公网部署时服务端可设 FIELDWORK_APP_TOKEN，此处填写同一令牌。
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
            setDashscope(e.target.value);
            setJustSaved(false);
          }}
          placeholder="sk-…"
          autoComplete="off"
          className="w-full hairline rounded-md bg-paper/60 px-3 h-8 text-[12.5px] pointer-coarse:text-[16px] text-ink outline-none placeholder:text-inkfaint"
        />
      </label>
      <label className="block mb-3.5">
        <span className="flex items-center justify-between text-[11.5px] text-inksoft mb-1">
          飞猪 API Key（可选）
          <span className={fa.cls}>{fa.text}</span>
        </span>
        <input
          type="password"
          value={flyai}
          onChange={(e) => {
            setFlyai(e.target.value);
            setJustSaved(false);
          }}
          placeholder="可留空"
          autoComplete="off"
          className="w-full hairline rounded-md bg-paper/60 px-3 h-8 text-[12.5px] pointer-coarse:text-[16px] text-ink outline-none placeholder:text-inkfaint"
        />
      </label>
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
