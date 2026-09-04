"""行事曆、鬧鐘、記帳、課表。

對外只暴露 store——工具層（給助理用的 MCP）與傳輸層（給 App 用的 HTTP）
都走同一份資料操作，不各寫一套。
"""
from . import store

__all__ = ["store"]
