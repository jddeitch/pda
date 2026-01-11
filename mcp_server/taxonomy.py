"""
Loads taxonomy.yaml — the single source of truth for classification terms.

This module provides:
- Valid method values
- Valid voice values
- Valid category IDs
- Valid flag codes
- French/English labels for all terms
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml


# Paths
PROJECT_ROOT = Path(__file__).parent.parent
TAXONOMY_PATH = PROJECT_ROOT / "data" / "taxonomy.yaml"


class Taxonomy:
    """
    Loads and provides access to taxonomy.yaml data.

    Instantiated once at server startup and reused for all requests.
    """

    def __init__(self, taxonomy_path: Path = TAXONOMY_PATH):
        self._path = taxonomy_path
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load taxonomy from YAML file."""
        if not self._path.exists():
            raise FileNotFoundError(f"Taxonomy file not found: {self._path}")

        with open(self._path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    def reload(self) -> None:
        """Reload taxonomy from disk. Useful for development."""
        self._load()

    # --- Method ---

    @property
    def methods(self) -> list[str]:
        """Return list of valid method values."""
        return list(self._data.get("method", {}).keys())

    def get_method_label(self, method: str, lang: str = "fr") -> str:
        """Get localized label for a method."""
        method_data = self._data.get("method", {}).get(method, {})
        return method_data.get(lang, method)

    def get_method_definition(self, method: str) -> str:
        """Get definition for a method."""
        method_data = self._data.get("method", {}).get(method, {})
        return method_data.get("definition", "")

    def is_valid_method(self, method: str) -> bool:
        """Check if method value is valid."""
        return method in self.methods

    # --- Voice ---

    @property
    def voices(self) -> list[str]:
        """Return list of valid voice values."""
        return list(self._data.get("voice", {}).keys())

    def get_voice_label(self, voice: str, lang: str = "fr") -> str:
        """Get localized label for a voice."""
        voice_data = self._data.get("voice", {}).get(voice, {})
        return voice_data.get(lang, voice)

    def get_voice_definition(self, voice: str) -> str:
        """Get definition for a voice."""
        voice_data = self._data.get("voice", {}).get(voice, {})
        return voice_data.get("definition", "")

    def is_valid_voice(self, voice: str) -> bool:
        """Check if voice value is valid."""
        return voice in self.voices

    # --- Categories ---

    @property
    def categories(self) -> list[str]:
        """Return list of valid category IDs."""
        return list(self._data.get("categories", {}).keys())

    def get_category_label(self, category: str, lang: str = "fr") -> str:
        """Get localized label for a category."""
        cat_data = self._data.get("categories", {}).get(category, {})
        return cat_data.get(lang, category)

    def get_category_definition(self, category: str) -> str:
        """Get definition for a category."""
        cat_data = self._data.get("categories", {}).get(category, {})
        return cat_data.get("definition", "")

    def is_valid_category(self, category: str) -> bool:
        """Check if category ID is valid."""
        return category in self.categories

    # --- Flags ---

    def get_all_flag_codes(self) -> set[str]:
        """Return set of all valid flag codes."""
        codes: set[str] = set()
        flags = self._data.get("processing_flags", {})

        for category_key, category_data in flags.items():
            if category_key == "automated":
                # automated has nested blocking/warning
                for severity in ("blocking", "warning"):
                    for code in category_data.get(severity, {}).keys():
                        codes.add(code)
            else:
                # content, access, classification, relevance
                for code in category_data.keys():
                    codes.add(code)

        return codes

    def is_valid_flag(self, code: str) -> bool:
        """Check if flag code is valid."""
        return code in self.get_all_flag_codes()

    def get_blocking_flags(self) -> set[str]:
        """Return set of blocking flag codes (SENTMIS, WORDMIS)."""
        automated = self._data.get("processing_flags", {}).get("automated", {})
        return set(automated.get("blocking", {}).keys())

    def get_warning_flags(self) -> set[str]:
        """Return set of warning flag codes."""
        automated = self._data.get("processing_flags", {}).get("automated", {})
        return set(automated.get("warning", {}).keys())

    def get_flag_description(self, code: str) -> str:
        """Get description for a flag code."""
        flags = self._data.get("processing_flags", {})

        for category_key, category_data in flags.items():
            if category_key == "automated":
                for severity in ("blocking", "warning"):
                    if code in category_data.get(severity, {}):
                        return category_data[severity][code].get("description", "")
            else:
                if code in category_data:
                    return category_data[code].get("description", "")

        return ""

    # --- Summary for Claude ---

    def get_taxonomy_summary(self) -> dict[str, Any]:
        """
        Return summary of valid taxonomy values.

        Included in get_next_article() response so Claude always has
        fresh taxonomy data (prevents context decay).
        """
        return {
            "methods": [
                {"id": m, "label_fr": self.get_method_label(m, "fr")}
                for m in self.methods
            ],
            "voices": [
                {"id": v, "label_fr": self.get_voice_label(v, "fr")}
                for v in self.voices
            ],
            "categories": [
                {"id": c, "label_fr": self.get_category_label(c, "fr")}
                for c in self.categories
            ],
        }


# Module-level singleton for convenience
_taxonomy: Optional[Taxonomy] = None


def get_taxonomy() -> Taxonomy:
    """Get the taxonomy singleton, loading it if necessary."""
    global _taxonomy
    if _taxonomy is None:
        _taxonomy = Taxonomy()
    return _taxonomy
