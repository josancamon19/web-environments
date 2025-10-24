from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional, List


class ToolCall(Enum):
    CLICK = "click"  # params (selector: str)
    TYPE = "type"  # params (selector: str, text: str)
    GO_TO = "go_to"  # params (url: str)


@dataclass
class BaseToolCallData:
    type: str
    params: Dict[str, Any]
    timestamp: Optional[str]

    def to_dict(self):
        return {"type": self.type, "params": self.params, "timestamp": self.timestamp}


@dataclass
class ToolCallData(BaseToolCallData):
    step_ids: List[int]

    def to_dict(self):
        data = super().to_dict()
        data["step_ids"] = self.step_ids
        return data
