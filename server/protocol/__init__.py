"""協定層：引擎與傳輸層之間的契約。兩邊都只依賴這裡，彼此不直接相依。"""
from .events import COALESCABLE, Event, EventType, make_event
from .frontend import (
    CONFIRM_CHOICES,
    AskChoice,
    AskKind,
    AskRequest,
    AskResponse,
    Frontend,
    is_approved,
)

__all__ = [
    "COALESCABLE", "Event", "EventType", "make_event",
    "CONFIRM_CHOICES", "AskChoice", "AskKind", "AskRequest", "AskResponse",
    "Frontend", "is_approved",
]
