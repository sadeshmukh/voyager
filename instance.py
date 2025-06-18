from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional
import yaml
import os
import time


class GameState(Enum):
    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class PlayerState(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass
class Player:
    user_id: str
    state: PlayerState = PlayerState.ACTIVE
    score: int = 0


class Instance:
    def __init__(self, channel_id: str, name: str):
        self.channel_id = channel_id
        self.name = name
        self.players: Dict[str, Player] = {}
        self.state = GameState.WAITING
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def add_player(self, user_id: str) -> None:
        if user_id not in self.players:
            self.players[user_id] = Player(user_id=user_id)

    def remove_player(self, user_id: str) -> None:
        if user_id in self.players:
            del self.players[user_id]

    def start_game(self) -> Dict[str, Any]:
        if len(self.players) < 1:
            raise ValueError("Not enough players to start")

        self.state = GameState.IN_PROGRESS
        self.start_time = time.time()
        return self.get_game_state()

    def get_game_state(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "player_count": len(self.players),
            "time_elapsed": f"{(time.time() - (self.start_time or time.time())):.1f}s"
            if self.start_time
            else "0s",
        }

    def end_game(self, success: bool = True) -> None:
        self.state = GameState.COMPLETED if success else GameState.FAILED
        self.end_time = time.time()
