"""
MCP Server entry point for the Translation Machine.

This server provides tools for the translation pipeline:
- get_next_article() — get next article to translate
- get_progress() — get translation progress statistics
- get_chunk() — get a chunk of article text for translation (Phase 2)
- validate_classification() — validate article classification (Phase 4)
- save_article() — save translated article (Phase 4)
- skip_article() — skip an article with reason
- set_human_review_interval() — configure review interval
- reset_session_counter() — reset after human review
- ingest_article() — add new article from intake/ folder (Phase 6)
- set_article_url() — set/update source URL for an article (Phase 6)

Usage:
    python -m mcp_server.server

Or run via the entry point:
    pda-mcp
"""

import functools
import json
import logging
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from .database import get_database
from .taxonomy import get_taxonomy
from . import tools
from . import preprocessing


# --- Logging Configuration ---
# Logs to both stderr (for Claude Desktop) AND file (for post-mortem debugging)

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "mcp.log"),
    ],
)
logger = logging.getLogger(__name__)


# --- Tool Call Logging Decorator ---

def log_tool_call(func: Callable) -> Callable:
    """
    Decorator to log entry/exit of MCP tool calls.

    If a conversation dies mid-session, the log will show the last TOOL_START
    without a corresponding TOOL_END — that's our culprit.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tool_name = func.__name__

        # Log entry with args (truncate large values)
        args_repr = repr(args)[:200] + "..." if len(repr(args)) > 200 else repr(args)
        kwargs_repr = repr(kwargs)[:200] + "..." if len(repr(kwargs)) > 200 else repr(kwargs)
        logger.info(f"TOOL_START: {tool_name} args={args_repr} kwargs={kwargs_repr}")

        try:
            result = func(*args, **kwargs)

            # Truncate result for logging if large
            result_str = str(result)
            result_preview = result_str[:500] + "..." if len(result_str) > 500 else result_str
            logger.info(f"TOOL_END: {tool_name} success=True result_preview={result_preview}")

            return result
        except Exception as e:
            logger.exception(f"TOOL_END: {tool_name} success=False error={e}")
            raise

    return wrapper


# Create the MCP server
mcp = FastMCP("PDA Translation Machine")


# --- Tool: get_next_article ---

@mcp.tool()
@log_tool_call
def get_next_article() -> dict[str, Any]:
    """
    Get the next article to translate.

    Returns the next article needing work, plus fresh taxonomy data.
    Prioritizes in_progress (crash recovery) over pending.
    Checks human_review_interval — returns SESSION_PAUSE if limit reached.

    Response contains:
    - article: Article metadata (id, source_title, source_url, summary_original, open_access, doi)
    - progress: Counts by status (pending, in_progress, translated, skipped)
    - taxonomy: Valid values for method, voice, categories
    - workflow_reminder: Steps to follow

    Or returns SESSION_PAUSE / COMPLETE status if applicable.
    """
    return tools.get_next_article()


# --- Tool: get_progress ---

@mcp.tool()
@log_tool_call
def get_progress() -> dict[str, Any]:
    """
    Get translation progress statistics.

    Returns counts by processing_status and session state.

    Response contains:
    - progress: Counts (pending, in_progress, translated, skipped, total)
    - session: Current session state (articles_processed_count, human_review_interval, remaining_before_pause)
    """
    return tools.get_progress()


# --- Tool: skip_article ---

@mcp.tool()
@log_tool_call
def skip_article(article_id: str, reason: str, flag_code: str) -> dict[str, Any]:
    """
    Skip an article with a reason and flag code.

    Use this when:
    - PDF extraction fails (flag_code: PDFEXTRACT)
    - Article is paywalled with no summary (flag_code: PAYWALL)
    - Article is not relevant (flag_code: SKIP)
    - Quality issues prevent translation (flag_code: QUALITY)

    Does NOT increment session counter — skips don't count toward review interval.

    Args:
        article_id: The article ID to skip
        reason: Human-readable explanation
        flag_code: Flag code from taxonomy.yaml processing_flags

    Returns:
        {"success": true, "article_id": "..."}
    """
    return tools.skip_article(article_id, reason, flag_code)


# --- Tool: get_chunk (Phase 2) ---

@mcp.tool()
@log_tool_call
def get_chunk(article_id: str, chunk_number: int) -> dict[str, Any]:
    """
    Get a chunk of article text for translation.

    Returns one chunk (3-5 paragraphs) of the article.
    First call triggers PDF extraction and caching.

    WORKFLOW FOR EACH CHUNK:
    1. Read the instruction field — it contains translation rules
    2. Translate the chunk faithfully using provided glossary terms
    3. Append translation to your running translated_chunks list
    4. Note any classification signals (method, voice, peer_reviewed)
    5. Note any flags (TBL if tables, FIG if figures, AMBIG if unclear)
    6. Call get_chunk(article_id, chunk_number + 1)
    7. Repeat until response contains "complete": true

    Args:
        article_id: The article ID from get_next_article()
        chunk_number: Which chunk to retrieve (1-indexed)

    Returns on success (more chunks):
        chunk_number, total_chunks, text, glossary_terms, instruction, complete=false

    Returns on success (no more chunks):
        complete=true, total_chunks, next_step

    Returns on error:
        error=true, error_code, problems, action
    """
    return tools.get_chunk(article_id, chunk_number)


# --- Tool: set_human_review_interval ---

@mcp.tool()
@log_tool_call
def set_human_review_interval(interval: int) -> dict[str, Any]:
    """
    Set how many articles to process before pausing for human review.

    Range: 1-20. Default: 5.

    Recommended ramp-up:
    - First 5 articles: interval = 1 (approve each one)
    - Next 10 articles: interval = 3
    - Steady state: interval = 5-10

    Args:
        interval: Number of articles before SESSION_PAUSE (1-20)

    Returns:
        {"success": true, "interval": 5}
    """
    return tools.set_human_review_interval(interval)


# --- Tool: reset_session_counter ---

@mcp.tool()
@log_tool_call
def reset_session_counter() -> dict[str, Any]:
    """
    Reset the session counter after human review.

    Call this after reviewing articles in /admin to continue processing.
    Counter also auto-resets at local midnight.

    Returns:
        {"success": true, "message": "Session counter reset."}
    """
    return tools.reset_session_counter()


# --- Tool: validate_classification (Phase 4) ---

@mcp.tool()
@log_tool_call
def validate_classification(
    article_id: str,
    method: str,
    voice: str,
    peer_reviewed: bool,
    source: str,
    primary_category: str,
    secondary_categories: list[str],
    keywords: list[str],
) -> dict[str, Any]:
    """
    Validate article classification and get a validation token.

    Call this after translating all chunks. Returns a token needed for save_article().

    Args:
        article_id: The article ID
        method: One of: empirical, synthesis, theoretical, lived_experience
        voice: One of: academic, practitioner, organization, individual
        peer_reviewed: True if peer-reviewed
        source: Journal/institution name
        primary_category: Main category ID
        secondary_categories: Additional category IDs
        keywords: 3-7 keywords for search

    Returns on success:
        {"valid": true, "validation_token": "...", "next_step": "Call save_article()..."}

    Returns on failure:
        {"valid": false, "errors": [...], "action": "Fix errors and retry"}
    """
    return tools.validate_classification(
        article_id=article_id,
        method=method,
        voice=voice,
        peer_reviewed=peer_reviewed,
        source=source,
        primary_category=primary_category,
        secondary_categories=secondary_categories,
        keywords=keywords,
    )


# --- Tool: save_article (Phase 4) ---

@mcp.tool()
@log_tool_call
def save_article(
    article_id: str,
    validation_token: str,
    translated_title: str,
    translated_summary: str,
    translated_full_text: str | None,
    flags: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Save translated article with quality checks.

    Requires a validation_token from validate_classification().
    Runs quality checks and saves if passing.

    Args:
        article_id: The article ID
        validation_token: Token from validate_classification()
        translated_title: French title
        translated_summary: French summary
        translated_full_text: French full text (or null if summary-only)
        flags: List of {"code": "...", "detail": "..."} for any issues

    Returns on success:
        {"success": true, "warning_flags": [...], "next_step": "..."}

    Returns on quality failure:
        {"success": false, "error": "QUALITY_CHECK_FAILED", "blocking_flags": [...], "action": "..."}
    """
    return tools.save_article(
        article_id=article_id,
        validation_token=validation_token,
        translated_title=translated_title,
        translated_summary=translated_summary,
        translated_full_text=translated_full_text,
        flags=flags,
    )


# --- Tool: ingest_article (Phase 6) ---

@mcp.tool()
@log_tool_call
def ingest_article(filename: str) -> dict[str, Any]:
    """
    Ingest a PDF from intake/articles/ into the database.

    Creates article with status 'pending' — ready for translation immediately.
    If DOI is found, source_url is auto-populated from doi.org.

    Args:
        filename: Name of PDF in intake/articles/ (e.g., "smith-2024-pda.pdf")

    Returns on success:
        {
            "success": true,
            "article": {"id": "...", "source_title": "...", "doi": "...", "source_url": "..."},
            "next_step": "Article added to queue. Call get_next_article() to begin."
        }

    Returns on failure:
        {"success": false, "error": "FILE_NOT_FOUND|EXTRACTION_FAILED|DUPLICATE", "details": "..."}
    """
    return tools.ingest_article(filename)


# --- Tool: search_article_url (Phase 6) ---

@mcp.tool()
@log_tool_call
def search_article_url(article_id: str) -> dict[str, Any]:
    """
    Get article details to search for its canonical URL.

    Use this for articles without a source_url. Returns the title and search hints.
    After finding the URL via web search, call set_article_url() to save it.

    Args:
        article_id: The article ID

    Returns:
        Article details for searching, or indicates URL already exists.
    """
    return tools.search_article_url(article_id)


# --- Tool: set_article_url (Phase 6) ---

@mcp.tool()
@log_tool_call
def set_article_url(article_id: str, source_url: str) -> dict[str, Any]:
    """
    Set or update the source URL for an article.

    Can be called at any time — before, during, or after translation.
    URL is needed before publishing but not required for translation.

    Args:
        article_id: The article ID
        source_url: The canonical URL where the original can be found

    Returns on success:
        {"success": true, "article_id": "...", "source_url": "...", "message": "Source URL updated."}

    Returns on failure:
        {"success": false, "error": "NOT_FOUND|INVALID_URL", "details": "..."}
    """
    return tools.set_article_url(article_id, source_url)


# --- Preprocessing Tools ---

@mcp.tool()
@log_tool_call
def list_intake_pdfs() -> dict[str, Any]:
    """
    List PDFs in intake/articles/ awaiting processing.

    Returns PDFs that haven't been extracted yet, plus those already extracted.
    Use this to see what's available before calling extract_pdf().
    """
    return preprocessing.list_intake_pdfs()


@mcp.tool()
@log_tool_call
def list_datalab_files() -> dict[str, Any]:
    """
    List datalab-output-*.json files in cache/articles/ awaiting parsing.

    These are files that have been extracted (either via API or manual download)
    but not yet parsed to generate a proper slug.

    Use this to see manually downloaded Datalab files before calling parse_datalab_file().
    """
    return preprocessing.list_datalab_files()


@mcp.tool()
@log_tool_call
def parse_datalab_file(filename: str) -> dict[str, Any]:
    """
    Parse a Datalab output file and generate a proper slug from metadata.

    This is the key function for processing extracted PDFs:
    1. Reads the datalab-output-*.json file
    2. Runs the parser to extract title, authors, year
    3. Generates a proper slug: {author}-{year}-{title}
    4. Renames the file to {slug}.json
    5. Creates {slug}_parsed.json
    6. Returns the slug for continuing with Step 4

    Use this after:
    - extract_pdf() outputs a temp file
    - Manually downloading from Datalab website

    Args:
        filename: Exact filename in cache/articles/ (e.g., "datalab-output-abc123.json")
    """
    return preprocessing.parse_datalab_file(filename)


@mcp.tool()
@log_tool_call
def extract_pdf(filename: str) -> dict[str, Any]:
    """
    Submit PDF to Datalab Marker API and wait for completion.

    This is a blocking operation that typically takes 30-120 seconds.
    Saves output as a temp file (datalab-output-{request_id}.json).
    Call parse_datalab_file() next to generate proper slug from metadata.

    Requires DATALAB_API_KEY environment variable.

    Args:
        filename: Name of PDF in intake/articles/ (e.g., "smith-2024-pda.pdf")
    """
    return preprocessing.extract_pdf(filename)


@mcp.tool()
@log_tool_call
def parse_extracted_article(slug: str) -> dict[str, Any]:
    """
    Run mechanical parser on Datalab JSON, create structured article data.

    Extracts title, authors, abstract, body, references from the raw blocks.

    Args:
        slug: The article slug (filename without extension, slugified)
    """
    return preprocessing.parse_extracted_article(slug)


@mcp.tool()
@log_tool_call
def get_article_for_review(slug: str) -> dict[str, Any]:
    """
    Get parsed article data + raw blocks for Claude to review and classify.

    Returns:
    - parser_extracted: What the mechanical parser found (claims to verify)
    - raw_blocks: First 2 pages of content to verify extractions and derive classifications
    - classification_guide: Definitions for method/voice/peer_reviewed (no suggestions)

    Claude must:
    1. Verify parser extractions against raw_blocks (confirm or correct title/authors/abstract)
    2. Derive year from raw_blocks if not extracted
    3. Derive method by reading the content (empirical/synthesis/theoretical/lived_experience)
    4. Derive voice by reading the content (academic/practitioner/organization/individual)
    5. Derive peer_reviewed from evidence in raw_blocks (DOI, journal name, etc.)

    NO SUGGESTIONS PROVIDED. Claude must derive all classifications from the content.

    Args:
        slug: The article slug
    """
    return preprocessing.get_article_for_review(slug)


@mcp.tool()
@log_tool_call
def complete_article_review(
    slug: str,
    title: str,
    authors: str,
    year: str,
    abstract_confirmed: bool,
    method: str,
    voice: str,
    peer_reviewed: bool,
    corrected_abstract: str | None = None,
    citation: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Complete the article review by stating all values explicitly.

    You must STATE the title, authors, and year — not just confirm them.
    Compare parser_extracted with raw_blocks and write what you found.

    You must DERIVE method, voice, and peer_reviewed from reading the content.
    No suggestions are provided. Read the raw_blocks and decide.

    Args:
        slug: The article slug
        title: REQUIRED — state the title you found in raw_blocks
        authors: REQUIRED — state the authors (e.g., "E. O'Nions, J. Gould, P. Christie")
        year: REQUIRED — publication year (4 digits, e.g., "2018")
        abstract_confirmed: True if parser's abstract is correct, False if correcting
        method: REQUIRED — one of: empirical, synthesis, theoretical, lived_experience
        voice: REQUIRED — one of: academic, practitioner, organization, individual
        peer_reviewed: REQUIRED — True if peer-reviewed, False otherwise
        corrected_abstract: Only required if abstract_confirmed=False
        citation: Optional full citation string
        notes: Optional notes about issues or flags
    """
    return preprocessing.complete_article_review(
        slug=slug,
        title=title,
        authors=authors,
        year=year,
        abstract_confirmed=abstract_confirmed,
        method=method,
        voice=voice,
        peer_reviewed=peer_reviewed,
        corrected_abstract=corrected_abstract,
        citation=citation,
        notes=notes,
    )


@mcp.tool()
@log_tool_call
def get_body_for_review(slug: str, chunk: int = 0) -> dict[str, Any]:
    """
    Get body_html in chunks for structural review.

    Returns paragraphs with flagged issues:
    - ORPHAN: Starts lowercase — split from previous
    - INCOMPLETE: No ending punctuation — continues in next
    - CAPTION: Figure/table caption in body
    - PAGE_ARTIFACT: Page number/header leaked through
    - SHORT_FRAGMENT: Very short text — cruft?
    - REFERENCE_LEAK: Reference in body

    Call with chunk=0, then chunk=1, etc. until all reviewed.

    Args:
        slug: The article slug
        chunk: Which chunk to return (0-indexed, 10 paragraphs each)
    """
    return preprocessing.get_body_for_review(slug, chunk)


@mcp.tool()
@log_tool_call
def complete_body_review(
    slug: str,
    body_approved: bool,
    fixes: list[dict] | None = None,
    issues_acknowledged: list[int] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Complete the body review — confirm clean or apply fixes.

    If get_body_for_review() flagged issues, you MUST either:
    1. Fix them (provide in fixes list), OR
    2. Acknowledge them as false positives (list indices in issues_acknowledged)

    You CANNOT approve the body while ignoring flagged issues.

    Args:
        slug: The article slug
        body_approved: True if body is clean (after fixes), False if issues remain
        fixes: List of fixes. Each is {"index": N, "action": ACTION}
            Actions: "join_previous", "join_next", "delete", "replace"
            For replace: {"index": N, "action": "replace", "text": "new text"}
        issues_acknowledged: List of paragraph indices where issues are false positives
        notes: Explain why acknowledged issues are false positives
    """
    return preprocessing.complete_body_review(
        slug=slug,
        body_approved=body_approved,
        fixes=fixes,
        issues_acknowledged=issues_acknowledged,
        notes=notes,
    )


@mcp.tool()
@log_tool_call
def get_preprocessing_status() -> dict[str, Any]:
    """
    Get overview of preprocessing pipeline status.

    Returns counts of PDFs in intake, extracted/parsed in cache,
    and database status by processing_status.
    """
    return preprocessing.get_preprocessing_status()


# --- Step 4: AI Enhancement Tools ---
# These enforce sequential checking. Cannot skip steps.

@mcp.tool()
@log_tool_call
def step4_check_fields(slug: str) -> dict[str, Any]:
    """
    Step 4.1: Check for missing or empty metadata fields.

    MUST be called first in Step 4 sequence. Returns fields that need attention.

    Claude must review each field and either:
    - Confirm the field is correctly empty (not in source document)
    - Provide the missing value extracted from raw HTML

    Args:
        slug: The article slug

    Returns:
        - fields_status: Dict of field -> status (ok/missing/empty) + value
        - raw_blocks_hint: First 2 pages for extraction
        - next_step: Call step4_confirm_fields()
    """
    return preprocessing.step4_check_fields(slug)


@mcp.tool()
@log_tool_call
def step4_confirm_fields(
    slug: str,
    title: str | None = None,
    authors: str | None = None,
    year: str | None = None,
    citation: str | None = None,
    abstract: str | None = None,
    all_fields_ok: bool = False,
) -> dict[str, Any]:
    """
    Confirm or provide missing field values (Step 4.1 completion).

    If step4_check_fields() showed issues, provide the corrected values.
    If all fields were ok, set all_fields_ok=True.

    Args:
        slug: The article slug
        title: Corrected title if missing/empty
        authors: Corrected authors if missing/empty
        year: Corrected year if missing/empty
        citation: Corrected citation if missing/empty
        abstract: Corrected abstract if missing/empty
        all_fields_ok: True if step4_check_fields showed no issues

    Returns:
        - Updates _parsed.json with corrections
        - Marks 'fields' check complete
        - next_step: Call step4_check_warnings()
    """
    return preprocessing.step4_confirm_fields(
        slug=slug,
        title=title,
        authors=authors,
        year=year,
        citation=citation,
        abstract=abstract,
        all_fields_ok=all_fields_ok,
    )


@mcp.tool()
@log_tool_call
def step4_check_warnings(slug: str) -> dict[str, Any]:
    """
    Step 4.2: Check for parser warnings (orphan paragraphs, etc.).

    MUST call step4_confirm_fields() first.

    Returns warnings from the parser that need attention:
    - [ORPHAN?] — paragraph starting with lowercase, likely split
    - Other structural warnings

    Args:
        slug: The article slug

    Returns:
        - parser_warnings: Warnings from parsing
        - orphan_paragraphs: Body paragraphs flagged as orphans
        - next_step: Call step4_confirm_warnings()
    """
    return preprocessing.step4_check_warnings(slug)


@mcp.tool()
@log_tool_call
def step4_confirm_warnings(
    slug: str,
    orphan_fixes: list[dict] | None = None,
    warnings_acknowledged: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Confirm warnings reviewed and apply fixes (Step 4.2 completion).

    Args:
        slug: The article slug
        orphan_fixes: Fixes for orphan paragraphs:
            [{"index": 5, "action": "join_previous"}, ...]
        warnings_acknowledged: True if warnings reviewed, no action needed
        notes: Optional notes about decisions

    Returns:
        - Applies fixes to body_html
        - Marks 'warnings' check complete
        - next_step: Call step4_check_references()
    """
    return preprocessing.step4_confirm_warnings(
        slug=slug,
        orphan_fixes=orphan_fixes,
        warnings_acknowledged=warnings_acknowledged,
        notes=notes,
    )


@mcp.tool()
@log_tool_call
def step4_check_references(slug: str) -> dict[str, Any]:
    """
    Step 4.3: Check if references were properly extracted.

    MUST call step4_confirm_warnings() first.

    Returns:
    - references_count: Number currently extracted
    - references_preview: First 5 references
    - raw_reference_hint: Raw HTML of reference section (if refs empty)
    - issues: Any problems detected

    Args:
        slug: The article slug
    """
    return preprocessing.step4_check_references(slug)


@mcp.tool()
@log_tool_call
def step4_confirm_references(
    slug: str,
    references_ok: bool = False,
    additional_references: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Confirm references check complete (Step 4.3 completion).

    Args:
        slug: The article slug
        references_ok: True if references are complete/correctly empty
        additional_references: References to add (if extracted from raw HTML)
        notes: Optional notes

    Returns:
        - Adds any additional references
        - Marks 'references' check complete
        - next_step: Call step4_check_formulas()
    """
    return preprocessing.step4_confirm_references(
        slug=slug,
        references_ok=references_ok,
        additional_references=additional_references,
        notes=notes,
    )


@mcp.tool()
@log_tool_call
def step4_check_formulas(slug: str) -> dict[str, Any]:
    """
    Step 4.4: Check for unwrapped statistical formulas needing normalization.

    MUST call step4_confirm_references() first.

    Scans body_html for statistical patterns NOT wrapped in <span class="formula">:
    - F-statistics: F(1, 156) = 4.07
    - t-tests: t(45) = 2.31
    - Chi-square: χ²(2) = 8.45
    - p-values: p < .05, p = .001
    - Effect sizes: η² = .12, d = 0.45
    - Correlations: r = .67
    - Means/SDs: M = 4.2, SD = 1.1

    Note: This is judgment-based. Ages, sample sizes don't need wrapping.
    Wrap statistical test results and their parameters.

    Args:
        slug: The article slug

    Returns:
        - paragraphs_with_unwrapped_formulas: Paragraphs + formulas found
        - total_unwrapped_formulas: Count
        - next_step: Call step4_confirm_formulas()
    """
    return preprocessing.step4_check_formulas(slug)


@mcp.tool()
@log_tool_call
def step4_confirm_formulas(
    slug: str,
    formulas_ok: bool = False,
    formula_wraps: list[dict] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Confirm formula normalization complete (Step 4.4 completion).

    Args:
        slug: The article slug
        formulas_ok: True if no formula wrapping needed
        formula_wraps: Formulas to wrap:
            [{"paragraph_index": 5, "formula_text": "F(1, 156) = 4.07"}, ...]
        notes: Optional notes about decisions

    Returns:
        - Wraps specified formulas in <span class="formula">
        - Marks 'formulas' check complete
        - next_step: Call step4_complete()
    """
    return preprocessing.step4_confirm_formulas(
        slug=slug,
        formulas_ok=formulas_ok,
        formula_wraps=formula_wraps,
        notes=notes,
    )


@mcp.tool()
@log_tool_call
def step4_complete(slug: str) -> dict[str, Any]:
    """
    Finalize Step 4 and move article to ready/ for human review.

    Can ONLY be called after all four checks are complete:
    - fields (step4_confirm_fields)
    - warnings (step4_confirm_warnings)
    - references (step4_confirm_references)
    - formulas (step4_confirm_formulas)

    Args:
        slug: The article slug

    Returns:
        - Moves _parsed.json to ready/ directory
        - Clears step4 state
        - next_step: Human reviews at /admin/review
    """
    return preprocessing.step4_complete(slug)


@mcp.tool()
@log_tool_call
def step4_reset(slug: str) -> dict[str, Any]:
    """
    Reset Step 4 state for an article, allowing it to be reprocessed from the beginning.

    Use this when:
    - Step 4 was interrupted and you need to start over
    - You want to re-run the checks after making manual changes
    - The state file is corrupted

    Note: This only clears the Step 4 progress state. It does NOT delete or modify
    the _parsed.json file. The article remains in cache/ ready for Step 4.

    Args:
        slug: The article slug

    Returns:
        - Clears step4 state file
        - next_step: Call step4_check_fields() to begin Step 4 fresh
    """
    return preprocessing.step4_reset(slug)


@mcp.tool()
@log_tool_call
def start_preprocessing(
    filename: str | None = None,
) -> dict[str, Any]:
    """
    Start or continue the preprocessing workflow.

    This is the entry point for "Let's preprocess". Shows available work
    and current status. IMPORTANT: Do not pick a file automatically —
    always ask the user which file they want to process.

    Args:
        filename: Optional specific file to process. If not provided,
                  shows available files and asks user to choose.

    Returns progress info and next_step telling you exactly what to call.
    """
    status = preprocessing.get_preprocessing_status()

    # Check for work in progress first — must complete before starting new
    if status["preprocessing"]["work_in_progress"] > 0:
        wip = list(preprocessing.CACHE_DIR.glob("*_parsed.json"))
        if wip:
            slug = wip[0].stem.replace("_parsed", "")
            parsed = json.loads(wip[0].read_text())

            if not parsed.get("article_reviewed"):
                return {
                    "status": "RESUME_REQUIRED",
                    "message": f"Article '{slug}' is partially processed. Complete it first.",
                    "slug": slug,
                    "next_step": f"Call get_article_for_review('{slug}') to continue classification.",
                }
            elif not parsed.get("body_reviewed"):
                return {
                    "status": "RESUME_REQUIRED",
                    "message": f"Article '{slug}' needs body review. Complete it first.",
                    "slug": slug,
                    "next_step": f"Call get_body_for_review('{slug}') to continue body review.",
                }

    # Gather counts for status display
    pending_reviews = status["preprocessing"]["ready_for_review"]
    pending_review_files = status["preprocessing"]["ready_files"] if pending_reviews > 0 else []
    archived_count = len(list(preprocessing.ARCHIVED_DIR.glob("*_parsed.json")))

    # If no filename specified, show available work and ASK user to choose
    if not filename:
        datalab = preprocessing.list_datalab_files()
        intake = preprocessing.list_intake_pdfs()

        if datalab["count"] == 0 and not intake["available"]:
            return {
                "status": "NO_WORK",
                "message": "No PDFs in intake/articles/ and no Datalab files to parse.",
                "counts": {
                    "archived": archived_count,
                    "pending_review": pending_reviews,
                },
                "next_step": "Add PDFs to intake/articles/ folder, or manually download from Datalab website.",
            }

        response = {
            "status": "CHOOSE_SOURCE",
            "message": "ASK THE USER which file to process. Do not pick automatically.",
            "counts": {
                "archived": archived_count,
                "pending_review": pending_reviews,
                "datalab_files": datalab["count"],
                "intake_pdfs": len(intake["available"]),
            },
            "datalab_files": datalab["files"] if datalab["count"] > 0 else [],
            "available_pdfs": intake["available"][:15] if intake["available"] else [],
            "next_step": "ASK the user which file to process. Then call parse_datalab_file('<filename>') or extract_pdf('<filename>').",
        }

        if pending_reviews > 0:
            response["pending_review_files"] = pending_review_files

        return response

    # Filename provided - check if it's a datalab file or a PDF
    if filename.startswith("datalab-output-") or filename.endswith(".json"):
        # It's a datalab file - parse it
        result = preprocessing.parse_datalab_file(filename)
    else:
        # It's a PDF - extract it
        result = preprocessing.extract_pdf(filename)

    if not result.get("success"):
        return result  # Error from extract/parse

    return result  # Has progress info


# --- Main entry point ---

def main():
    """Run the MCP server."""
    logger.info("Starting PDA Translation Machine MCP Server...")

    # Initialize on startup
    db = get_database()
    db.cleanup_expired_tokens()
    logger.info(f"Database at {db._path}")

    # Log taxonomy status
    taxonomy = get_taxonomy()
    logger.info(
        f"Taxonomy loaded: {len(taxonomy.methods)} methods, "
        f"{len(taxonomy.voices)} voices, {len(taxonomy.categories)} categories"
    )

    # Log progress
    progress = db.get_progress()
    logger.info(
        f"Progress: {progress['translated']} translated, "
        f"{progress['pending']} pending, {progress['skipped']} skipped"
    )

    # Run the server
    mcp.run()


if __name__ == "__main__":
    main()
