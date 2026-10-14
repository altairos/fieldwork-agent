"""
百炼 / DashScope SDK 集成层
提供文本对话、结构化输出等能力
"""
import os
from typing import Any, Dict, List

try:
    from dashscope import Generation
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False


class DashScopeChat:
    """通过 DashScope SDK 调用千问模型"""

    def __init__(self, api_key: str = "", model: str = "qwen-max", base_url: str = ""):
        if not DASHSCOPE_AVAILABLE:
            raise RuntimeError("DashScope SDK not installed. Run: pip install -U dashscope")
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.model = model
        self.base_url = base_url or os.getenv(
            "DASHSCOPE_HTTP_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"
        )
        if self.api_key:
            os.environ["DASHSCOPE_API_KEY"] = self.api_key

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7,
             max_tokens: int = 4096, stream: bool = False) -> Dict[str, Any]:
        """发送对话请求"""
        call_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stream:
            return self._stream_call(call_kwargs)
        else:
            return self._sync_call(call_kwargs)

    def _sync_call(self, kwargs: dict) -> Dict[str, Any]:
        response = Generation.call(**kwargs)
        if response.status_code != 200:
            return {"status": "error", "code": response.status_code, "message": response.message}
        return {
            "status": "ok",
            "content": response.output.choices[0].message.content,
            "finish_reason": response.output.choices[0].finish_reason,
            "usage": response.usage,
        }

    def _stream_call(self, kwargs: dict):
        for chunk in Generation.call(stream=True, **kwargs):
            if chunk.status_code == 200:
                yield {"status": "chunk", "content": chunk.output.choices[0].message.content}
            else:
                yield {"status": "error", "code": chunk.status_code, "message": chunk.message}


def create_chat_client(config) -> DashScopeChat:
    """根据 Config 创建聊天客户端"""
    return DashScopeChat(
        api_key=config.dashscope_api_key,
        model=config.chat_model,
    )
