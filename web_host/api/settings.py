"""设置路由：API key 配置状态查询（只回布尔，不回明文）。

首页「API 密钥」卡据此显示「服务端已配」徽标；Workspace 据此判定
「可浏览不可发」——服务端未配且浏览器未存 key 时禁用 Composer。
"""

from fastapi import APIRouter, Request

router = APIRouter(tags=["settings"])


@router.get("/settings/status")
async def settings_status(request: Request):
    cfg = request.app.state.config
    return {
        # 单例非 None = 环境变量/配置文件 key 就绪（含 SDK 在位；二者缺一均构建失败 → None）
        "dashscope_configured": request.app.state.llm is not None,
        "flyai_configured": bool(getattr(cfg, "flyai_api_key", "")),
    }
