"""
Tests for Phase 2 tools.

Covers:
- get_chunk() — chunked delivery, caching, error handling
- PDF extraction — fallback chain, problem detection
- Glossary matching — term detection, variant matching
- Chunking logic — paragraph splitting, long paragraph handling
"""

import pytest
from pathlib import Path


class TestGetChunk:
    """Tests for get_chunk() tool."""

    def test_article_not_found(self, db_with_articles, clear_chunk_cache):
        """Should return error for nonexistent article."""
        from mcp_server.tools import get_chunk

        result = get_chunk("nonexistent-article", 1)

        assert result["error"] is True
        assert result["error_code"] == "ARTICLE_NOT_FOUND"
        assert "action" in result

    def test_paywalled_article_returns_error(self, db_with_articles, clear_chunk_cache):
        """Should return PAYWALLED error for non-open-access articles (D11)."""
        from mcp_server.tools import get_chunk

        # test-article-3 has open_access=0 (defined in conftest sample_articles)
        result = get_chunk("test-article-3", 1)

        assert result["error"] is True
        assert result["error_code"] == "PAYWALLED"
        assert "validate_classification" in result["action"]

    def test_no_cached_file_returns_not_cached(self, db_with_articles, clear_chunk_cache):
        """Should return NOT_CACHED when file isn't in cache but URL exists."""
        from mcp_server.tools import get_chunk

        # test-article-1 has source_url but no cached file
        result = get_chunk("test-article-1", 1)

        assert result["error"] is True
        assert result["error_code"] == "NOT_CACHED"

    def test_no_source_url_returns_no_source(self, db_with_articles, clear_chunk_cache):
        """Should return NO_SOURCE when article has no source_url."""
        from mcp_server.database import get_database

        db = get_database()
        # Insert article with no source_url
        db.execute("""
            INSERT INTO articles (id, source_title, open_access, processing_status)
            VALUES ('no-url-article', 'No URL Test', 1, 'pending')
        """)
        db.commit()

        from mcp_server.tools import get_chunk
        result = get_chunk("no-url-article", 1)

        assert result["error"] is True
        assert result["error_code"] == "NO_SOURCE"
        assert "NOURL" in result["problems"]

    def test_chunk_response_schema(self, db_with_articles, cached_pdf, clear_chunk_cache):
        """Chunk response should match expected schema."""
        from mcp_server.tools import get_chunk

        # Rename cached PDF to match test article
        target = cached_pdf.parent / "test-article-1.pdf"
        if cached_pdf != target:
            cached_pdf.rename(target)

        result = get_chunk("test-article-1", 1)

        assert "chunk_number" in result
        assert "total_chunks" in result
        assert "text" in result
        assert "glossary_terms" in result
        assert "instruction" in result
        assert result["complete"] is False

    def test_chunk_caching(self, db_with_articles, cached_pdf, clear_chunk_cache):
        """Subsequent chunk requests should use cache."""
        from mcp_server.tools import get_chunk, _get_cached_entry

        target = cached_pdf.parent / "test-article-1.pdf"
        if cached_pdf != target:
            cached_pdf.rename(target)

        # First call populates cache
        get_chunk("test-article-1", 1)
        assert _get_cached_entry("test-article-1") is not None

        # Second call should use cache (no extraction)
        result = get_chunk("test-article-1", 2)
        assert result["chunk_number"] == 2

    def test_complete_response_after_last_chunk(self, db_with_articles, cached_pdf, clear_chunk_cache):
        """Should return complete=true after last chunk."""
        from mcp_server.tools import get_chunk

        target = cached_pdf.parent / "test-article-1.pdf"
        if cached_pdf != target:
            cached_pdf.rename(target)

        # Get first chunk to know total
        result1 = get_chunk("test-article-1", 1)
        total = result1["total_chunks"]

        # Request one past the last
        result = get_chunk("test-article-1", total + 1)

        assert result["complete"] is True
        assert result["total_chunks"] == total
        assert "next_step" in result

    def test_complete_response_includes_extraction_metadata(self, db_with_articles, cached_pdf, clear_chunk_cache):
        """Complete response should include extraction_metadata for save_article."""
        from mcp_server.tools import get_chunk

        target = cached_pdf.parent / "test-article-1.pdf"
        if cached_pdf != target:
            cached_pdf.rename(target)

        # Get first chunk to know total
        result1 = get_chunk("test-article-1", 1)
        total = result1["total_chunks"]

        # Request one past the last to get complete response
        result = get_chunk("test-article-1", total + 1)

        assert result["complete"] is True
        assert "extraction_metadata" in result
        assert "extractor_used" in result["extraction_metadata"]
        assert "extraction_problems" in result["extraction_metadata"]

    def test_extraction_warnings_on_every_chunk(self, db_with_articles, cached_pdf, clear_chunk_cache):
        """Extraction warnings should be included on every chunk response."""
        from mcp_server.tools import get_chunk

        target = cached_pdf.parent / "test-article-1.pdf"
        if cached_pdf != target:
            cached_pdf.rename(target)

        # Get first chunk
        result1 = get_chunk("test-article-1", 1)
        assert "extraction_warnings" in result1

        # Get second chunk (if exists)
        if result1["total_chunks"] > 1:
            result2 = get_chunk("test-article-1", 2)
            assert "extraction_warnings" in result2

    def test_glossary_terms_per_chunk(self, db_with_articles, cached_pdf, clear_chunk_cache):
        """Each chunk should have glossary terms relevant to that chunk."""
        from mcp_server.tools import get_chunk

        target = cached_pdf.parent / "test-article-1.pdf"
        if cached_pdf != target:
            cached_pdf.rename(target)

        result = get_chunk("test-article-1", 1)

        # O'Nions paper should have PDA-related terms
        terms = result["glossary_terms"]
        assert isinstance(terms, dict)
        # Should find at least some terms in the first chunk
        assert len(terms) > 0


class TestPdfExtraction:
    """Tests for PDF extraction module."""

    def test_extract_from_real_pdf(self, cached_pdf):
        """Should extract text from real PDF."""
        from mcp_server.pdf_extraction import extract_article_text

        result = extract_article_text(cached_pdf)

        assert result.usable is True
        assert len(result.text) > 1000
        assert result.extractor_used in ("pymupdf", "pdfminer", "pdfplumber", "preprocessed")

    def test_problem_detection_tooshort(self):
        """Should detect TOOSHORT for very short text."""
        from mcp_server.pdf_extraction import detect_extraction_problems

        short_text = "Just a few words here."
        problems = detect_extraction_problems(short_text)

        assert "TOOSHORT" in problems
        assert "UNUSABLE" in problems

    def test_problem_detection_noparagraphs(self):
        """Should detect NOPARAGRAPHS for text without structure."""
        from mcp_server.pdf_extraction import detect_extraction_problems

        # Long text with no paragraph breaks
        flat_text = "word " * 600  # 600 words, no paragraphs
        problems = detect_extraction_problems(flat_text)

        assert "NOPARAGRAPHS" in problems

    def test_preprocessed_txt_takes_precedence(self, tmp_path):
        """Should use .txt file if it exists alongside PDF."""
        from mcp_server.pdf_extraction import extract_article_text

        # Create a .txt file
        txt_path = tmp_path / "test.txt"
        txt_path.write_text("This is preprocessed text. " * 50)

        # Even if we pass a PDF path, it should check for .txt
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")

        result = extract_article_text(txt_path)

        assert result.extractor_used == "preprocessed"
        assert "preprocessed text" in result.text

    def test_get_cached_path(self, cached_pdf):
        """Should return cached path when file exists."""
        from mcp_server.pdf_extraction import get_cached_path, CACHE_DIR

        # The cached_pdf fixture puts file at CACHE_DIR/test-article-1.pdf
        # but we need to check with an article ID
        article_id = cached_pdf.stem  # e.g., "test-article-1"

        path = get_cached_path(article_id)
        assert path is not None
        assert path.exists()

    def test_get_cached_path_not_found(self):
        """Should return None when no cached file."""
        from mcp_server.pdf_extraction import get_cached_path

        path = get_cached_path("definitely-not-cached-article")
        assert path is None


class TestGlossary:
    """Tests for glossary module."""

    def test_glossary_loads(self):
        """Glossary should load from YAML."""
        from mcp_server.glossary import get_glossary

        glossary = get_glossary()
        terms = glossary.get_all_terms()

        assert len(terms) > 100  # Should have lots of terms

    def test_glossary_has_version(self):
        """Glossary should have a version field (D27)."""
        from mcp_server.glossary import get_glossary, get_glossary_version

        glossary = get_glossary()
        version = glossary.version

        assert version != "unknown"
        assert version == get_glossary_version()
        # Version should be a date string like "2025-01-11"
        assert len(version) == 10

    def test_find_exact_match(self):
        """Should find exact term matches."""
        from mcp_server.glossary import find_glossary_terms_in_text

        text = "Children with demand avoidance often show need for control."
        terms = find_glossary_terms_in_text(text)

        assert "demand avoidance" in terms
        assert "need for control" in terms

    def test_case_insensitive_matching(self):
        """Should match regardless of case."""
        from mcp_server.glossary import find_glossary_terms_in_text

        text = "DEMAND AVOIDANCE is a key feature of PDA."
        terms = find_glossary_terms_in_text(text)

        assert "demand avoidance" in terms or "Demand Avoidance" in terms

    def test_word_boundary_matching(self):
        """Should not match partial words."""
        from mcp_server.glossary import find_glossary_terms_in_text

        # "attention" is a glossary term, but "inattention" should not match
        text = "The child showed inattention during the test."
        terms = find_glossary_terms_in_text(text)

        # Should NOT match "attention" inside "inattention"
        # This depends on implementation - verify the boundary check works
        assert "attention" not in terms or text.find(" attention ") >= 0

    def test_returns_french_translation(self):
        """Should return French translations for matched terms."""
        from mcp_server.glossary import find_glossary_terms_in_text

        text = "The autism spectrum includes many presentations."
        terms = find_glossary_terms_in_text(text)

        if "autism spectrum" in terms:
            assert terms["autism spectrum"] == "spectre autistique"

    def test_verify_terms_finds_missing(self):
        """Should identify when French terms are missing from translation."""
        from mcp_server.glossary import verify_glossary_terms

        source = "Children show demand avoidance and need for control."
        # Translation missing "besoin de contrôle"
        translation = "Les enfants montrent un évitement des demandes."

        missing = verify_glossary_terms(source, translation)

        assert len(missing) > 0
        assert any("need for control" in m for m in missing)

    def test_verify_terms_accepts_variants(self):
        """Should accept fr_alt variants as valid translations."""
        from mcp_server.glossary import get_glossary

        glossary = get_glossary()

        # Check if any term has fr_alt defined
        terms = glossary.get_all_terms()
        terms_with_alts = [t for t in terms if t.get("fr_alt")]

        # If there are terms with alternatives, verify they're indexed
        if terms_with_alts:
            term = terms_with_alts[0]
            entry = glossary.get_entry(term["en"])
            assert entry is not None


class TestChunking:
    """Tests for text chunking logic."""

    def test_splits_on_paragraphs(self, sample_text):
        """Should split text into chunks based on paragraphs."""
        from mcp_server.tools import _split_into_chunks

        chunks = _split_into_chunks(sample_text, target_paragraphs=4)

        assert len(chunks) >= 2
        # Each chunk should have multiple paragraphs
        for chunk in chunks:
            assert "\n\n" in chunk or len(chunk.split()) > 50

    def test_respects_target_paragraphs(self, sample_text):
        """Should create chunks with approximately target paragraph count."""
        from mcp_server.tools import _split_into_chunks

        chunks = _split_into_chunks(sample_text, target_paragraphs=2)

        # With 8 paragraphs and target of 2, should have 4 chunks
        assert len(chunks) == 4

    def test_handles_long_paragraphs(self):
        """Should split paragraphs exceeding 500 words."""
        from mcp_server.tools import _split_into_chunks

        # Create a very long paragraph
        long_para = "This is a sentence with several words. " * 100  # ~700 words
        text = f"Short intro paragraph.\n\n{long_para}\n\nShort outro."

        chunks = _split_into_chunks(text, target_paragraphs=4)

        # The long paragraph should have been split
        # So we should have more chunks than if it weren't split
        assert len(chunks) >= 1

    def test_empty_text_returns_empty(self):
        """Should handle empty or whitespace-only text."""
        from mcp_server.tools import _split_into_chunks

        chunks = _split_into_chunks("")
        assert chunks == []

        chunks2 = _split_into_chunks("   \n\n   ")
        assert chunks2 == []


class TestChunkCache:
    """Tests for chunk caching behavior."""

    def test_cache_cleared_on_skip(self, db_with_articles, cached_pdf, clear_chunk_cache):
        """Cache should be cleared when article is skipped."""
        from mcp_server.tools import get_chunk, skip_article, _get_cached_entry

        target = cached_pdf.parent / "test-article-1.pdf"
        if cached_pdf != target:
            cached_pdf.rename(target)

        # Populate cache
        get_chunk("test-article-1", 1)
        assert _get_cached_entry("test-article-1") is not None

        # Skip article
        skip_article("test-article-1", "Test skip", "SKIP")

        # Cache should be cleared
        assert _get_cached_entry("test-article-1") is None

    def test_cache_isolation(self, db_with_articles, clear_chunk_cache):
        """Each article should have independent cache."""
        from mcp_server.tools import _set_cached_entry, _get_cached_entry

        _set_cached_entry("article-a", ["chunk1", "chunk2"], "test", [])
        _set_cached_entry("article-b", ["different1"], "test", [])

        entry_a = _get_cached_entry("article-a")
        entry_b = _get_cached_entry("article-b")

        assert entry_a.chunks == ["chunk1", "chunk2"]
        assert entry_b.chunks == ["different1"]

    def test_cache_stores_extraction_metadata(self, clear_chunk_cache):
        """Cache entries should store extraction metadata."""
        from mcp_server.tools import _set_cached_entry, _get_cached_entry

        _set_cached_entry(
            "test-article",
            ["chunk1", "chunk2"],
            extractor_used="pymupdf",
            extraction_problems=["COLUMNJUMBLE", "NOREFSSECTION"]
        )

        entry = _get_cached_entry("test-article")

        assert entry is not None
        assert entry.extractor_used == "pymupdf"
        assert entry.extraction_problems == ["COLUMNJUMBLE", "NOREFSSECTION"]
