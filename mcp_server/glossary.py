"""
Glossary matching with variant detection.

Per D12 of the plan:
- Match exact terms (case-insensitive)
- Match hyphenation variants ("demand avoidance" <-> "demand-avoidance")
- Match abbreviations if defined
- Return {en_term: fr_term} for all matches found

The glossary is loaded from data/glossary.yaml and indexed for fast lookup.
Terms may have optional fields:
- fr_alt: list of acceptable French variants
- abbreviation: e.g., "DA" for "demand avoidance"
- note: translator guidance (not used for matching)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# --- Paths ---

PROJECT_ROOT = Path(__file__).parent.parent
GLOSSARY_PATH = PROJECT_ROOT / "data" / "glossary.yaml"


class Glossary:
    """
    Loads and provides access to glossary.yaml for term matching.

    The glossary is structured with category keys (core_terms, autism_terms, etc.)
    each containing a list of term entries with en/fr pairs.
    """

    def __init__(self, glossary_path: Path = GLOSSARY_PATH):
        self._path = glossary_path
        self._data: dict[str, Any] = {}
        self._index: dict[str, dict[str, Any]] = {}  # Normalized EN term -> entry
        self._version: str = "unknown"
        self._load()

    def _load(self) -> None:
        """Load glossary from YAML file and build index."""
        if not self._path.exists():
            raise FileNotFoundError(f"Glossary file not found: {self._path}")

        with open(self._path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

        # Extract version if present
        self._version = self._data.get("version", "unknown")

        # Build index for fast lookup
        self._build_index()

    def _build_index(self) -> None:
        """
        Build lookup index from glossary data.

        Index keys are normalized English terms (lowercase, spaces normalized).
        Index values are the full entry dicts with en, fr, and optional fields.
        """
        self._index = {}

        for category_key, terms in self._data.items():
            # Skip non-list values (like 'version' or 'note')
            if not isinstance(terms, list):
                continue

            # Skip sections that are just string lists (like 'keep_english')
            # These don't have en/fr translations
            for entry in terms:
                if not isinstance(entry, dict):
                    # Plain string (e.g., "DSM-5" in keep_english) — skip
                    continue

                # Get English term
                en_term = entry.get("en")
                if not en_term:
                    continue

                # Build entry with normalized data
                processed_entry = {
                    "en": en_term,
                    "fr": entry.get("fr", ""),
                    "fr_alt": entry.get("fr_alt", []),
                    "abbreviation": entry.get("abbreviation"),
                    "note": entry.get("note"),
                    "category": category_key,
                }

                # Index by normalized term
                normalized = self._normalize(en_term)
                self._index[normalized] = processed_entry

                # Also index by hyphenation variant
                hyphen_variant = self._hyphen_variant(normalized)
                if hyphen_variant != normalized:
                    self._index[hyphen_variant] = processed_entry

                # Also index by abbreviation if present
                if processed_entry["abbreviation"]:
                    abbr_normalized = self._normalize(processed_entry["abbreviation"])
                    self._index[abbr_normalized] = processed_entry

        logger.info(f"Glossary loaded: {len(self._index)} indexed terms from {self._path}")

    def _normalize(self, text: str) -> str:
        """Normalize text for matching: lowercase, normalize spaces."""
        return " ".join(text.lower().split())

    def _hyphen_variant(self, text: str) -> str:
        """Generate hyphenation variant: spaces <-> hyphens."""
        if " " in text:
            return text.replace(" ", "-")
        elif "-" in text:
            return text.replace("-", " ")
        return text

    @property
    def version(self) -> str:
        """Return glossary version string."""
        return self._version

    def reload(self) -> None:
        """Reload glossary from disk. Useful for development."""
        self._load()

    def find_terms_in_text(self, text: str) -> dict[str, str]:
        """
        Find glossary terms that appear in the given text.

        Per D12:
        1. Normalize text: lowercase, normalize whitespace
        2. For each glossary entry, check:
           a. Exact match (case-insensitive)
           b. Hyphenation variant
           c. Abbreviation if defined

        Args:
            text: Source text (English) to search

        Returns:
            Dict mapping English term to French translation for all matches found.
            Example: {"demand avoidance": "évitement des demandes"}
        """
        normalized_text = self._normalize(text)
        matches: dict[str, str] = {}

        for normalized_term, entry in self._index.items():
            # Check if term appears in text
            # Use word boundary regex to avoid partial matches
            # e.g., "autism" should not match inside "autistic"
            pattern = r'\b' + re.escape(normalized_term) + r'\b'

            if re.search(pattern, normalized_text):
                en_term = entry["en"]
                fr_term = entry["fr"]

                # Only add if we haven't already matched this term
                # (avoids duplicates from hyphen variants)
                if en_term not in matches:
                    matches[en_term] = fr_term

        return matches

    def verify_terms(
        self,
        source_text: str,
        translation: str
    ) -> list[str]:
        """
        Verify that expected glossary terms appear in translation.

        Per D12: Returns list of missing terms for TERMMIS flag.
        Accepts primary fr term OR any fr_alt variant.

        Args:
            source_text: Original English text
            translation: French translation to verify

        Returns:
            List of missing terms in format "en_term -> fr_term"
        """
        expected = self.find_terms_in_text(source_text)
        missing: list[str] = []

        normalized_translation = self._normalize(translation)

        for en_term, fr_primary in expected.items():
            # Get entry to check for alternatives
            normalized_en = self._normalize(en_term)
            entry = self._index.get(normalized_en, {})

            # Build list of acceptable French terms
            acceptable = [fr_primary.lower()]
            fr_alts = entry.get("fr_alt", [])
            if isinstance(fr_alts, list):
                acceptable.extend(alt.lower() for alt in fr_alts)

            # Check if ANY acceptable variant appears in translation
            found = False
            for variant in acceptable:
                if variant and variant in normalized_translation:
                    found = True
                    break

            if not found:
                missing.append(f"{en_term} -> {fr_primary}")

        return missing

    def get_entry(self, en_term: str) -> dict[str, Any] | None:
        """Get full glossary entry for an English term."""
        normalized = self._normalize(en_term)
        return self._index.get(normalized)

    def get_all_terms(self) -> list[dict[str, Any]]:
        """Get all glossary entries (deduplicated)."""
        # Deduplicate by English term (hyphen variants point to same entry)
        seen_en: set[str] = set()
        result: list[dict[str, Any]] = []

        for entry in self._index.values():
            en_term = entry["en"]
            if en_term not in seen_en:
                seen_en.add(en_term)
                result.append(entry)

        return result


# --- Module-level singleton ---

_glossary: Glossary | None = None


def get_glossary() -> Glossary:
    """Get the glossary singleton, loading it if necessary."""
    global _glossary
    if _glossary is None:
        _glossary = Glossary()
    return _glossary


def get_glossary_version() -> str:
    """Return version string from glossary.yaml header."""
    return get_glossary().version


def find_glossary_terms_in_text(text: str) -> dict[str, str]:
    """
    Find glossary terms in text.

    Convenience function that uses the singleton glossary.
    """
    return get_glossary().find_terms_in_text(text)


def verify_glossary_terms(source_text: str, translation: str) -> list[str]:
    """
    Verify glossary terms appear in translation.

    Convenience function that uses the singleton glossary.
    Returns list of missing terms for TERMMIS flag.
    """
    return get_glossary().verify_terms(source_text, translation)
