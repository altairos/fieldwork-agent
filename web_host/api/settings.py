"""设置路由：API key 配置状态查询（布尔状态；JS Key 例外下发明文）。

首页「API 密钥」卡据此显示「服务端已配」徽标；Workspace 据此判定
「可浏览不可发」——服务端未配且浏览器未存 key 时禁用 Composer。

例外：高德 JS API 2.0 机制要求前端持有 key + securityJsCode（N6 地图
面板），故 amap_js_key/amap_js_security 下发明文——防滥用靠控制台域名
白名单，而非服务端保密。

S06 决策（风险接受，2026-10-21）：这两个字段服务于**匿名会话**的地图面板
（匿名项目是产品能力），按高德官方用法本就要下发到浏览器；改成布尔值会
直接打断地图。暴露面由两道边界兜住：``/settings`` 全族在
``_PROTECTED_PREFIXES`` 内，公网部署又被 S01 强制要求 ``FIELDWORK_APP_TOKEN``
（否则拒绝启动），未持令牌者拿不到该响应；本地无令牌模式只监听本机。
回归用例：``test_web_app_token.py``（无令牌 401）与 ``test_web_turns_api.py``
（字段形状）。
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
        "amap_configured": bool(getattr(cfg, "amap_api_key", "")),
        # JS Key 例外下发明文（见模块 docstring）
        "amap_js_configured": bool(getattr(cfg, "amap_js_key", "")),
        "amap_js_key": getattr(cfg, "amap_js_key", "") or "",
        "amap_js_security": getattr(cfg, "amap_js_security", "") or "",
    }
