"""Tests for the Move Music save editor (no GUI required)."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from editor.models import PlayerData, SongEntry, GameSettings, DifficultyScores
from editor.save_file import SaveFile


# ── Model round-trips ─────────────────────────────────────────────────────────

def test_player_data_roundtrip():
    pd = PlayerData(username="DanceKing", level=42, xp=99500, total_score=1234567)
    assert PlayerData.from_dict(pd.to_dict()) == pd


def test_song_entry_roundtrip():
    song = SongEntry(
        id="song_003",
        title="Bass Drop Boogie",
        artist="DJ Vortex",
        unlocked=True,
        high_score=250000,
        accuracy=0.9750,
        plays=7,
        difficulty_scores=DifficultyScores(easy=1000, normal=5000, hard=100000, expert=250000),
    )
    assert SongEntry.from_dict(song.to_dict()) == song


def test_game_settings_roundtrip():
    gs = GameSettings(
        music_volume=0.75,
        sfx_volume=0.5,
        comfort_mode=True,
        controller_haptics=False,
        hand_tracking=True,
        mirror_display=False,
    )
    assert GameSettings.from_dict(gs.to_dict()) == gs


def test_difficulty_scores_roundtrip():
    ds = DifficultyScores(easy=100, normal=200, hard=300, expert=400)
    assert DifficultyScores.from_dict(ds.to_dict()) == ds


# ── SaveFile defaults ─────────────────────────────────────────────────────────

def test_new_save_has_default_songs():
    sf = SaveFile()
    assert len(sf.songs) > 0
    # First song should be unlocked by default
    assert sf.songs[0].unlocked is True


def test_new_save_default_player():
    sf = SaveFile()
    assert sf.player.username == "Player"
    assert sf.player.level == 1


# ── SaveFile helpers ──────────────────────────────────────────────────────────

def test_unlock_all_songs():
    sf = SaveFile()
    sf.unlock_all_songs()
    assert all(s.unlocked for s in sf.songs)


def test_reset_scores():
    sf = SaveFile()
    sf.songs[0].high_score = 99999
    sf.songs[0].accuracy = 0.99
    sf.songs[0].plays = 5
    sf.reset_scores()
    assert sf.songs[0].high_score == 0
    assert sf.songs[0].accuracy == 0.0
    assert sf.songs[0].plays == 0


def test_get_song_by_id_found():
    sf = SaveFile()
    song = sf.get_song_by_id("song_001")
    assert song is not None
    assert song.id == "song_001"


def test_get_song_by_id_not_found():
    sf = SaveFile()
    assert sf.get_song_by_id("nonexistent") is None


# ── SaveFile persistence ──────────────────────────────────────────────────────

def test_save_and_load_roundtrip():
    sf = SaveFile()
    sf.player.username = "TestUser"
    sf.player.level = 10
    sf.player.xp = 5000
    sf.unlock_all_songs()
    sf.songs[0].high_score = 123456
    sf.settings.comfort_mode = True

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = tmp.name

    try:
        sf.save(path)
        sf2 = SaveFile()
        sf2.load(path)
        assert sf2.player.username == "TestUser"
        assert sf2.player.level == 10
        assert sf2.player.xp == 5000
        assert sf2.songs[0].high_score == 123456
        assert sf2.settings.comfort_mode is True
        assert all(s.unlocked for s in sf2.songs)
    finally:
        os.unlink(path)


def test_save_file_is_valid_json():
    sf = SaveFile()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = tmp.name
    try:
        sf.save(path)
        with open(path) as fh:
            data = json.load(fh)
        assert "version" in data
        assert "player" in data
        assert "songs" in data
        assert "settings" in data
    finally:
        os.unlink(path)


def test_save_without_path_raises():
    sf = SaveFile()
    with pytest.raises(ValueError):
        sf.save()


def test_new_default_creates_file():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = tmp.name
    os.unlink(path)
    try:
        sf = SaveFile.new_default(path)
        assert os.path.exists(path)
        assert sf.path == path
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_load_sets_path():
    sf = SaveFile()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = tmp.name
    try:
        sf.save(path)
        sf2 = SaveFile()
        sf2.load(path)
        assert sf2.path == path
    finally:
        os.unlink(path)
