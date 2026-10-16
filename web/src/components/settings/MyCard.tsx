/**
 * MyCard — 首页侧栏「我的」卡：导出格式 / 界面配色 / API 密钥三节。
 * 导出格式 chips 点击即生效（写 localStorage，无保存钮）；配色沿用 ThemeSwitcher
 * （卡壳从标题区移入，组件零改动）；密钥节由原 ApiKeyCard 改造（ApiKeySection）。
 * chips 样式沿用 ProjectHub 学科倾向（tokens.css .chip + 选中 cinnabar 洗色）。
 */
import { EXPORT_FORMATS } from "../../lib/exportFormat";
import { useExportFormatStore } from "../../stores/exportFormat";
import { IconUser } from "../ui/icons";
import { ThemeSwitcher } from "../ui/ThemeSwitcher";
import { ApiKeySection } from "./ApiKeyCard";

export function MyCard() {
  const format = useExportFormatStore((s) => s.format);
  const setFormat = useExportFormatStore((s) => s.setFormat);

  return (
    <div className="hairline rounded-lg bg-card p-5">
      <div className="font-serif text-[15px] font-semibold mb-1 flex items-center gap-2">
        <IconUser className="w-4 h-4 text-cinnabar" />
        我的
      </div>
      <p className="text-[11.5px] text-inksoft leading-relaxed mb-4">
        导出格式、界面配色与密钥的个人偏好，只保存在本浏览器。
      </p>

      {/* 第一节：导出格式（chips 沿用 ProjectHub 学科倾向样式） */}
      <div className="mb-4">
        <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">导出格式</div>
        <div className="flex flex-wrap gap-1.5">
          {EXPORT_FORMATS.map((f) => {
            const selected = format === f.id;
            return (
              <button
                key={f.id}
                type="button"
                aria-pressed={selected}
                title={f.note}
                onClick={() => setFormat(f.id)}
                className={`chip transition ${
                  selected
                    ? "bg-cinnabarwash text-cinnabar border-cinnabar/25"
                    : "bg-card hairline text-inksoft hover:bg-cardsunk"
                }`}
              >
                {f.label}
              </button>
            );
          })}
        </div>
        <div className="text-[10.5px] text-inkfaint mt-2">
          下载最终报告时使用的文件格式，仅保存在本浏览器。
        </div>
      </div>

      {/* 第二节：界面配色（ThemeSwitcher 原样嵌入，零改动） */}
      <div className="mb-4">
        <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">界面配色</div>
        <ThemeSwitcher />
      </div>

      {/* 第三节：API 密钥（原 ApiKeyCard 改造，卡壳已上移至本卡） */}
      <ApiKeySection />
    </div>
  );
}
