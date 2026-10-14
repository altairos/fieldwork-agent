#!/usr/bin/env python3
"""田野旅行规划 Agent — 主入口"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fieldwork_agent.core.cli import main

if __name__ == "__main__":
    main()
