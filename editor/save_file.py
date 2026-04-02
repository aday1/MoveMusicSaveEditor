"""Save file read/write logic for Move Music save data."""

from __future__ import annotations
import json
import os
from typing import List, Optional

from .models import PlayerData, SongEntry, GameSettings

SAVE_FORMAT_VERSION = "1.0"

DEFAULT_SONGS: List[dict] = [
    {"id": "song_001", "title": "Electric Pulse", "artist": "MoveBeats", "unlocked": True},
    {"id": "song_002", "title": "Neon Drift", "artist": "Synthwave Collective", "unlocked": False},
    {"id": "song_003", "title": "Bass Drop Boogie", "artist": "DJ Vortex", "unlocked": False},
    {"id": "song_004", "title": "Stellar Groove", "artist": "Cosmic Rhythm", "unlocked": False},
    {"id": "song_005", "title": "Retro Runners", "artist": "8-Bit Masters", "unlocked": False},
    {"id": "song_006", "title": "Midnight Flow", "artist": "Luna & The Echoes", "unlocked": False},
    {"id": "song_007", "title": "Hyper Rush", "artist": "Overdrive", "unlocked": False},
    {"id": "song_008", "title": "Sunrise Samba", "artist": "Tropical Beats", "unlocked": False},
]


class SaveFile:
    """Represents a Move Music save file."""

    def __init__(self) -> None:
        self.version: str = SAVE_FORMAT_VERSION
        self.player: PlayerData = PlayerData()
        self.songs: List[SongEntry] = [SongEntry.from_dict(s) for s in DEFAULT_SONGS]
        self.settings: GameSettings = GameSettings()
        self._path: Optional[str] = None

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "player": self.player.to_dict(),
            "songs": [s.to_dict() for s in self.songs],
            "settings": self.settings.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SaveFile":
        sf = cls.__new__(cls)
        sf.version = str(data.get("version", SAVE_FORMAT_VERSION))
        sf.player = PlayerData.from_dict(data.get("player", {}))
        sf.songs = [SongEntry.from_dict(s) for s in data.get("songs", [])]
        sf.settings = GameSettings.from_dict(data.get("settings", {}))
        sf._path = None
        return sf

    def load(self, path: str) -> None:
        """Load save data from a JSON file at *path*."""
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        loaded = SaveFile.from_dict(data)
        self.version = loaded.version
        self.player = loaded.player
        self.songs = loaded.songs
        self.settings = loaded.settings
        self._path = path

    def save(self, path: Optional[str] = None) -> None:
        """Write save data to *path* (or the previously loaded path)."""
        target = path or self._path
        if not target:
            raise ValueError("No save path provided.")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        self._path = target

    @property
    def path(self) -> Optional[str]:
        return self._path

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def unlock_all_songs(self) -> None:
        for song in self.songs:
            song.unlocked = True

    def reset_scores(self) -> None:
        for song in self.songs:
            song.high_score = 0
            song.accuracy = 0.0
            song.plays = 0
            song.difficulty_scores.easy = 0
            song.difficulty_scores.normal = 0
            song.difficulty_scores.hard = 0
            song.difficulty_scores.expert = 0

    def get_song_by_id(self, song_id: str) -> Optional[SongEntry]:
        for song in self.songs:
            if song.id == song_id:
                return song
        return None

    # ------------------------------------------------------------------
    # Factory: create a fresh default save file on disk
    # ------------------------------------------------------------------

    @classmethod
    def new_default(cls, path: Optional[str] = None) -> "SaveFile":
        sf = cls()
        if path:
            sf.save(path)
        return sf
