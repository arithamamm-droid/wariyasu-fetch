"""Genres to fetch (generated from genres.json by the private site repo's sync script; do not edit here)."""
import json
from pathlib import Path

GENRES = json.loads((Path(__file__).resolve().parent / "genres.json").read_text(encoding="utf-8"))
ACTIVE = GENRES
