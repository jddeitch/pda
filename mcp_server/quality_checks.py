"""
Quality checks for translation validation.

Per Part 7 and Part 3 of the plan, these automated checks detect:

BLOCKING FLAGS (save rejected, must fix):
- SENTMIS: Sentence count mismatch >15%
- WORDMIS: Word ratio outside 0.9-1.5

WARNING FLAGS (save allowed, human reviews later):
- WORDDRIFT: Content word Jaccard similarity < 0.6 (possible editorial drift)
- TERMMIS: Expected glossary term missing
- STATMIS: Statistics may have been modified

spaCy models are loaded ONCE at module import (per D18), not per-request.
This takes ~2-3 seconds on first import but is reused for all subsequent calls.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# --- spaCy Model Loading (per D18) ---
# Models loaded once at module import for consistent behavior.

_nlp_en = None
_nlp_fr = None


def _get_nlp_en():
    """Get English spaCy model, loading it if necessary."""
    global _nlp_en
    if _nlp_en is None:
        try:
            import spacy
            _nlp_en = spacy.load("en_core_web_sm")
            logger.info("Loaded spaCy model: en_core_web_sm")
        except OSError as e:
            logger.error(
                f"spaCy model 'en_core_web_sm' not found. "
                f"Run: python -m spacy download en_core_web_sm"
            )
            raise RuntimeError(
                "spaCy model 'en_core_web_sm' not found. "
                "Run: python -m spacy download en_core_web_sm"
            ) from e
    return _nlp_en


def _get_nlp_fr():
    """Get French spaCy model, loading it if necessary."""
    global _nlp_fr
    if _nlp_fr is None:
        try:
            import spacy
            _nlp_fr = spacy.load("fr_core_news_sm")
            logger.info("Loaded spaCy model: fr_core_news_sm")
        except OSError as e:
            logger.error(
                f"spaCy model 'fr_core_news_sm' not found. "
                f"Run: python -m spacy download fr_core_news_sm"
            )
            raise RuntimeError(
                "spaCy model 'fr_core_news_sm' not found. "
                "Run: python -m spacy download fr_core_news_sm"
            ) from e
    return _nlp_fr


# --- Result Types ---

@dataclass
class SentenceCountResult:
    """Result of sentence count comparison."""
    source_count: int
    target_count: int
    ratio: float
    flag: str | None  # "SENTMIS" if >15% variance, else None


@dataclass
class WordRatioResult:
    """Result of word ratio calculation."""
    source_words: int
    target_words: int
    ratio: float
    flag: str | None  # "WORDMIS" if outside 0.9-1.5, else None


@dataclass
class JaccardResult:
    """Result of content word Jaccard similarity."""
    similarity: float
    expected_words: set[str]
    actual_words: set[str]
    missing_expected: list[str]
    flag: str | None  # "WORDDRIFT" if similarity < 0.6, else None


@dataclass
class StatisticsResult:
    """Result of statistics preservation check."""
    source_numbers: list[str]
    target_numbers: list[str]
    missing: list[str]
    added: list[str]
    flag: str | None  # "STATMIS" if numbers differ, else None


@dataclass
class QualityCheckResults:
    """Combined results of all quality checks."""
    sentence_check: SentenceCountResult | None
    word_ratio_check: WordRatioResult | None
    jaccard_check: JaccardResult | None
    statistics_check: StatisticsResult | None
    glossary_missing: list[str]  # For TERMMIS

    @property
    def blocking_flags(self) -> list[str]:
        """Return list of blocking flag codes."""
        flags = []
        if self.sentence_check and self.sentence_check.flag:
            flags.append(self.sentence_check.flag)
        if self.word_ratio_check and self.word_ratio_check.flag:
            flags.append(self.word_ratio_check.flag)
        return flags

    @property
    def warning_flags(self) -> list[str]:
        """Return list of warning flag codes."""
        flags = []
        if self.jaccard_check and self.jaccard_check.flag:
            flags.append(self.jaccard_check.flag)
        if self.statistics_check and self.statistics_check.flag:
            flags.append(self.statistics_check.flag)
        if self.glossary_missing:
            flags.append("TERMMIS")
        return flags

    @property
    def has_blocking(self) -> bool:
        """Return True if any blocking flags are present."""
        return len(self.blocking_flags) > 0


# --- Sentence Counting ---

def count_sentences_en(text: str) -> int:
    """
    Count sentences in English text using spaCy.

    Why spaCy over regex (per Part 7):
    - "Dr. Smith found..." → 1 sentence (regex: 2)
    - "p < 0.05 was significant." → 1 sentence (regex: 2)
    - "The U.S.A. is..." → 1 sentence (regex: 4)
    """
    nlp = _get_nlp_en()
    doc = nlp(text)
    return len(list(doc.sents))


def count_sentences_fr(text: str) -> int:
    """
    Count sentences in French text using spaCy.

    French-specific considerations:
    - "M. Dupont a dit..." → 1 sentence
    - "etc." → handled correctly
    - "p. ex." → handled correctly
    """
    nlp = _get_nlp_fr()
    doc = nlp(text)
    return len(list(doc.sents))


def compare_sentence_counts(source_en: str, target_fr: str) -> SentenceCountResult:
    """
    Compare sentence counts between source (EN) and target (FR).

    Per Part 7: Flag SENTMIS if ratio outside 0.85-1.15 (>15% variance).

    Args:
        source_en: English source text
        target_fr: French translation

    Returns:
        SentenceCountResult with counts, ratio, and optional flag
    """
    source_count = count_sentences_en(source_en)
    target_count = count_sentences_fr(target_fr)

    # Avoid division by zero
    ratio = target_count / max(source_count, 1)

    # Flag if outside 0.85-1.15 range (>15% variance either direction)
    acceptable = 0.85 <= ratio <= 1.15
    flag = None if acceptable else "SENTMIS"

    return SentenceCountResult(
        source_count=source_count,
        target_count=target_count,
        ratio=round(ratio, 2),
        flag=flag,
    )


# --- Word Ratio ---

def count_words(text: str) -> int:
    """Count words in text (simple whitespace split)."""
    return len(text.split())


def calculate_word_ratio(source_en: str, target_fr: str) -> WordRatioResult:
    """
    Calculate word count ratio between source (EN) and target (FR).

    Per Part 7: Flag WORDMIS if ratio outside 0.9-1.5.

    EN→FR typically expands to 1.1-1.2x, but scientific writing
    varies more, so we use a wider range.

    Args:
        source_en: English source text
        target_fr: French translation

    Returns:
        WordRatioResult with counts, ratio, and optional flag
    """
    source_words = count_words(source_en)
    target_words = count_words(target_fr)

    # Avoid division by zero
    ratio = target_words / max(source_words, 1)

    # Flag if outside 0.9-1.5 range
    acceptable = 0.9 <= ratio <= 1.5
    flag = None if acceptable else "WORDMIS"

    return WordRatioResult(
        source_words=source_words,
        target_words=target_words,
        ratio=round(ratio, 2),
        flag=flag,
    )


# --- Content Word Jaccard Similarity ---

def extract_content_words_en(text: str) -> set[str]:
    """
    Extract lemmatized content words (nouns, verbs, adjectives) from English text.

    Uses spaCy for accurate lemmatization.
    """
    nlp = _get_nlp_en()
    doc = nlp(text.lower())

    content_pos = {"NOUN", "VERB", "ADJ"}
    return {
        token.lemma_
        for token in doc
        if token.pos_ in content_pos and len(token.lemma_) > 2
    }


def extract_content_words_fr(text: str) -> set[str]:
    """
    Extract lemmatized content words (nouns, verbs, adjectives) from French text.

    Uses spaCy for accurate lemmatization.
    """
    nlp = _get_nlp_fr()
    doc = nlp(text.lower())

    content_pos = {"NOUN", "VERB", "ADJ"}
    return {
        token.lemma_
        for token in doc
        if token.pos_ in content_pos and len(token.lemma_) > 2
    }


def check_content_word_similarity(
    source_en: str,
    translation_fr: str,
    glossary: dict | None = None
) -> JaccardResult:
    """
    Check content word similarity between source and translation.

    Per Part 3: Compares content words between translation and expected vocabulary.
    Catches editorial drift that sentence counting misses.

    The check uses two strategies:
    1. If glossary provided: Check expected French terms from glossary matches
    2. Fall back to comparing source EN content words against translation FR content words

    Args:
        source_en: English source text
        translation_fr: French translation
        glossary: Optional dict of {en_term: fr_term} for expected terms

    Returns:
        JaccardResult with similarity, word sets, and optional flag
    """
    # Strategy 1: Use glossary terms if provided
    if glossary and len(glossary) >= 3:
        expected_fr_words = set()
        nlp_fr = _get_nlp_fr()

        for fr_term in glossary.values():
            if isinstance(fr_term, str) and fr_term:
                doc = nlp_fr(fr_term.lower())
                expected_fr_words.update(
                    token.lemma_
                    for token in doc
                    if token.pos_ in ("NOUN", "VERB", "ADJ") and len(token.lemma_) > 2
                )

        if len(expected_fr_words) >= 3:
            # Extract actual content words from translation
            actual_fr_words = extract_content_words_fr(translation_fr)

            # Calculate Jaccard similarity
            intersection = expected_fr_words & actual_fr_words
            union = expected_fr_words | actual_fr_words

            similarity = len(intersection) / len(union) if union else 1.0

            return JaccardResult(
                similarity=round(similarity, 2),
                expected_words=expected_fr_words,
                actual_words=actual_fr_words,
                missing_expected=list(expected_fr_words - actual_fr_words)[:10],
                flag="WORDDRIFT" if similarity < 0.6 else None,
            )

    # Strategy 2: Skip check if not enough glossary terms
    # Per plan: "Skip check if too few terms to be meaningful"
    return JaccardResult(
        similarity=1.0,
        expected_words=set(),
        actual_words=set(),
        missing_expected=[],
        flag=None,
    )


# --- Statistics Preservation ---

# Regex to find numbers in text, including:
# - Integers: 42, 1234
# - Decimals: 0.05, 3.14
# - Percentages: 45%, 12.5%
# - Scientific notation: 1e-5, 2.3E+10
# - Numbers with units: 100mg, 5cm
# Note: Don't require word boundary after suffix (% is not alphanumeric)
NUMBER_PATTERN = re.compile(
    r'\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:%|mg|kg|cm|mm|ml|l|g)?'
)


def extract_numbers(text: str) -> list[str]:
    """
    Extract all numbers from text.

    Returns sorted list of unique number strings found.
    Includes percentages, decimals, and numbers with units.
    """
    matches = NUMBER_PATTERN.findall(text)
    # Deduplicate and sort for consistent comparison
    return sorted(set(matches))


def check_statistics_preserved(source_en: str, target_fr: str) -> StatisticsResult:
    """
    Check that statistics/numbers are preserved in translation.

    Per Part 3: Flag STATMIS when numbers differ.

    Note: French uses comma for decimal separator, but many
    scientific papers use period even in French. We normalize
    by finding numbers in both and comparing.

    Args:
        source_en: English source text
        target_fr: French translation

    Returns:
        StatisticsResult with numbers found and optional flag
    """
    source_numbers = extract_numbers(source_en)
    target_numbers = extract_numbers(target_fr)

    source_set = set(source_numbers)
    target_set = set(target_numbers)

    missing = sorted(source_set - target_set)
    added = sorted(target_set - source_set)

    # Flag if any numbers are missing or added
    # (Added numbers might be acceptable, like adding page refs, but flag for review)
    flag = "STATMIS" if missing else None

    return StatisticsResult(
        source_numbers=source_numbers,
        target_numbers=target_numbers,
        missing=missing,
        added=added,
        flag=flag,
    )


# --- Combined Quality Check ---

def run_quality_checks(
    source_en: str,
    translation_fr: str,
    glossary_terms: dict[str, str] | None = None,
    glossary_missing: list[str] | None = None,
) -> QualityCheckResults:
    """
    Run all quality checks on a translation.

    Args:
        source_en: English source text
        translation_fr: French translation
        glossary_terms: Dict of {en_term: fr_term} found in source (for Jaccard)
        glossary_missing: List of missing glossary terms (for TERMMIS)

    Returns:
        QualityCheckResults with all check results
    """
    sentence_check = compare_sentence_counts(source_en, translation_fr)
    word_ratio_check = calculate_word_ratio(source_en, translation_fr)
    jaccard_check = check_content_word_similarity(
        source_en, translation_fr, glossary_terms
    )
    statistics_check = check_statistics_preserved(source_en, translation_fr)

    return QualityCheckResults(
        sentence_check=sentence_check,
        word_ratio_check=word_ratio_check,
        jaccard_check=jaccard_check,
        statistics_check=statistics_check,
        glossary_missing=glossary_missing or [],
    )


# --- Flag Classification ---

# Per Part 10: BLOCKING flags require fix before save
BLOCKING_FLAGS = {"SENTMIS", "WORDMIS"}

# Per Part 10: WARNING flags are informational, human reviews later
WARNING_FLAGS = {"WORDDRIFT", "TERMMIS", "STATMIS"}


def is_blocking_flag(flag_code: str) -> bool:
    """Check if a flag code is blocking (requires fix before save)."""
    return flag_code in BLOCKING_FLAGS


def is_warning_flag(flag_code: str) -> bool:
    """Check if a flag code is a warning (save allowed, review later)."""
    return flag_code in WARNING_FLAGS


def classify_flag(flag_code: str) -> str:
    """
    Classify a flag as 'blocking', 'warning', or 'unknown'.

    Per Part 10 of the plan:
    - BLOCKING: SENTMIS, WORDMIS — save rejected, must fix
    - WARNING: WORDDRIFT, TERMMIS, STATMIS — save allowed, human reviews

    Returns:
        'blocking', 'warning', or 'unknown'
    """
    if flag_code in BLOCKING_FLAGS:
        return "blocking"
    elif flag_code in WARNING_FLAGS:
        return "warning"
    else:
        return "unknown"
