"""Data models for Move Music save file entries."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DifficultyScores:
    easy: int = 0
    normal: int = 0
    hard: int = 0
    expert: int = 0

    def to_dict(self) -> Dict:
        return {
            "easy": self.easy,
            "normal": self.normal,
            "hard": self.hard,
            "expert": self.expert,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DifficultyScores":
        return cls(
            easy=int(data.get("easy", 0)),
            normal=int(data.get("normal", 0)),
            hard=int(data.get("hard", 0)),
            expert=int(data.get("expert", 0)),
        )


@dataclass
class SongEntry:
    id: str = ""
    title: str = "Unknown"
    artist: str = "Unknown"
    unlocked: bool = False
    high_score: int = 0
    accuracy: float = 0.0
    plays: int = 0
    difficulty_scores: DifficultyScores = field(default_factory=DifficultyScores)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "unlocked": self.unlocked,
            "high_score": self.high_score,
            "accuracy": round(self.accuracy, 4),
            "plays": self.plays,
            "difficulty_scores": self.difficulty_scores.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SongEntry":
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "Unknown")),
            artist=str(data.get("artist", "Unknown")),
            unlocked=bool(data.get("unlocked", False)),
            high_score=int(data.get("high_score", 0)),
            accuracy=float(data.get("accuracy", 0.0)),
            plays=int(data.get("plays", 0)),
            difficulty_scores=DifficultyScores.from_dict(
                data.get("difficulty_scores", {})
            ),
        )


@dataclass
class PlayerData:
    username: str = "Player"
    level: int = 1
    xp: int = 0
    total_score: int = 0

    def to_dict(self) -> Dict:
        return {
            "username": self.username,
            "level": self.level,
            "xp": self.xp,
            "total_score": self.total_score,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PlayerData":
        return cls(
            username=str(data.get("username", "Player")),
            level=int(data.get("level", 1)),
            xp=int(data.get("xp", 0)),
            total_score=int(data.get("total_score", 0)),
        )


@dataclass
class GameSettings:
    music_volume: float = 1.0
    sfx_volume: float = 1.0
    comfort_mode: bool = False
    controller_haptics: bool = True
    hand_tracking: bool = False
    mirror_display: bool = True

    def to_dict(self) -> Dict:
        return {
            "music_volume": round(self.music_volume, 2),
            "sfx_volume": round(self.sfx_volume, 2),
            "comfort_mode": self.comfort_mode,
            "controller_haptics": self.controller_haptics,
            "hand_tracking": self.hand_tracking,
            "mirror_display": self.mirror_display,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "GameSettings":
        return cls(
            music_volume=float(data.get("music_volume", 1.0)),
            sfx_volume=float(data.get("sfx_volume", 1.0)),
            comfort_mode=bool(data.get("comfort_mode", False)),
            controller_haptics=bool(data.get("controller_haptics", True)),
            hand_tracking=bool(data.get("hand_tracking", False)),
            mirror_display=bool(data.get("mirror_display", True)),
        )
