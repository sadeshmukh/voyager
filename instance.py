from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional, Union, Callable

from ai import verify
from config import SCORING, DEFAULT_LIVES, DEFAULT_TIME_LIMIT

# import typing as t
import time
import random


# region Enums
class GameState(Enum):
    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class GamePhase(Enum):
    """High-level game flow phases"""

    INTRO = "intro"
    MAIN_ROUND = "main_round"
    OUTRO = "outro"


class GameType(Enum):
    """Different types of games/challenges that can be spawned"""

    QUICK_MATH = "quick_math"
    TRIVIA = "trivia"
    SPEED_CHALLENGE = "speed_challenge"
    RIDDLE = "riddle"
    MEMORY_GAME = "memory_game"
    COLLABORATIVE = "collaborative"
    CUSTOM = "custom"
    TEXT_MODIFICATION = "text_modification"
    EMOJI_CHALLENGE = "emoji_challenge"


# class EliminationMode(Enum):
#     didn't work so well earlier, probably won't work well now

#     SINGLE_LIFE = "single_life"
#     MULTIPLE_LIVES = "multiple_lives"
#     SCORE_BASED = "score_based"
#     COLLABORATIVE_FAILURE = "collaborative_failure"


class PlayerState(Enum):
    ACTIVE = "active"
    WINNER = "winner"


@dataclass
class Challenge:
    """Represents a specific game challenge"""

    challenge_type: GameType
    question: str
    correct_answer: Optional[Union[str, List[str]]] = None
    time_limit: int = DEFAULT_TIME_LIMIT  # seconds
    metadata: Dict[str, Any] = None  # certain challenges require metadata

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# endregion


def get_random_game_type(exclude: List[GameType] = None) -> GameType:
    available = list(GameType)
    if exclude:
        available = [gt for gt in available if gt not in exclude]
    return random.choice(available) if available else GameType.TRIVIA


@dataclass
class Player:
    user_id: str
    state: PlayerState = PlayerState.ACTIVE
    score: int = 0
    lives: int = DEFAULT_LIVES
    current_answer: Optional[str] = None
    response_time: Optional[float] = None
    previous_message_ts: Optional[str] = None  # track previous answer por unreact


@dataclass
class GameConfig:
    """Configuration for different player counts and game types"""

    player_count: int
    main_rounds: int = 5
    available_game_types: List[GameType] = None

    def __post_init__(self):
        if self.available_game_types is None:
            self.available_game_types = [
                GameType.QUICK_MATH,
                GameType.TRIVIA,
                GameType.SPEED_CHALLENGE,
                GameType.RIDDLE,
                GameType.MEMORY_GAME,
                GameType.TEXT_MODIFICATION,
                GameType.EMOJI_CHALLENGE,
            ]
            if self.player_count >= 5:
                self.available_game_types.append(GameType.COLLABORATIVE)

    def get_random_game_type(self, exclude: List[GameType] = None) -> GameType:
        """Get a random game type, optionally excluding certain types"""
        available = self.available_game_types.copy()
        if exclude:
            available = [gt for gt in available if gt not in exclude]
        return random.choice(available) if available else GameType.TRIVIA


class Instance:
    def __init__(self, channel_id: str, name: str, config: Optional[GameConfig] = None):
        self.channel_id = channel_id
        self.name = name
        self.players: Dict[str, Player] = {}
        self.state = GameState.WAITING
        self.current_phase = GamePhase.INTRO
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.config = config

        self.current_round = 0
        self.current_challenge: Optional[Challenge] = None
        self.round_start_time: Optional[float] = None
        self.recent_game_types: List[GameType] = []
        self.previous_leader: Optional[str] = None

        # callback here is important for eventual custom challenges
        self.challenge_generator: Optional[Callable[[GameType], Challenge]] = None

    def set_challenge_generator(
        self, generator: Callable[[GameType], Challenge]
    ) -> None:
        """Set the challenge generator callback"""
        self.challenge_generator = generator

    def add_player(self, user_id: str) -> None:
        if user_id not in self.players:
            self.players[user_id] = Player(user_id=user_id)

    def remove_player(self, user_id: str) -> None:
        if user_id in self.players:
            del self.players[user_id]

    def start_game(self, config: Optional[GameConfig] = None) -> Dict[str, Any]:
        if len(self.players) < 1:
            raise ValueError("Not enough players to start")

        self.state = GameState.IN_PROGRESS
        self.start_time = time.time()

        if config:
            self.config = config
        elif not self.config:
            self.config = GameConfig(len(self.players))

        return self.get_game_state()

    def get_game_state(self) -> Dict[str, Any]:
        active_players = sum(
            1 for p in self.players.values() if p.state == PlayerState.ACTIVE
        )

        return {
            "state": self.state.value,
            "phase": self.current_phase.value,
            "player_count": len(self.players),
            "active_players": active_players,
            "round": self.current_round,
            "current_challenge": self.current_challenge.challenge_type.value
            if self.current_challenge
            else None,
            "time_elapsed": f"{(time.time() - (self.start_time or time.time())):.1f}s"
            if self.start_time
            else "0s",
        }

    def end_game(self, success: bool = True) -> Dict[str, Any]:
        self.state = GameState.COMPLETED if success else GameState.FAILED
        self.end_time = time.time()

        if success:
            max_score = max((p.score for p in self.players.values()), default=0)
            for player in self.players.values():
                if player.score == max_score:
                    player.state = PlayerState.WINNER

        return self.get_final_results()

    def get_final_results(self) -> Dict[str, Any]:
        """Get final game results"""
        winners = [
            uid for uid, p in self.players.items() if p.state == PlayerState.WINNER
        ]
        player_scores = {uid: p.score for uid, p in self.players.items()}

        return {
            "winners": winners,
            "scores": player_scores,
            "total_rounds": self.current_round,
            "duration": (self.end_time or time.time())
            - (self.start_time or time.time()),
        }

    def start_main_round(self, game_type: Optional[GameType] = None) -> Challenge:
        if not self.config:
            raise ValueError("Game not started - no config available")

        self.current_phase = GamePhase.MAIN_ROUND
        self.current_round += 1

        if game_type is None:
            exclude = (
                self.recent_game_types[-2:] if len(self.recent_game_types) >= 2 else []
            )
            game_type = self.config.get_random_game_type(exclude=exclude)

        self.recent_game_types.append(game_type)
        if len(self.recent_game_types) > 5:
            self.recent_game_types.pop(0)

        if not self.challenge_generator:
            raise ValueError("No challenge generator set")

        self.current_challenge = self.challenge_generator(game_type)
        self.round_start_time = time.time()

        for player in self.players.values():
            player.current_answer = None
            player.response_time = None
            player.previous_message_ts = None

        return self.current_challenge

    def submit_answer(
        self, user_id: str, answer: str, message_ts: Optional[str] = None
    ) -> Optional[str]:
        """Submit answer for the current challenge"""
        if user_id in self.players:
            player = self.players[user_id]

            previous_ts = player.previous_message_ts

            player.current_answer = answer
            player.previous_message_ts = message_ts

            # record response time for speed challenges
            if (
                self.current_challenge
                and self.current_challenge.metadata.get("speed_based")
                and self.round_start_time
            ):
                player.response_time = time.time() - self.round_start_time

            return previous_ts
        return None

    def evaluate_current_challenge(self) -> Dict[str, Any]:
        """Evaluate the current challenge and return results"""
        if not self.current_challenge:
            return {"error": "No active challenge"}

        results = {
            "challenge_type": self.current_challenge.challenge_type.value,
            "correct_players": [],
            "failed_players": [],
        }

        players_to_evaluate = [
            user_id
            for user_id, player in self.players.items()
            if player.state == PlayerState.ACTIVE
        ]

        if self.current_challenge.metadata.get("speed_based"):
            valid_responses = []
            for user_id in players_to_evaluate:
                player = self.players[user_id]
                if player.current_answer and player.response_time:
                    valid_responses.append((user_id, player.response_time))

            valid_responses.sort(key=lambda x: x[1])

            # check first and validity of responses
            if valid_responses:
                winner_id = valid_responses[0][0]
                results["correct_players"] = [winner_id]
                results["failed_players"] = [uid for uid, _ in valid_responses[1:]]
                results["failed_players"].extend(
                    [
                        uid
                        for uid in players_to_evaluate
                        if uid not in [uid for uid, _ in valid_responses]
                    ]
                )
            else:
                results["failed_players"] = players_to_evaluate

        elif self.current_challenge.metadata.get("emoji_challenge"):
            expected_set = set(self.current_challenge.correct_answer or [])
            for user_id in players_to_evaluate:
                player = self.players[user_id]
                if player.current_answer:
                    user_set = set(player.current_answer.split())
                    if expected_set.issubset(user_set):
                        results["correct_players"].append(user_id)
                    else:
                        results["failed_players"].append(user_id)
                else:
                    results["failed_players"].append(user_id)
        else:
            correct_answers = self.current_challenge.correct_answer
            if isinstance(correct_answers, str):
                correct_answers = [correct_answers]

            for user_id in players_to_evaluate:
                player = self.players[user_id]
                if player.current_answer and correct_answers:
                    is_correct = False
                    for correct_answer in correct_answers:
                        if verify(player.current_answer, correct_answer):
                            is_correct = True
                            break

                    if is_correct:
                        results["correct_players"].append(user_id)
                    else:
                        results["failed_players"].append(user_id)
                else:
                    results["failed_players"].append(user_id)

        self._apply_challenge_results(results)

        return results

    def _apply_challenge_results(self, results: Dict[str, Any]) -> None:
        """Apply challenge results to player states"""
        for user_id in results["correct_players"]:
            if user_id in self.players:
                self.players[user_id].score += SCORING["correct_answer_points"]

    def check_leader_change(self) -> Optional[str]:
        """Check if there's a new leader and return their user_id"""
        if not self.players:
            return None

        current_leader = max(self.players.items(), key=lambda x: x[1].score)[0]

        if self.previous_leader != current_leader:
            old_leader = self.previous_leader
            self.previous_leader = current_leader
            return (
                current_leader if old_leader is not None else None
            )  # can't announce first one

        return None
