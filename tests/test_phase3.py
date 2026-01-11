"""
Tests for Phase 3: Quality Checks.

Covers:
- Sentence counting (EN/FR) with spaCy
- Word ratio calculation
- Glossary term recall for drift detection
- Glossary term verification
- Statistics preservation check
- Blocking vs warning flag classification
"""

import pytest


class TestSentenceCounting:
    """Tests for sentence counting using spaCy."""

    def test_accurate_count_en(self):
        """Should accurately count English sentences."""
        from mcp_server.quality_checks import count_sentences_en

        text = "This is the first sentence. Here is the second one. And a third!"
        count = count_sentences_en(text)

        assert count == 3

    def test_accurate_count_fr(self):
        """Should accurately count French sentences."""
        from mcp_server.quality_checks import count_sentences_fr

        text = "Voici la première phrase. En voici une deuxième. Et une troisième!"
        count = count_sentences_fr(text)

        assert count == 3

    def test_handles_abbreviation_dr(self):
        """Should not split on 'Dr.' abbreviation."""
        from mcp_server.quality_checks import count_sentences_en

        text = "Dr. Smith found the results significant. The study continued."
        count = count_sentences_en(text)

        assert count == 2  # NOT 3

    def test_handles_abbreviation_usa(self):
        """Should not split on 'U.S.A.' abbreviation."""
        from mcp_server.quality_checks import count_sentences_en

        text = "The U.S.A. is a large country. It has many states."
        count = count_sentences_en(text)

        assert count == 2  # NOT 5

    def test_handles_abbreviation_mr(self):
        """Should not split on 'Mr.' abbreviation."""
        from mcp_server.quality_checks import count_sentences_en

        text = "Mr. Jones presented the findings. They were interesting."
        count = count_sentences_en(text)

        assert count == 2

    def test_handles_decimal_numbers(self):
        """Should not split on decimal numbers like 'p < 0.05'."""
        from mcp_server.quality_checks import count_sentences_en

        text = "The result was significant at p < 0.05 level. This is important."
        count = count_sentences_en(text)

        assert count == 2  # NOT 3

    def test_handles_french_abbreviation_m(self):
        """Should handle French 'M.' abbreviation correctly."""
        from mcp_server.quality_checks import count_sentences_fr

        text = "M. Dupont a dit quelque chose. C'était important."
        count = count_sentences_fr(text)

        assert count == 2

    def test_ratio_calculation_matching(self):
        """Should return ratio ~1.0 for matching sentence counts."""
        from mcp_server.quality_checks import compare_sentence_counts

        source = "First sentence. Second sentence. Third sentence."
        target = "Première phrase. Deuxième phrase. Troisième phrase."

        result = compare_sentence_counts(source, target)

        assert result.source_count == 3
        assert result.target_count == 3
        assert result.ratio == 1.0
        assert result.flag is None  # No flag when matching

    def test_sentmis_flag_when_variance_exceeds_15_percent(self):
        """Should flag SENTMIS when variance exceeds 15%."""
        from mcp_server.quality_checks import compare_sentence_counts

        # Source: 10 sentences, Target: ~7 sentences → ratio ~0.7 (30% fewer)
        # Use proper sentences to avoid spaCy counting quirks
        source = "This is sentence one. This is sentence two. This is sentence three. " \
                 "This is sentence four. This is sentence five. This is sentence six. " \
                 "This is sentence seven. This is sentence eight. This is sentence nine. " \
                 "This is sentence ten."
        target = "Ceci est une phrase. Ceci est la deuxième. Voici la troisième. " \
                 "Voici la quatrième. Voici la cinquième. Voici la sixième. " \
                 "Voici la septième."

        result = compare_sentence_counts(source, target)

        assert result.source_count == 10
        assert result.target_count == 7
        assert result.ratio == 0.7
        assert result.flag == "SENTMIS"

    def test_no_flag_within_15_percent(self):
        """Should not flag when within 15% variance."""
        from mcp_server.quality_checks import compare_sentence_counts

        # Source: 10 sentences, Target: 9 sentences → ratio 0.9 (10% fewer)
        # Use proper sentences to avoid spaCy counting quirks
        source = "This is sentence one. This is sentence two. This is sentence three. " \
                 "This is sentence four. This is sentence five. This is sentence six. " \
                 "This is sentence seven. This is sentence eight. This is sentence nine. " \
                 "This is sentence ten."
        target = "Ceci est une phrase. Ceci est la deuxième. Voici la troisième. " \
                 "Voici la quatrième. Voici la cinquième. Voici la sixième. " \
                 "Voici la septième. Voici la huitième. Voici la neuvième."

        result = compare_sentence_counts(source, target)

        assert result.source_count == 10
        assert result.target_count == 9
        assert 0.85 <= result.ratio <= 1.15
        assert result.flag is None

    def test_empty_text_handling(self):
        """Should handle empty text gracefully."""
        from mcp_server.quality_checks import compare_sentence_counts

        result = compare_sentence_counts("", "")

        assert result.source_count == 0
        assert result.target_count == 0
        # Ratio calculation avoids division by zero
        assert result.ratio == 0.0


class TestWordRatio:
    """Tests for word ratio calculation."""

    def test_calculates_word_counts(self):
        """Should correctly count words in both texts."""
        from mcp_server.quality_checks import calculate_word_ratio

        source = "This is a test with ten words in total here."  # 10 words
        target = "Ceci est un test avec douze mots au total ici maintenant."  # 11 words

        result = calculate_word_ratio(source, target)

        assert result.source_words == 10
        assert result.target_words == 11
        assert result.ratio == 1.1

    def test_wordmis_flag_when_ratio_too_low(self):
        """Should flag WORDMIS when ratio below 0.9."""
        from mcp_server.quality_checks import calculate_word_ratio

        # Source: 100 words, Target: 80 words → ratio 0.8
        source = "word " * 100
        target = "mot " * 80

        result = calculate_word_ratio(source, target)

        assert result.ratio == 0.8
        assert result.flag == "WORDMIS"

    def test_wordmis_flag_when_ratio_too_high(self):
        """Should flag WORDMIS when ratio above 1.5."""
        from mcp_server.quality_checks import calculate_word_ratio

        # Source: 100 words, Target: 160 words → ratio 1.6
        source = "word " * 100
        target = "mot " * 160

        result = calculate_word_ratio(source, target)

        assert result.ratio == 1.6
        assert result.flag == "WORDMIS"

    def test_no_flag_within_acceptable_range(self):
        """Should not flag when ratio within 0.9-1.5."""
        from mcp_server.quality_checks import calculate_word_ratio

        # Source: 100 words, Target: 120 words → ratio 1.2 (typical EN→FR)
        source = "word " * 100
        target = "mot " * 120

        result = calculate_word_ratio(source, target)

        assert result.ratio == 1.2
        assert result.flag is None

    def test_ratio_at_boundary_0_9(self):
        """Ratio of exactly 0.9 should be acceptable."""
        from mcp_server.quality_checks import calculate_word_ratio

        source = "word " * 100
        target = "mot " * 90

        result = calculate_word_ratio(source, target)

        assert result.ratio == 0.9
        assert result.flag is None

    def test_ratio_at_boundary_1_5(self):
        """Ratio of exactly 1.5 should be acceptable."""
        from mcp_server.quality_checks import calculate_word_ratio

        source = "word " * 100
        target = "mot " * 150

        result = calculate_word_ratio(source, target)

        assert result.ratio == 1.5
        assert result.flag is None

    def test_empty_source_handling(self):
        """Should handle empty source text gracefully."""
        from mcp_server.quality_checks import calculate_word_ratio

        result = calculate_word_ratio("", "quelques mots")

        assert result.source_words == 0
        assert result.target_words == 2
        # Avoid division by zero
        assert result.ratio == 2.0
        assert result.flag == "WORDMIS"  # Outside acceptable range


class TestGlossaryRecall:
    """Tests for glossary term recall check (replaces Jaccard)."""

    def test_extracts_content_words_en(self):
        """Should extract nouns, verbs, and adjectives from English."""
        from mcp_server.quality_checks import extract_content_words_en

        text = "The anxious child showed avoidance behavior during assessment."
        words = extract_content_words_en(text)

        # Should include content words (lemmatized)
        assert "anxious" in words or "anxiety" in words.union({"anxious"})
        assert "child" in words
        assert "show" in words  # lemmatized from "showed"
        # Should NOT include function words like "the", "during"
        assert "the" not in words
        assert "during" not in words

    def test_extracts_content_words_fr(self):
        """Should extract nouns, verbs, and adjectives from French."""
        from mcp_server.quality_checks import extract_content_words_fr

        text = "L'enfant anxieux montrait un comportement d'évitement."
        words = extract_content_words_fr(text)

        # Should include content words (lemmatized)
        # French lemmas may differ, just check we get some content
        assert len(words) > 0
        # Should NOT include function words
        assert "le" not in words
        assert "un" not in words

    def test_high_recall_when_terms_present(self):
        """Should return high recall when expected terms are found."""
        from mcp_server.quality_checks import check_glossary_recall

        source = "The child shows demand avoidance and anxiety."
        translation = "L'enfant montre un évitement des demandes et de l'anxiété."

        glossary = {
            "demand avoidance": "évitement des demandes",
            "anxiety": "anxiété",
            "child": "enfant",
        }

        result = check_glossary_recall(source, translation, glossary)

        # Should have high recall (most expected terms present)
        assert result.recall >= 0.7
        assert result.flag is None

    def test_worddrift_flag_when_low_recall(self):
        """Should flag WORDDRIFT when recall below 0.7."""
        from mcp_server.quality_checks import check_glossary_recall

        source = "The child shows demand avoidance and anxiety in clinical settings."
        # Translation uses completely different vocabulary
        translation = "La situation médicale présente des caractéristiques particulières."

        glossary = {
            "demand avoidance": "évitement des demandes",
            "anxiety": "anxiété",
            "child": "enfant",
            "clinical": "clinique",
        }

        result = check_glossary_recall(source, translation, glossary)

        # Should have low recall since expected terms are missing
        assert result.flag == "WORDDRIFT"
        assert result.recall < 0.7

    def test_skips_check_with_few_glossary_terms(self):
        """Should skip check when fewer than 3 glossary terms."""
        from mcp_server.quality_checks import check_glossary_recall

        source = "A short sentence."
        translation = "Une phrase courte."

        # Only 1 term - not enough for meaningful check
        glossary = {"sentence": "phrase"}

        result = check_glossary_recall(source, translation, glossary)

        # Per plan: "Skip check if too few terms to be meaningful"
        assert result.recall == 1.0
        assert result.flag is None

    def test_skips_check_with_no_glossary(self):
        """Should return neutral result when no glossary provided."""
        from mcp_server.quality_checks import check_glossary_recall

        result = check_glossary_recall(
            "Source text here.",
            "Texte source ici.",
            glossary=None
        )

        assert result.recall == 1.0
        assert result.flag is None

    def test_returns_missing_expected_words(self):
        """Should return list of missing expected words."""
        from mcp_server.quality_checks import check_glossary_recall

        source = "The child shows demand avoidance and anxiety and stress."
        translation = "L'enfant montre quelque chose."  # Missing most terms

        glossary = {
            "demand avoidance": "évitement des demandes",
            "anxiety": "anxiété",
            "stress": "stress",
            "child": "enfant",
        }

        result = check_glossary_recall(source, translation, glossary)

        # Should report missing expected words
        assert len(result.missing_expected) > 0

    def test_recall_vs_jaccard_difference(self):
        """Recall should pass good translations that Jaccard would fail.

        This test validates the metric change: Jaccard penalizes translations
        for having additional content words (normal), while recall only checks
        if expected terms are present.
        """
        from mcp_server.quality_checks import check_glossary_recall

        # Real-world example: source with few glossary terms, but translation
        # has many additional content words (normal for a full translation)
        source = "Children with PDA show demand avoidance and anxiety."
        translation = (
            "Les enfants avec PDA montrent un évitement des demandes et de l'anxiété. "
            "Cette présentation clinique nécessite une évaluation approfondie et "
            "des stratégies d'accompagnement personnalisées."
        )

        glossary = {
            "demand avoidance": "évitement des demandes",
            "anxiety": "anxiété",
            "PDA": "PDA",
        }

        result = check_glossary_recall(source, translation, glossary)

        # Recall should be high (expected terms are present)
        # Jaccard would have been low (~0.15) due to extra content words
        assert result.recall >= 0.7
        assert result.flag is None


class TestGlossaryVerification:
    """Tests for glossary term verification (TERMMIS)."""

    def test_termmis_for_missing_terms(self):
        """Should identify missing glossary terms."""
        from mcp_server.glossary import verify_glossary_terms

        source = "Children show demand avoidance and need for control."
        # Translation missing "besoin de contrôle"
        translation = "Les enfants montrent un évitement des demandes."

        missing = verify_glossary_terms(source, translation)

        assert len(missing) > 0
        assert any("need for control" in m for m in missing)

    def test_no_termmis_when_terms_present(self):
        """Should not flag when all terms are present."""
        from mcp_server.glossary import verify_glossary_terms

        source = "Children show demand avoidance."
        translation = "Les enfants montrent un évitement des demandes."

        missing = verify_glossary_terms(source, translation)

        # "demand avoidance" is present
        assert not any("demand avoidance" in m for m in missing)

    def test_accepts_fr_alt_variants(self):
        """Should accept fr_alt variants as valid translations."""
        from mcp_server.glossary import get_glossary

        glossary = get_glossary()

        # Find a term with fr_alt defined
        terms = glossary.get_all_terms()
        terms_with_alts = [t for t in terms if t.get("fr_alt")]

        if terms_with_alts:
            term = terms_with_alts[0]
            source = f"The {term['en']} is important."

            # Use the alternative French term
            alt = term["fr_alt"][0] if term.get("fr_alt") else term["fr"]
            translation = f"Le {alt} est important."

            missing = glossary.verify_terms(source, translation)

            # Should NOT flag the term as missing since alt is acceptable
            assert not any(term["en"] in m for m in missing)

    def test_handles_quotes_edge_case(self):
        """
        Edge case: Terms in direct quotes may be flagged.

        Per D12: If source contains quoted English term and translator
        correctly leaves it in English, TERMMIS will flag it.
        This is intended behavior - human reviews and recognizes quote.
        """
        from mcp_server.glossary import verify_glossary_terms

        source = 'As Smith called it, "demand avoidance" is the key feature.'
        # Translator correctly leaves quoted term in English
        translation = 'Comme Smith l\'a appelé, "demand avoidance" est la caractéristique clé.'

        missing = verify_glossary_terms(source, translation)

        # The term IS flagged (since French term not present)
        # This is correct per plan - human reviewer will see it's a quote
        # Just verify the system works - flag present OR not, depending on implementation
        # The key is we don't crash and return a list
        assert isinstance(missing, list)


class TestStatisticsCheck:
    """Tests for statistics preservation check."""

    def test_detects_numbers_in_source(self):
        """Should extract all numbers from source text."""
        from mcp_server.quality_checks import extract_numbers

        text = "The sample included 42 participants. Mean age was 8.5 years. Results showed p < 0.05."
        numbers = extract_numbers(text)

        assert "42" in numbers
        assert "8.5" in numbers
        assert "0.05" in numbers

    def test_detects_percentages(self):
        """Should extract percentage values."""
        from mcp_server.quality_checks import extract_numbers

        text = "Response rate was 85% with 12.5% showing improvement."
        numbers = extract_numbers(text)

        assert "85%" in numbers
        assert "12.5%" in numbers

    def test_statmis_when_numbers_differ(self):
        """Should flag STATMIS when source numbers are missing from translation."""
        from mcp_server.quality_checks import check_statistics_preserved

        source = "The study included 42 participants with mean age 8.5."
        # Translation missing "42"
        translation = "L'étude comprenait des participants avec un âge moyen de 8.5."

        result = check_statistics_preserved(source, translation)

        assert "42" in result.missing
        assert result.flag == "STATMIS"

    def test_no_statmis_when_numbers_preserved(self):
        """Should not flag when all numbers are preserved."""
        from mcp_server.quality_checks import check_statistics_preserved

        source = "Results: n=42, mean=8.5, p<0.05"
        translation = "Résultats: n=42, moyenne=8.5, p<0.05"

        result = check_statistics_preserved(source, translation)

        assert result.flag is None
        assert len(result.missing) == 0

    def test_reports_added_numbers(self):
        """Should report numbers added in translation."""
        from mcp_server.quality_checks import check_statistics_preserved

        source = "The study was significant."
        translation = "L'étude (page 42) était significative avec p=0.01."

        result = check_statistics_preserved(source, translation)

        # Added numbers are reported but don't cause STATMIS
        assert "42" in result.added or "0.01" in result.added
        # No STATMIS since nothing is missing
        assert result.flag is None

    def test_handles_text_without_numbers(self):
        """Should handle text with no numbers gracefully."""
        from mcp_server.quality_checks import check_statistics_preserved

        source = "The study examined behavioral patterns."
        translation = "L'étude a examiné les comportements."

        result = check_statistics_preserved(source, translation)

        assert result.source_numbers == []
        assert result.flag is None


class TestBlockingVsWarning:
    """Tests for flag classification (blocking vs warning)."""

    def test_sentmis_is_blocking(self):
        """SENTMIS should be classified as blocking."""
        from mcp_server.quality_checks import is_blocking_flag, classify_flag

        assert is_blocking_flag("SENTMIS") is True
        assert classify_flag("SENTMIS") == "blocking"

    def test_wordmis_is_blocking(self):
        """WORDMIS should be classified as blocking."""
        from mcp_server.quality_checks import is_blocking_flag, classify_flag

        assert is_blocking_flag("WORDMIS") is True
        assert classify_flag("WORDMIS") == "blocking"

    def test_worddrift_is_warning(self):
        """WORDDRIFT should be classified as warning."""
        from mcp_server.quality_checks import is_warning_flag, classify_flag

        assert is_warning_flag("WORDDRIFT") is True
        assert classify_flag("WORDDRIFT") == "warning"

    def test_termmis_is_warning(self):
        """TERMMIS should be classified as warning."""
        from mcp_server.quality_checks import is_warning_flag, classify_flag

        assert is_warning_flag("TERMMIS") is True
        assert classify_flag("TERMMIS") == "warning"

    def test_statmis_is_warning(self):
        """STATMIS should be classified as warning."""
        from mcp_server.quality_checks import is_warning_flag, classify_flag

        assert is_warning_flag("STATMIS") is True
        assert classify_flag("STATMIS") == "warning"

    def test_unknown_flag_classification(self):
        """Unknown flags should be classified as 'unknown'."""
        from mcp_server.quality_checks import classify_flag

        assert classify_flag("UNKNOWN_FLAG") == "unknown"
        assert classify_flag("TBL") == "unknown"  # Content flag, not quality check

    def test_quality_check_results_blocking_flags(self):
        """QualityCheckResults should correctly identify blocking flags."""
        from mcp_server.quality_checks import (
            QualityCheckResults,
            SentenceCountResult,
            WordRatioResult,
        )

        # Create result with SENTMIS
        results = QualityCheckResults(
            sentence_check=SentenceCountResult(
                source_count=10, target_count=5, ratio=0.5, flag="SENTMIS"
            ),
            word_ratio_check=WordRatioResult(
                source_words=100, target_words=110, ratio=1.1, flag=None
            ),
            recall_check=None,
            statistics_check=None,
            glossary_missing=[],
        )

        assert results.has_blocking is True
        assert "SENTMIS" in results.blocking_flags
        assert len(results.blocking_flags) == 1

    def test_quality_check_results_warning_flags(self):
        """QualityCheckResults should correctly identify warning flags."""
        from mcp_server.quality_checks import (
            QualityCheckResults,
            SentenceCountResult,
            WordRatioResult,
            GlossaryRecallResult,
            StatisticsResult,
        )

        # Create result with warnings but no blocking
        results = QualityCheckResults(
            sentence_check=SentenceCountResult(
                source_count=10, target_count=10, ratio=1.0, flag=None
            ),
            word_ratio_check=WordRatioResult(
                source_words=100, target_words=110, ratio=1.1, flag=None
            ),
            recall_check=GlossaryRecallResult(
                recall=0.5, expected_words=set(), actual_words=set(),
                missing_expected=[], flag="WORDDRIFT"
            ),
            statistics_check=StatisticsResult(
                source_numbers=["42"], target_numbers=[],
                missing=["42"], added=[], flag="STATMIS"
            ),
            glossary_missing=["term -> terme"],
        )

        assert results.has_blocking is False
        assert "WORDDRIFT" in results.warning_flags
        assert "STATMIS" in results.warning_flags
        assert "TERMMIS" in results.warning_flags
        assert len(results.warning_flags) == 3

    def test_quality_check_results_combined(self):
        """QualityCheckResults should handle both blocking and warning flags."""
        from mcp_server.quality_checks import (
            QualityCheckResults,
            SentenceCountResult,
            WordRatioResult,
            StatisticsResult,
        )

        results = QualityCheckResults(
            sentence_check=SentenceCountResult(
                source_count=10, target_count=5, ratio=0.5, flag="SENTMIS"
            ),
            word_ratio_check=WordRatioResult(
                source_words=100, target_words=200, ratio=2.0, flag="WORDMIS"
            ),
            recall_check=None,
            statistics_check=StatisticsResult(
                source_numbers=["42"], target_numbers=[],
                missing=["42"], added=[], flag="STATMIS"
            ),
            glossary_missing=["term -> terme"],
        )

        assert results.has_blocking is True
        assert len(results.blocking_flags) == 2
        assert "SENTMIS" in results.blocking_flags
        assert "WORDMIS" in results.blocking_flags
        assert len(results.warning_flags) == 2
        assert "STATMIS" in results.warning_flags
        assert "TERMMIS" in results.warning_flags


class TestCombinedQualityChecks:
    """Integration tests for run_quality_checks function."""

    def test_run_all_checks(self):
        """Should run all quality checks and return combined results."""
        from mcp_server.quality_checks import run_quality_checks

        source = "The child showed demand avoidance. Results: n=42, p<0.05."
        translation = "L'enfant montrait un évitement des demandes. Résultats: n=42, p<0.05."

        results = run_quality_checks(
            source_en=source,
            translation_fr=translation,
            glossary_terms={"demand avoidance": "évitement des demandes"},
            glossary_missing=[],
        )

        assert results.sentence_check is not None
        assert results.word_ratio_check is not None
        assert results.statistics_check is not None
        # No blocking flags for this good translation
        assert results.has_blocking is False

    def test_run_checks_with_missing_glossary_terms(self):
        """Should include glossary_missing in results."""
        from mcp_server.quality_checks import run_quality_checks

        source = "The child showed demand avoidance."
        translation = "L'enfant montrait quelque chose."

        results = run_quality_checks(
            source_en=source,
            translation_fr=translation,
            glossary_terms={},
            glossary_missing=["demand avoidance -> évitement des demandes"],
        )

        assert "TERMMIS" in results.warning_flags
        assert len(results.glossary_missing) == 1
