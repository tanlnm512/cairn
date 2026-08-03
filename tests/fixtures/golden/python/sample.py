import os
from enum import Enum
from typing import Optional

class Status(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class BaseEntity:
    def __init__(self, name: str):
        self.name = name

class User(BaseEntity):
    def __init__(self, user_id: str, name: str):
        super().__init__(name)
        self.user_id = user_id
        self.status = Status.ACTIVE

    def process(self) -> str:
        return self.helper_call(self.name)

    def helper_call(self, input_str: str) -> str:
        return f"processed_{input_str}"
