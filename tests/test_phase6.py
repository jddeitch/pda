"""
Tests for Phase 6: Integration Testing.

Covers end-to-end workflows, crash recovery, session management,
and multi-article processing scenarios.

Test classes:
- TestEndToEnd: complete article workflow (get_next → chunks → validate → save)
- TestCrashRecovery: in_progress article picked up on restart, chunk cache regenerated
- TestSessionPause: pause triggers at interval, continues after reset
- TestPaywalledFlow: title/summary only, no chunks, save with null full_text
- TestSkipFlow: extraction failure → skip → next article
- TestMultipleArticles: process 3 articles sequentially, verify state consistency
- TestTokenExpiry: token expires after 30 minutes (integration with full workflow)
"""

import pytest


class TestEndToEnd:
    """Tests for complete article workflow: get_next → chunks → validate → save."""

    def _setup_cached_text(self, article_id: str, text: str, monkeypatch):
        """Helper to set up cached text file for an article."""
        from mcp_server.pdf_extraction import CACHE_DIR

        cache_path = CACHE_DIR / f"{article_id}.txt"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text)
        return cache_path

    def _cleanup_cached_text(self, article_id: str):
        """Clean up cached text file after test."""
        from mcp_server.pdf_extraction import CACHE_DIR

        cache_path = CACHE_DIR / f"{article_id}.txt"
        if cache_path.exists():
            cache_path.unlink()

    def test_complete_workflow_open_access_article(self, db_with_articles, monkeypatch, clear_chunk_cache):
        """Should complete full workflow for open-access article: get → chunks → validate → save."""
        from mcp_server.tools import (
            get_next_article, get_chunk, validate_classification, save_article
        )

        # Set up source text with enough content for quality checks
        # Using clear paragraph structure that spaCy will segment consistently.
        # The French/English sentence models segment differently, so we use
        # explicit sentence structure to ensure ratio stays within 0.85-1.15.
        source_text = """This is a study about Pathological Demand Avoidance in children. The research examines demand avoidance behaviors in autism spectrum disorder.

Children with PDA show significant anxiety. The study included 25 participants aged 5-12 years from clinical settings.

Assessment tools included the EDA-Q. Results showed 85% of participants met criteria for PDA profile.

Therapeutic approaches focused on reducing demands. Family support was an important component."""

        cache_path = self._setup_cached_text("test-article-1", source_text, monkeypatch)

        try:
            # Step 1: Get next article
            result = get_next_article()
            assert "article" in result
            article = result["article"]
            assert article["id"] == "test-article-1"
            assert article["open_access"] is True

            # Step 2: Process chunks
            translated_chunks = []
            chunk_num = 1

            while True:
                chunk_result = get_chunk(article["id"], chunk_num)

                if chunk_result.get("complete"):
                    break

                assert "text" in chunk_result
                assert "glossary_terms" in chunk_result
                assert "instruction" in chunk_result

                # For integration test, provide a proper French translation
                # that maintains sentence structure (matching sentence count)
                translated_chunks.append(chunk_result["text"])  # Will replace below
                chunk_num += 1

            # Use a pre-written proper French translation with matching sentence count
            # Structure matches source: 7 sentences in 4 paragraphs (spaCy sees 7 due to EDA-Q abbreviation)
            translated_full_text = """Ceci est une étude sur l'évitement pathologique des demandes chez les enfants. La recherche examine les comportements d'évitement des demandes dans le trouble du spectre autistique.

Les enfants présentant un profil EPA montrent une anxiété significative. L'étude a inclus 25 participants âgés de 5 à 12 ans provenant de milieux cliniques.

Les outils d'évaluation comprenaient l'EDA-Q. Les résultats ont montré que 85% des participants répondaient aux critères du profil EPA.

Les approches thérapeutiques se sont concentrées sur la réduction des demandes. Le soutien familial était une composante importante."""

            # Step 3: Validate classification
            validate_result = validate_classification(
                article_id=article["id"],
                method="empirical",
                voice="academic",
                peer_reviewed=True,
                open_access=True,
                primary_category="fondements",
                secondary_categories=["evaluation"],
                keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
            )

            assert validate_result["valid"] is True
            assert "token" in validate_result
            token = validate_result["token"]

            # Step 4: Save article
            save_result = save_article(
                article_id=article["id"],
                validation_token=token,
                source="Test Research Journal",
                doi="10.1234/test.2024",
                translated_title="Étude sur l'EPA chez les enfants",
                translated_summary="Cette étude examine l'évitement des demandes chez les enfants autistes.",
                translated_full_text=translated_full_text,
                flags=[],
            )

            assert save_result["success"] is True
            # Verify quality checks ran - success=True means no blocking flags
            # warning_flags may be present (e.g., TERMMIS) but don't block save
            assert "warning_flags" in save_result  # Quality checks produced a result

            # Verify article is now translated in database
            article_after = db_with_articles.get_article_by_id("test-article-1")
            assert article_after["processing_status"] == "translated"
            assert article_after["method"] == "empirical"
            assert article_after["voice"] == "academic"

            # Verify translation saved
            translation_row = db_with_articles.execute(
                "SELECT * FROM translations WHERE article_id = ? AND target_language = ?",
                ("test-article-1", "fr")
            ).fetchone()
            assert translation_row is not None
            assert translation_row["translated_title"] == "Étude sur l'EPA chez les enfants"

        finally:
            self._cleanup_cached_text("test-article-1")

    def test_workflow_includes_glossary_terms(self, db_with_articles, monkeypatch, clear_chunk_cache):
        """Should provide relevant glossary terms with each chunk."""
        from mcp_server.tools import get_next_article, get_chunk

        # Set up text with glossary terms
        source_text = """Pathological Demand Avoidance is a behavior profile in autism.
Children with PDA show demand avoidance and anxiety.

The autism spectrum includes many presentations.
Interoception plays a role in emotional regulation."""

        cache_path = self._setup_cached_text("test-article-1", source_text, monkeypatch)

        try:
            result = get_next_article()
            article = result["article"]

            chunk_result = get_chunk(article["id"], 1)

            assert chunk_result.get("error") is not True
            assert "glossary_terms" in chunk_result
            # Should have found some glossary terms
            glossary = chunk_result["glossary_terms"]
            # Common terms like "demand avoidance", "autism" should be present
            assert len(glossary) >= 1

        finally:
            self._cleanup_cached_text("test-article-1")

    def test_workflow_marks_article_in_progress_during_processing(self, db_with_articles):
        """Article should be marked in_progress when get_next_article() returns it."""
        from mcp_server.tools import get_next_article

        # Before
        article_before = db_with_articles.get_article_by_id("test-article-1")
        assert article_before["processing_status"] == "pending"

        # Get next article
        get_next_article()

        # After — should be in_progress
        article_after = db_with_articles.get_article_by_id("test-article-1")
        assert article_after["processing_status"] == "in_progress"


class TestCrashRecovery:
    """Tests for crash recovery: in_progress article picked up on restart."""

    def test_in_progress_article_picked_up_on_restart(self, db_with_articles):
        """Should return in_progress article before pending articles."""
        from mcp_server.tools import get_next_article

        # Manually set article-2 to in_progress (simulating crash mid-processing)
        db_with_articles.execute(
            "UPDATE articles SET processing_status = 'in_progress' WHERE id = ?",
            ("test-article-2",)
        )
        db_with_articles.commit()

        # Get next article — should return in_progress one first
        result = get_next_article()

        assert "article" in result
        assert result["article"]["id"] == "test-article-2"

    def test_chunk_cache_regenerated_after_restart(self, db_with_articles, monkeypatch, clear_chunk_cache):
        """Should regenerate chunk cache when resuming in_progress article."""
        from mcp_server.tools import get_next_article, get_chunk, _chunk_cache
        from mcp_server.tools import clear_chunk_cache as tool_clear_cache
        from mcp_server.pdf_extraction import CACHE_DIR

        # Set up cached text file
        source_text = """First paragraph about PDA and demand avoidance.

Second paragraph about autism spectrum disorder.

Third paragraph about assessment approaches.

Fourth paragraph about intervention strategies."""

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / "test-article-1.txt"
        cache_path.write_text(source_text)

        try:
            # Clear cache to simulate server restart (use function, not fixture)
            tool_clear_cache()
            assert "test-article-1" not in _chunk_cache

            # Get next article
            get_next_article()

            # Request chunk — should regenerate cache
            chunk_result = get_chunk("test-article-1", 1)

            assert chunk_result.get("error") is not True
            assert "text" in chunk_result
            # Cache should now be populated
            assert "test-article-1" in _chunk_cache

        finally:
            if cache_path.exists():
                cache_path.unlink()

    def test_partial_translation_lost_on_crash(self, db_with_articles, monkeypatch, clear_chunk_cache):
        """
        Per D1: Partial translations are NOT persisted.
        If crash occurs mid-article, next session starts fresh from chunk 1.
        """
        from mcp_server.tools import get_next_article, get_chunk, clear_chunk_cache as tool_clear_cache
        from mcp_server.pdf_extraction import CACHE_DIR

        # Set up source text
        source_text = """First chunk text.

Second chunk text.

Third chunk text.

Fourth chunk text."""

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / "test-article-1.txt"
        cache_path.write_text(source_text)

        try:
            # Simulate processing some chunks
            get_next_article()
            get_chunk("test-article-1", 1)
            get_chunk("test-article-1", 2)

            # Simulate crash — clear cache
            tool_clear_cache()

            # On "restart", article is still in_progress
            article = db_with_articles.get_article_by_id("test-article-1")
            assert article["processing_status"] == "in_progress"

            # Next session must start from chunk 1 again
            # The cache is regenerated, but we start from the beginning
            chunk_result = get_chunk("test-article-1", 1)
            assert chunk_result.get("chunk_number") == 1
            assert "text" in chunk_result

        finally:
            if cache_path.exists():
                cache_path.unlink()


class TestSessionPause:
    """Tests for session pause: triggers at interval, continues after reset."""

    def test_pause_triggers_after_interval(self, db_with_articles):
        """Should return SESSION_PAUSE after processing interval number of articles."""
        from mcp_server.tools import (
            get_next_article, validate_classification, save_article,
            set_human_review_interval
        )

        # Set interval to 2
        set_human_review_interval(2)

        # Process first article
        result1 = get_next_article()
        assert "article" in result1

        token1 = validate_classification(
            article_id=result1["article"]["id"],
            method="empirical",
            voice="academic",
            peer_reviewed=True,
            open_access=True,
            primary_category="fondements",
            secondary_categories=[],
            keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
        )["token"]

        save_article(
            article_id=result1["article"]["id"],
            validation_token=token1,
            source="Journal 1",
            doi=None,
            translated_title="Titre 1",
            translated_summary="Résumé 1",
            translated_full_text=None,
            flags=[],
        )

        # Process second article
        result2 = get_next_article()
        assert "article" in result2

        token2 = validate_classification(
            article_id=result2["article"]["id"],
            method="synthesis",
            voice="practitioner",
            peer_reviewed=False,
            open_access=True,
            primary_category="prise_en_charge",
            secondary_categories=[],
            keywords=["PDA", "autism", "management", "children", "families"],
        )["token"]

        save_article(
            article_id=result2["article"]["id"],
            validation_token=token2,
            source="Journal 2",
            doi=None,
            translated_title="Titre 2",
            translated_summary="Résumé 2",
            translated_full_text=None,
            flags=[],
        )

        # Third call should return SESSION_PAUSE
        result3 = get_next_article()

        assert result3.get("status") == "SESSION_PAUSE"
        assert result3["articles_processed"] == 2

    def test_continues_after_reset(self, db_with_articles):
        """Should continue processing after reset_session_counter()."""
        from mcp_server.tools import (
            get_next_article, validate_classification, save_article,
            set_human_review_interval, reset_session_counter
        )

        # Set interval to 1
        set_human_review_interval(1)

        # Process one article
        result1 = get_next_article()
        token1 = validate_classification(
            article_id=result1["article"]["id"],
            method="empirical",
            voice="academic",
            peer_reviewed=True,
            open_access=True,
            primary_category="fondements",
            secondary_categories=[],
            keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
        )["token"]

        save_article(
            article_id=result1["article"]["id"],
            validation_token=token1,
            source="Journal",
            doi=None,
            translated_title="Titre",
            translated_summary="Résumé",
            translated_full_text=None,
            flags=[],
        )

        # Should be paused
        paused_result = get_next_article()
        assert paused_result.get("status") == "SESSION_PAUSE"

        # Reset counter
        reset_result = reset_session_counter()
        assert reset_result["success"] is True

        # Should now be able to continue
        continued_result = get_next_article()
        assert "article" in continued_result
        assert continued_result.get("status") is None

    def test_session_state_visible_in_progress(self, db_with_articles):
        """Session state should be visible in get_progress() response."""
        from mcp_server.tools import get_progress, set_human_review_interval

        set_human_review_interval(10)

        result = get_progress()

        assert "session" in result
        assert result["session"]["human_review_interval"] == 10
        assert result["session"]["articles_processed_count"] == 0
        assert result["session"]["remaining_before_pause"] == 10


class TestPaywalledFlow:
    """Tests for paywalled article flow: title/summary only, no chunks."""

    def test_paywalled_article_skips_chunk_loop(self, db_with_articles):
        """Should not call get_chunk for paywalled articles (open_access=0)."""
        from mcp_server.tools import get_next_article, get_chunk

        # test-article-3 is paywalled
        # First need to make it the next article by translating others
        # Or directly test get_chunk behavior on paywalled article
        result = get_chunk("test-article-3", 1)

        assert result.get("error") is True
        assert result.get("error_code") == "PAYWALLED"

    def test_paywalled_complete_workflow(self, db_with_articles):
        """Should complete workflow for paywalled article with only title/summary."""
        from mcp_server.tools import validate_classification, save_article

        # Validate classification for paywalled article
        validate_result = validate_classification(
            article_id="test-article-3",
            method="empirical",
            voice="academic",
            peer_reviewed=True,
            open_access=False,  # Paywalled
            primary_category="fondements",
            secondary_categories=[],
            keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
        )

        assert validate_result["valid"] is True
        token = validate_result["token"]

        # Save with null full text
        save_result = save_article(
            article_id="test-article-3",
            validation_token=token,
            source="Paywalled Journal",
            doi="10.1234/paywalled",
            translated_title="Titre pour article payant",
            translated_summary="Résumé traduit pour un article derrière un paywall.",
            translated_full_text=None,  # No full text for paywalled
            flags=[{"code": "PAYWALL", "detail": "Full text behind paywall"}],
        )

        assert save_result["success"] is True

        # Verify in database
        article = db_with_articles.get_article_by_id("test-article-3")
        assert article["processing_status"] == "translated"

        translation = db_with_articles.execute(
            "SELECT * FROM translations WHERE article_id = ? AND target_language = ?",
            ("test-article-3", "fr")
        ).fetchone()
        assert translation["translated_title"] == "Titre pour article payant"
        assert translation["translated_full_text"] is None

    def test_paywalled_no_quality_checks(self, db_with_articles):
        """Quality checks should be skipped for paywalled articles."""
        from mcp_server.tools import validate_classification, save_article

        # Validate
        validate_result = validate_classification(
            article_id="test-article-3",
            method="synthesis",
            voice="practitioner",
            peer_reviewed=False,
            open_access=False,
            primary_category="prise_en_charge",
            secondary_categories=[],
            keywords=["PDA", "autism", "management", "families", "support"],
        )
        token = validate_result["token"]

        # Save — should succeed without quality check issues
        save_result = save_article(
            article_id="test-article-3",
            validation_token=token,
            source="Paywalled Source",
            doi=None,
            translated_title="Titre",
            translated_summary="Résumé",
            translated_full_text=None,
            flags=[],
        )

        assert save_result["success"] is True
        # No warning flags since no quality checks run
        assert save_result["warning_flags"] == []


class TestSkipFlow:
    """Tests for skip flow: extraction failure → skip → next article."""

    def test_extraction_failure_prompts_skip_for_garbled_pdf(self, db_with_articles, clear_chunk_cache):
        """
        Should return skip action when PDF extraction fails.

        Note: .txt files are trusted as preprocessed, so we test with HTML
        that has garbage content (HTML extractor will detect problems).
        """
        from mcp_server.tools import get_next_article, get_chunk
        from mcp_server.pdf_extraction import CACHE_DIR

        # Create an HTML file with too-short/garbage content
        # HTML files are NOT trusted like .txt, so extraction problems will trigger
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / "test-article-1.html"
        cache_path.write_text("<html><body>Too short.</body></html>")

        try:
            get_next_article()
            result = get_chunk("test-article-1", 1)

            assert result.get("error") is True
            assert result.get("error_code") == "EXTRACTION_FAILED"
            assert "TOOSHORT" in result.get("problems", [])
            assert "skip_article" in result.get("action", "")

        finally:
            if cache_path.exists():
                cache_path.unlink()

    def test_skip_article_workflow(self, db_with_articles):
        """Should successfully skip article and continue to next."""
        from mcp_server.tools import get_next_article, skip_article

        # Get first article
        result1 = get_next_article()
        article1_id = result1["article"]["id"]

        # Skip it
        skip_result = skip_article(
            article_id=article1_id,
            reason="PDF extraction failed: GARBLED",
            flag_code="PDFEXTRACT"
        )

        assert skip_result["success"] is True

        # Article should be skipped in database
        article = db_with_articles.get_article_by_id(article1_id)
        assert article["processing_status"] == "skipped"
        assert "PDFEXTRACT" in article["processing_flags"]

        # Next article should be different
        result2 = get_next_article()
        assert "article" in result2
        assert result2["article"]["id"] != article1_id

    def test_skip_does_not_increment_session_counter(self, db_with_articles):
        """Skipped articles should not count toward session pause interval."""
        from mcp_server.tools import get_next_article, skip_article, get_progress

        # Check initial state
        initial = get_progress()
        initial_count = initial["session"]["articles_processed_count"]

        # Get and skip an article
        result = get_next_article()
        skip_article(
            article_id=result["article"]["id"],
            reason="Test skip",
            flag_code="PDFEXTRACT"
        )

        # Counter should NOT have increased
        after = get_progress()
        assert after["session"]["articles_processed_count"] == initial_count

    def test_no_source_url_triggers_skip_action(self, db_with_articles, clear_chunk_cache):
        """Should prompt skip when article has no source URL or cached file."""
        from mcp_server.tools import get_next_article, get_chunk
        from mcp_server.pdf_extraction import CACHE_DIR

        # Remove any cached files for article-1
        cache_path = CACHE_DIR / "test-article-1.txt"
        pdf_path = CACHE_DIR / "test-article-1.pdf"
        for p in [cache_path, pdf_path]:
            if p.exists():
                p.unlink()

        # Set source_url to NULL
        db_with_articles.execute(
            "UPDATE articles SET source_url = NULL WHERE id = ?",
            ("test-article-1",)
        )
        db_with_articles.commit()

        get_next_article()
        result = get_chunk("test-article-1", 1)

        assert result.get("error") is True
        assert result.get("error_code") == "NO_SOURCE"


class TestMultipleArticles:
    """Tests for processing multiple articles sequentially."""

    def test_process_three_articles_sequentially(self, db_with_articles):
        """Should process 3 articles sequentially with consistent state."""
        from mcp_server.tools import (
            get_next_article, validate_classification, save_article,
            get_progress, set_human_review_interval
        )

        # Set high interval to avoid pause
        set_human_review_interval(20)

        # Check initial state
        initial_progress = get_progress()
        assert initial_progress["progress"]["pending"] >= 3

        articles_processed = []

        for i in range(3):
            # Get next article
            result = get_next_article()
            assert "article" in result, f"Iteration {i+1}: Expected article, got {result}"

            article = result["article"]
            articles_processed.append(article["id"])

            # Validate
            validate_result = validate_classification(
                article_id=article["id"],
                method="empirical",
                voice="academic",
                peer_reviewed=True,
                open_access=True if article.get("open_access") else False,
                primary_category="fondements",
                secondary_categories=[],
                keywords=["PDA", "autism", "demand avoidance", "children", f"keyword{i}"],
            )
            assert validate_result["valid"] is True

            # Save
            save_result = save_article(
                article_id=article["id"],
                validation_token=validate_result["token"],
                source=f"Journal {i+1}",
                doi=None,
                translated_title=f"Titre {i+1}",
                translated_summary=f"Résumé {i+1}",
                translated_full_text=None,
                flags=[],
            )
            assert save_result["success"] is True

        # Verify all three are translated
        for article_id in articles_processed:
            article = db_with_articles.get_article_by_id(article_id)
            assert article["processing_status"] == "translated"

        # Verify session counter
        final_progress = get_progress()
        assert final_progress["session"]["articles_processed_count"] == 3

    def test_state_consistency_across_articles(self, db_with_articles):
        """State should remain consistent as multiple articles are processed."""
        from mcp_server.tools import (
            get_next_article, validate_classification, save_article, get_progress
        )

        # Process first article
        result1 = get_next_article()
        progress1 = get_progress()
        assert progress1["progress"]["in_progress"] == 1

        token1 = validate_classification(
            article_id=result1["article"]["id"],
            method="empirical",
            voice="academic",
            peer_reviewed=True,
            open_access=True,
            primary_category="fondements",
            secondary_categories=[],
            keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
        )["token"]

        save_article(
            article_id=result1["article"]["id"],
            validation_token=token1,
            source="Journal",
            doi=None,
            translated_title="Titre",
            translated_summary="Résumé",
            translated_full_text=None,
            flags=[],
        )

        progress_after_1 = get_progress()
        assert progress_after_1["progress"]["translated"] == progress1["progress"]["translated"] + 1
        assert progress_after_1["session"]["articles_processed_count"] == 1

        # Process second article
        result2 = get_next_article()
        progress2 = get_progress()
        assert progress2["progress"]["in_progress"] == 1

        token2 = validate_classification(
            article_id=result2["article"]["id"],
            method="synthesis",
            voice="practitioner",
            peer_reviewed=False,
            open_access=True,
            primary_category="prise_en_charge",
            secondary_categories=[],
            keywords=["PDA", "autism", "management", "children", "support"],
        )["token"]

        save_article(
            article_id=result2["article"]["id"],
            validation_token=token2,
            source="Journal 2",
            doi=None,
            translated_title="Titre 2",
            translated_summary="Résumé 2",
            translated_full_text=None,
            flags=[],
        )

        progress_after_2 = get_progress()
        assert progress_after_2["progress"]["translated"] == progress_after_1["progress"]["translated"] + 1
        assert progress_after_2["session"]["articles_processed_count"] == 2

    def test_different_classification_per_article(self, db_with_articles):
        """Each article should have its own classification stored correctly."""
        from mcp_server.tools import (
            get_next_article, validate_classification, save_article
        )

        # Process two articles with different classifications
        classifications = [
            {
                "method": "empirical",
                "voice": "academic",
                "peer_reviewed": True,
                "primary_category": "evaluation",
                "secondary_categories": ["fondements"],
            },
            {
                "method": "lived_experience",
                "voice": "individual",
                "peer_reviewed": False,
                "primary_category": "trajectoire",
                "secondary_categories": [],
            },
        ]

        article_ids = []

        for i, classification in enumerate(classifications):
            result = get_next_article()
            article_id = result["article"]["id"]
            article_ids.append(article_id)

            token = validate_classification(
                article_id=article_id,
                method=classification["method"],
                voice=classification["voice"],
                peer_reviewed=classification["peer_reviewed"],
                open_access=True,
                primary_category=classification["primary_category"],
                secondary_categories=classification["secondary_categories"],
                keywords=["PDA", "autism", "demand avoidance", "children", f"key{i}"],
            )["token"]

            save_article(
                article_id=article_id,
                validation_token=token,
                source=f"Journal {i}",
                doi=None,
                translated_title=f"Titre {i}",
                translated_summary=f"Résumé {i}",
                translated_full_text=None,
                flags=[],
            )

        # Verify each article has its own classification
        article1 = db_with_articles.get_article_by_id(article_ids[0])
        assert article1["method"] == "empirical"
        assert article1["voice"] == "academic"
        assert article1["peer_reviewed"] == 1

        article2 = db_with_articles.get_article_by_id(article_ids[1])
        assert article2["method"] == "lived_experience"
        assert article2["voice"] == "individual"
        assert article2["peer_reviewed"] == 0

        # Verify categories are different
        categories1 = db_with_articles.execute(
            "SELECT category_id FROM article_categories WHERE article_id = ?",
            (article_ids[0],)
        ).fetchall()
        category_ids1 = {r["category_id"] for r in categories1}
        assert "evaluation" in category_ids1

        categories2 = db_with_articles.execute(
            "SELECT category_id FROM article_categories WHERE article_id = ?",
            (article_ids[1],)
        ).fetchall()
        category_ids2 = {r["category_id"] for r in categories2}
        assert "trajectoire" in category_ids2

    def test_complete_status_when_all_done(self, db_with_articles):
        """Should return COMPLETE status when all articles are processed."""
        from mcp_server.tools import (
            get_next_article, validate_classification, save_article,
            set_human_review_interval
        )

        # Set high interval to avoid pause
        set_human_review_interval(20)

        # Mark existing translated/skipped articles
        # test-article-4 is already 'translated'
        # test-article-5 is already 'skipped'

        # Process all pending articles
        while True:
            result = get_next_article()

            if result.get("status") == "COMPLETE":
                break

            if result.get("status") == "SESSION_PAUSE":
                # Reset and continue
                from mcp_server.tools import reset_session_counter
                reset_session_counter()
                continue

            if "article" not in result:
                break

            article = result["article"]

            token = validate_classification(
                article_id=article["id"],
                method="empirical",
                voice="academic",
                peer_reviewed=True,
                open_access=bool(article.get("open_access")),
                primary_category="fondements",
                secondary_categories=[],
                keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
            )["token"]

            save_article(
                article_id=article["id"],
                validation_token=token,
                source="Journal",
                doi=None,
                translated_title="Titre",
                translated_summary="Résumé",
                translated_full_text=None,
                flags=[],
            )

        # Final call should be COMPLETE
        final_result = get_next_article()
        assert final_result.get("status") == "COMPLETE"
        assert "translated" in final_result
        assert final_result["message"] == "All articles processed."


class TestTokenExpiry:
    """Tests for token expiry in integration context (per D8: 30-minute expiry)."""

    def test_expired_token_rejected_in_full_workflow(self, db_with_articles):
        """
        Should reject expired token and require re-validation.

        Per D8: Tokens expire after 30 minutes. This tests the full workflow
        where a token expires between validate and save.
        """
        from datetime import datetime, timedelta
        from mcp_server.tools import get_next_article, validate_classification, save_article

        # Get article and validate
        result = get_next_article()
        article = result["article"]

        validate_result = validate_classification(
            article_id=article["id"],
            method="empirical",
            voice="academic",
            peer_reviewed=True,
            open_access=True,
            primary_category="fondements",
            secondary_categories=[],
            keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
        )

        assert validate_result["valid"] is True
        token = validate_result["token"]

        # Manually expire the token by backdating created_at
        db_with_articles.execute(
            "UPDATE validation_tokens SET created_at = ? WHERE token = ?",
            ((datetime.utcnow() - timedelta(minutes=31)).isoformat(), token)
        )
        db_with_articles.commit()

        # Attempt to save with expired token
        save_result = save_article(
            article_id=article["id"],
            validation_token=token,
            source="Test Journal",
            doi=None,
            translated_title="Titre",
            translated_summary="Résumé",
            translated_full_text=None,
            flags=[],
        )

        # Should be rejected
        assert save_result["success"] is False
        assert save_result["error"] == "INVALID_TOKEN"
        assert "expired" in save_result["message"].lower()

        # Article should still be in_progress (not translated)
        article_after = db_with_articles.get_article_by_id(article["id"])
        assert article_after["processing_status"] == "in_progress"

    def test_revalidation_after_expiry_succeeds(self, db_with_articles):
        """
        After token expiry, re-validation should produce a new valid token.

        This tests the recovery path when a token expires.
        """
        from datetime import datetime, timedelta
        from mcp_server.tools import get_next_article, validate_classification, save_article

        # Get article and validate
        result = get_next_article()
        article = result["article"]

        first_validate = validate_classification(
            article_id=article["id"],
            method="empirical",
            voice="academic",
            peer_reviewed=True,
            open_access=True,
            primary_category="fondements",
            secondary_categories=[],
            keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
        )

        first_token = first_validate["token"]

        # Expire the first token
        db_with_articles.execute(
            "UPDATE validation_tokens SET created_at = ? WHERE token = ?",
            ((datetime.utcnow() - timedelta(minutes=31)).isoformat(), first_token)
        )
        db_with_articles.commit()

        # Re-validate to get a new token
        second_validate = validate_classification(
            article_id=article["id"],
            method="empirical",
            voice="academic",
            peer_reviewed=True,
            open_access=True,
            primary_category="fondements",
            secondary_categories=[],
            keywords=["PDA", "autism", "demand avoidance", "children", "assessment"],
        )

        assert second_validate["valid"] is True
        second_token = second_validate["token"]
        assert second_token != first_token  # Should be a new token

        # Save with new token should succeed
        save_result = save_article(
            article_id=article["id"],
            validation_token=second_token,
            source="Test Journal",
            doi=None,
            translated_title="Titre",
            translated_summary="Résumé",
            translated_full_text=None,
            flags=[],
        )

        assert save_result["success"] is True

        # Article should now be translated
        article_after = db_with_articles.get_article_by_id(article["id"])
        assert article_after["processing_status"] == "translated"


class TestArticleIngestion:
    """Tests for article ingestion workflow: ingest_article → set_article_url (optional)."""

    @pytest.fixture
    def intake_dir(self, tmp_path):
        """Create a temporary intake directory with a test PDF."""
        from mcp_server.tools import INTAKE_DIR, PROJECT_ROOT
        import shutil

        # Create intake directory
        intake_path = PROJECT_ROOT / "intake" / "articles"
        intake_path.mkdir(parents=True, exist_ok=True)

        yield intake_path

        # Cleanup any test files we created
        for f in intake_path.glob("test-*.pdf"):
            f.unlink()

    @pytest.fixture
    def sample_pdf(self, intake_dir):
        """Create a sample PDF file in intake directory."""
        # Copy a real PDF for testing (use the one from external/ if available)
        from mcp_server.tools import PROJECT_ROOT

        source_pdf = PROJECT_ROOT / "external" / "pda" / "An examination of the behavioural features associated with PDA (O'Nions 2013).pdf"

        if source_pdf.exists():
            test_pdf = intake_dir / "test-onions-2013.pdf"
            import shutil
            shutil.copy(source_pdf, test_pdf)
            yield test_pdf
            if test_pdf.exists():
                test_pdf.unlink()
        else:
            pytest.skip("Real PDF not available for ingestion tests")

    def test_ingest_article_creates_pending_record(self, db_with_articles, sample_pdf):
        """Should create article directly in pending status (ready for translation)."""
        from mcp_server.tools import ingest_article
        from mcp_server.pdf_extraction import CACHE_DIR

        result = ingest_article(sample_pdf.name)

        assert result["success"] is True
        assert "article" in result
        assert result["article"]["id"]  # ID was generated
        assert result["article"]["open_access"] is True  # We have the PDF

        # Check database record — should be 'pending', ready for translation
        article = db_with_articles.get_article_by_id(result["article"]["id"])
        assert article is not None
        assert article["processing_status"] == "pending"

        # Check PDF was cached
        cache_path = CACHE_DIR / f"{result['article']['id']}.pdf"
        assert cache_path.exists()

        # Cleanup
        if cache_path.exists():
            cache_path.unlink()
        db_with_articles.execute(
            "DELETE FROM articles WHERE id = ?",
            (result["article"]["id"],)
        )
        db_with_articles.commit()

    def test_ingest_article_auto_populates_url_from_doi(self, db_with_articles, sample_pdf):
        """Should auto-populate source_url from DOI when found in PDF."""
        from mcp_server.tools import ingest_article
        from mcp_server.pdf_extraction import CACHE_DIR

        result = ingest_article(sample_pdf.name)

        assert result["success"] is True
        article_id = result["article"]["id"]

        # If DOI was found, source_url should be auto-populated
        article = db_with_articles.get_article_by_id(article_id)
        if result["article"].get("doi"):
            assert article["source_url"] is not None
            assert article["source_url"].startswith("https://doi.org/")

        # Cleanup
        cache_path = CACHE_DIR / f"{article_id}.pdf"
        if cache_path.exists():
            cache_path.unlink()
        db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        db_with_articles.commit()

    def test_set_article_url_updates_url(self, db_with_articles, sample_pdf):
        """Should update source_url when set_article_url is called."""
        from mcp_server.tools import ingest_article, set_article_url
        from mcp_server.pdf_extraction import CACHE_DIR

        # Ingest article
        ingest_result = ingest_article(sample_pdf.name)
        article_id = ingest_result["article"]["id"]

        # Set/update URL
        url_result = set_article_url(
            article_id,
            "https://example.com/article.pdf"
        )

        assert url_result["success"] is True
        assert url_result["source_url"] == "https://example.com/article.pdf"

        # Check database
        article = db_with_articles.get_article_by_id(article_id)
        assert article["source_url"] == "https://example.com/article.pdf"
        # Status should still be pending (URL doesn't change status)
        assert article["processing_status"] == "pending"

        # Cleanup
        cache_path = CACHE_DIR / f"{article_id}.pdf"
        if cache_path.exists():
            cache_path.unlink()
        db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        db_with_articles.commit()

    def test_set_article_url_validates_url_format(self, db_with_articles, sample_pdf):
        """Should reject invalid URL formats."""
        from mcp_server.tools import ingest_article, set_article_url
        from mcp_server.pdf_extraction import CACHE_DIR

        # Ingest article
        ingest_result = ingest_article(sample_pdf.name)
        article_id = ingest_result["article"]["id"]

        # Try invalid URL
        url_result = set_article_url(article_id, "not-a-url")

        assert url_result["success"] is False
        assert url_result["error"] == "INVALID_URL"

        # Cleanup
        cache_path = CACHE_DIR / f"{article_id}.pdf"
        if cache_path.exists():
            cache_path.unlink()
        db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        db_with_articles.commit()

    def test_set_article_url_rejects_nonexistent_article(self, db_with_articles):
        """Should reject URL update for non-existent article."""
        from mcp_server.tools import set_article_url

        result = set_article_url("nonexistent-article", "https://example.com/article.pdf")

        assert result["success"] is False
        assert result["error"] == "NOT_FOUND"

    def test_ingest_article_file_not_found(self, db_with_articles):
        """Should return error for non-existent file."""
        from mcp_server.tools import ingest_article

        result = ingest_article("does-not-exist.pdf")

        assert result["success"] is False
        assert result["error"] == "FILE_NOT_FOUND"

    def test_ingest_article_rejects_non_pdf(self, intake_dir, db_with_articles):
        """Should reject non-PDF files."""
        from mcp_server.tools import ingest_article

        # Create a text file
        txt_file = intake_dir / "test-file.txt"
        txt_file.write_text("This is not a PDF")

        try:
            result = ingest_article("test-file.txt")
            assert result["success"] is False
            assert result["error"] == "INVALID_FILE"
        finally:
            if txt_file.exists():
                txt_file.unlink()

    def test_ingest_article_generates_unique_ids(self, db_with_articles, sample_pdf, intake_dir):
        """Should generate unique IDs when duplicate titles exist."""
        from mcp_server.tools import ingest_article
        from mcp_server.pdf_extraction import CACHE_DIR
        import shutil

        # Ingest first copy
        result1 = ingest_article(sample_pdf.name)
        article_id1 = result1["article"]["id"]

        # Create a copy with same content (different filename)
        copy_pdf = intake_dir / "test-copy.pdf"
        shutil.copy(sample_pdf, copy_pdf)

        try:
            # Ingest second copy
            result2 = ingest_article("test-copy.pdf")
            article_id2 = result2["article"]["id"]

            # IDs should be different (second should have -2 suffix)
            assert article_id1 != article_id2
            assert article_id2.endswith("-2") or article_id2 != article_id1

        finally:
            # Cleanup
            for aid in [article_id1, article_id2]:
                cache_path = CACHE_DIR / f"{aid}.pdf"
                if cache_path.exists():
                    cache_path.unlink()
                db_with_articles.execute("DELETE FROM articles WHERE id = ?", (aid,))
            db_with_articles.commit()
            if copy_pdf.exists():
                copy_pdf.unlink()

    def test_ingested_article_appears_in_queue(self, db_with_articles, sample_pdf):
        """Should appear in pending queue immediately after ingestion."""
        from mcp_server.tools import ingest_article, get_progress
        from mcp_server.pdf_extraction import CACHE_DIR

        # Check initial pending count
        initial_progress = get_progress()
        initial_pending = initial_progress["progress"]["pending"]

        # Ingest article
        ingest_result = ingest_article(sample_pdf.name)
        assert ingest_result["success"] is True
        article_id = ingest_result["article"]["id"]

        # Article should be in pending status immediately
        article = db_with_articles.get_article_by_id(article_id)
        assert article["processing_status"] == "pending"

        # Pending count should include the new article
        final_progress = get_progress()
        assert final_progress["progress"]["pending"] == initial_pending + 1

        # Cleanup
        cache_path = CACHE_DIR / f"{article_id}.pdf"
        if cache_path.exists():
            cache_path.unlink()
        db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        db_with_articles.commit()

    def test_set_article_url_works_any_status(self, db_with_articles, sample_pdf):
        """Should allow setting URL regardless of article status."""
        from mcp_server.tools import ingest_article, set_article_url, get_next_article
        from mcp_server.pdf_extraction import CACHE_DIR

        # Ingest article
        ingest_result = ingest_article(sample_pdf.name)
        article_id = ingest_result["article"]["id"]

        # Move to in_progress
        db_with_articles.execute(
            "UPDATE articles SET processing_status = 'in_progress' WHERE id = ?",
            (article_id,)
        )
        db_with_articles.commit()

        # Should still be able to set URL
        url_result = set_article_url(article_id, "https://example.com/article.pdf")
        assert url_result["success"] is True

        # Cleanup
        cache_path = CACHE_DIR / f"{article_id}.pdf"
        if cache_path.exists():
            cache_path.unlink()
        db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        db_with_articles.commit()

    def test_ingest_article_generates_summary(self, db_with_articles, sample_pdf):
        """Should extract and store summary from PDF content."""
        from mcp_server.tools import ingest_article
        from mcp_server.pdf_extraction import CACHE_DIR

        # sample_pdf fixture creates a PDF with test content
        result = ingest_article(sample_pdf.name)
        assert result["success"] is True
        article_id = result["article"]["id"]

        try:
            # Check summary was generated (may be None if PDF text is too short)
            article = db_with_articles.get_article_by_id(article_id)
            # The summary_original field should exist in response
            # It may be None if PDF content is minimal, but the field should be set
            assert "summary_preview" in result["article"]

        finally:
            # Cleanup
            cache_path = CACHE_DIR / f"{article_id}.pdf"
            if cache_path.exists():
                cache_path.unlink()
            db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            db_with_articles.commit()

    def test_search_article_url_returns_search_hints(self, db_with_articles, sample_pdf):
        """Should return article details for URL search when URL is missing."""
        from mcp_server.tools import ingest_article, search_article_url
        from mcp_server.pdf_extraction import CACHE_DIR

        # Ingest article without DOI (no auto URL)
        ingest_result = ingest_article(sample_pdf.name)
        article_id = ingest_result["article"]["id"]

        try:
            # Search should return hints
            search_result = search_article_url(article_id)
            assert search_result["success"] is True
            assert search_result["has_url"] is False
            assert "article_id" in search_result
            assert "title" in search_result
            assert "search_query" in search_result
            assert "instructions" in search_result

        finally:
            # Cleanup
            cache_path = CACHE_DIR / f"{article_id}.pdf"
            if cache_path.exists():
                cache_path.unlink()
            db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            db_with_articles.commit()

    def test_search_article_url_indicates_existing_url(self, db_with_articles, sample_pdf):
        """Should indicate when URL already exists."""
        from mcp_server.tools import ingest_article, set_article_url, search_article_url
        from mcp_server.pdf_extraction import CACHE_DIR

        # Ingest and set URL
        ingest_result = ingest_article(sample_pdf.name)
        article_id = ingest_result["article"]["id"]
        set_article_url(article_id, "https://example.com/article")

        try:
            # Search should indicate URL exists
            search_result = search_article_url(article_id)
            assert search_result["success"] is True
            assert search_result["has_url"] is True
            assert search_result["current_url"] == "https://example.com/article"

        finally:
            # Cleanup
            cache_path = CACHE_DIR / f"{article_id}.pdf"
            if cache_path.exists():
                cache_path.unlink()
            db_with_articles.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            db_with_articles.commit()

    def test_search_article_url_not_found(self, db_with_articles):
        """Should return error for nonexistent article."""
        from mcp_server.tools import search_article_url

        result = search_article_url("nonexistent-article-id")
        assert result["success"] is False
        assert result["error"] == "NOT_FOUND"


class TestSummaryExtraction:
    """Tests for the _extract_summary_from_text helper function."""

    def test_extracts_substantive_content(self):
        """Should skip headers and extract substantive paragraphs."""
        from mcp_server.tools import _extract_summary_from_text

        text = """
Short Header

DOI: 10.1234/test

INTRODUCTION

Pathological Demand Avoidance (PDA) is a behavioral profile within autism. This profile is characterized by extreme avoidance of everyday demands and expectations. Research has shown significant overlap with anxiety disorders.

The study examines these patterns in detail.
"""
        summary = _extract_summary_from_text(text, max_words=50)
        assert summary is not None
        # Should not include DOI or INTRODUCTION header
        assert "DOI:" not in summary
        assert "INTRODUCTION" not in summary
        # Should include substantive content
        assert "Pathological Demand Avoidance" in summary

    def test_respects_max_words(self):
        """Should truncate to approximately max_words."""
        from mcp_server.tools import _extract_summary_from_text

        text = """
This is a test paragraph with many words that should be truncated. The paragraph continues with more content about various topics including research methodology and findings.

Another paragraph continues the discussion with additional details about the subject matter at hand. This provides more context.

A third paragraph adds even more content that exceeds our word limit substantially.
"""
        summary = _extract_summary_from_text(text, max_words=20)
        assert summary is not None
        word_count = len(summary.split())
        # Should be around max_words (allowing some flexibility for sentence boundaries)
        assert word_count <= 30

    def test_handles_empty_text(self):
        """Should return None for empty or too-short text."""
        from mcp_server.tools import _extract_summary_from_text

        assert _extract_summary_from_text("") is None
        assert _extract_summary_from_text("   ") is None
        assert _extract_summary_from_text("Short") is None

    def test_skips_metadata_paragraphs(self):
        """Should skip paragraphs that look like metadata."""
        from mcp_server.tools import _extract_summary_from_text

        text = """
© 2024 Publisher. All rights reserved.

ISSN: 1234-5678

This is the actual content of the article which discusses important research findings about the topic at hand. It continues with detailed analysis.
"""
        summary = _extract_summary_from_text(text, max_words=100)
        assert summary is not None
        assert "©" not in summary
        assert "ISSN" not in summary
        assert "actual content" in summary
