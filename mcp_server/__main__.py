"""``python -m fieldwork_agent.mcp_server`` 入口（与 console script fieldwork-mcp 同源）。"""
from fieldwork_agent.mcp_server.server import run_stdio

if __name__ == "__main__":
    run_stdio()
