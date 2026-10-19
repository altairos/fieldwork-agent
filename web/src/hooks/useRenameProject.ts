import { useCallback } from "react";
import { rest } from "../api/rest";
import { useProjectStore } from "../stores/project";

/**
 * Header 改名（评审 R8 拆分自 App.tsx 的 Workspace）：PUT title → 重拉快照（Header 经 store
 * 回读新名）。失败**保持原名**并只 warn——没有全局 toast 通道，静默失败比弹一个误报成功
 * 的提示好；用户重试即可。
 *
 * 不用本地 state 乐观更新：标题的单一数据源是 project snapshot（右栏 BasicInfoPanel 同源），
 * 双写必然漂移。
 */
export function useRenameProject(projectId: string) {
  const loadSnapshot = useProjectStore((s) => s.loadSnapshot);
  return useCallback(
    async (name: string) => {
      try {
        await rest.updateResearchContext(projectId, { title: name });
        loadSnapshot(await rest.getProject(projectId));
      } catch (e) {
        console.warn("rename failed:", e);
      }
    },
    [projectId, loadSnapshot],
  );
}
