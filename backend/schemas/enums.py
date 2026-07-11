from enum import Enum


class RoleEnum(str, Enum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


class VoteEnum(str, Enum):
    up = "up"
    down = "down"
