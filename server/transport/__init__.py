"""傳輸層：實作 protocol.Frontend，把引擎事件送到手機。不含業務邏輯。"""
from .hub import EventHub, SseFrontend

__all__ = ["EventHub", "SseFrontend"]
