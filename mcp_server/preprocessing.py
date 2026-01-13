"""
Preprocessing tools for the PDA Translation Machine.

Tools for PDF extraction, parsing, and review before translation.
Claude must derive all values — no auto-fill, no suggestions.

Steps:
1. list_intake_pdfs() — see available PDFs
2. extract_pdf(filename) — submit to Datalab API
3. parse_extracted_article(slug) — mechanical parsing
4. get_article_for_review(slug) — see parsed data + raw blocks (pages 0-1)
5. complete_article_review(slug, title, authors, ...) — Claude states all values
6. get_body_for_review(slug, chunk) — review body structure in chunks
7. complete_body_review(slug, body_approved, fixes) — moves to ready/ for human review

Body issues to check:
- ORPHAN: Starts lowercase — split from previous paragraph
- INCOMPLETE: No ending punctuation — continues in next
- CAPTION: Figure/table caption leaked into body
- PAGE_ARTIFACT: Page number/header leaked through
- SHORT_FRAGMENT: Very short cruft
- REFERENCE_LEAK: Reference in body
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / 'scripts'
sys.path.insert(0, str(scripts_dir))

from parse_article_blocks import parse_blocks, extract_text

from .taxonomy import get_taxonomy
from .utils import slugify

logger = logging.getLogger(__name__)

# Directories
PROJECT_ROOT = Path(__file__).parent.parent
INTAKE_DIR = PROJECT_ROOT / "intake" / "articles"
PROCESSED_DIR = PROJECT_ROOT / "intake" / "processed"
CACHE_DIR = PROJECT_ROOT / "cache" / "articles"
READY_DIR = CACHE_DIR / "ready"      # Preprocessing complete, awaiting human review
ARCHIVED_DIR = CACHE_DIR / "archived"  # After human approval + DB insert
SESSION_FILE = CACHE_DIR / ".preprocessing_session.json"

# Preprocessing steps for visibility
STEPS = {
    "extract": {"number": 1, "name": "Extract PDF", "description": "Calling Datalab API to extract text from PDF"},
    "parse": {"number": 2, "name": "Parse Structure", "description": "Parsing HTML into title, authors, abstract, body, references"},
    "classify": {"number": 3, "name": "Classify Article", "description": "Review metadata and assign method/voice/peer_reviewed"},
    "body_review": {"number": 4, "name": "Review Body", "description": "Check body for formatting issues"},
    "ready": {"number": 5, "name": "Ready for Approval", "description": "Human reviews in /admin/review"},
}
TOTAL_STEPS = 5


def get_session() -> dict[str, Any]:
    """Load preprocessing session state."""
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except:
            pass
    return {"target_count": 1, "completed_count": 0, "current_slug": None}


def save_session(session: dict[str, Any]) -> None:
    """Save preprocessing session state."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(session, indent=2))


def clear_session() -> None:
    """Clear session when complete or cancelled."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def step_progress(step_key: str, slug: str | None = None) -> dict[str, Any]:
    """Generate step progress info for response."""
    session = get_session()
    step = STEPS[step_key]
    return {
        "step": f"{step['number']}/{TOTAL_STEPS}",
        "step_name": step["name"],
        "step_description": step["description"],
        "article_progress": f"{session['completed_count'] + 1}/{session['target_count']}",
        "slug": slug or session.get("current_slug"),
    }


def find_pdf_by_query(query: str) -> dict[str, Any]:
    """
    Find a PDF in intake by partial name match.

    Matches case-insensitively against filename. Returns best match or error.
    """
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    intake_pdfs = list(INTAKE_DIR.glob("*.pdf"))

    if not intake_pdfs:
        return {"success": False, "error": "No PDFs in intake/articles/"}

    query_lower = query.lower()

    # Exact match first
    for pdf in intake_pdfs:
        if pdf.name.lower() == query_lower or pdf.name == query:
            return {"success": True, "filename": pdf.name, "path": pdf}

    # Partial match - all words must appear
    query_words = query_lower.replace("-", " ").replace("_", " ").split()
    matches = []

    for pdf in intake_pdfs:
        name_lower = pdf.stem.lower().replace("-", " ").replace("_", " ")
        if all(word in name_lower for word in query_words):
            matches.append(pdf)

    if len(matches) == 1:
        return {"success": True, "filename": matches[0].name, "path": matches[0]}
    elif len(matches) > 1:
        return {
            "success": False,
            "error": "AMBIGUOUS",
            "message": f"'{query}' matches {len(matches)} files. Be more specific.",
            "matches": [m.name for m in matches[:5]]
        }
    else:
        return {
            "success": False,
            "error": "NOT_FOUND",
            "message": f"No PDF matching '{query}' found in intake/articles/",
            "available": [p.name for p in intake_pdfs[:10]]
        }


def list_intake_pdfs() -> dict[str, Any]:
    """
    List PDFs in intake/articles/ awaiting processing.

    Returns PDFs that haven't been extracted yet, plus those already extracted.
    Cross-references with cache/articles/*.json to determine status.
    """
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Get all PDFs in intake
    intake_pdfs = sorted(INTAKE_DIR.glob("*.pdf"))

    # Get already-extracted slugs (have .json in cache)
    extracted_slugs = {f.stem for f in CACHE_DIR.glob("*.json") if not f.stem.endswith('_parsed')}

    # Get processed PDFs count
    processed_pdfs = list(PROCESSED_DIR.glob("*.pdf"))

    available = []
    already_extracted = []

    for pdf in intake_pdfs:
        slug = slugify(pdf.stem)
        stat = pdf.stat()
        modified = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d')
        size_kb = stat.st_size // 1024

        if slug in extracted_slugs:
            already_extracted.append({
                "filename": pdf.name,
                "slug": slug,
                "size_kb": size_kb,
                "modified": modified
            })
        else:
            available.append({
                "filename": pdf.name,
                "slug": slug,
                "size_kb": size_kb,
                "modified": modified
            })

    return {
        "available": available,
        "already_extracted": already_extracted,
        "processed_count": len(processed_pdfs),
        "next_step": "Call extract_pdf('<filename>') to process a PDF, or parse_extracted_article('<slug>') for already-extracted files."
    }


def list_datalab_files() -> dict[str, Any]:
    """
    List datalab-output-*.json files in cache/articles/ awaiting parsing.

    These are files that have been extracted (either via API or manual download)
    but not yet parsed to generate a proper slug.

    Returns:
        - files: List of datalab output files with size info
        - count: Number of files
        - next_step: Instructions to parse
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Find all datalab-output files (from API or manual download)
    datalab_files = sorted(CACHE_DIR.glob("datalab-output-*.json"))

    files = []
    for f in datalab_files:
        stat = f.stat()
        files.append({
            "filename": f.name,
            "size_kb": stat.st_size // 1024,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
        })

    return {
        "files": files,
        "count": len(files),
        "next_step": "Call parse_datalab_file('<filename>') to parse and generate proper slug." if files else "No datalab files to parse. Use extract_pdf() or manually download from Datalab."
    }


def parse_datalab_file(filename: str) -> dict[str, Any]:
    """
    Parse a Datalab output file and generate a proper slug from metadata.

    This is the key function that:
    1. Reads the datalab-output-*.json file
    2. Runs the parser to extract title, authors, year
    3. Generates a proper slug: {author}-{year}-{title}
    4. Renames the file to {slug}.json
    5. Creates {slug}_parsed.json
    6. Returns the slug for continuing with Step 4

    Args:
        filename: Exact filename in cache/articles/ (e.g., "datalab-output-abc123.json")

    Returns on success:
        - slug: The generated article slug
        - parsed_path: Path to the parsed JSON
        - summary: Extracted metadata
        - next_step: Instructions for Step 4

    Returns on error:
        - error: Error code
        - details: What went wrong
        - action: How to fix it
    """
    import shutil

    # Validate filename
    json_path = CACHE_DIR / filename

    if not json_path.exists():
        # Try to find a partial match
        matches = list(CACHE_DIR.glob(f"*{filename}*"))
        if matches:
            return {
                "success": False,
                "error": "NOT_FOUND",
                "details": f"File not found: {filename}",
                "similar_files": [m.name for m in matches[:5]],
                "action": "Use the exact filename from list_datalab_files()"
            }
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"File not found: {filename}",
            "action": "Use list_datalab_files() to see available files"
        }

    # Run the parser
    try:
        result = parse_blocks(json_path)
    except Exception as e:
        logger.exception(f"Parser failed for {filename}")
        return {
            "success": False,
            "error": "PARSE_ERROR",
            "details": str(e),
            "action": "Check the JSON file format"
        }

    # Extract metadata for slug generation
    title = result.get('title') or ''
    authors = result.get('authors') or ''
    year = result.get('year') or ''

    # Validate we have enough to generate a slug
    if not title:
        return {
            "success": False,
            "error": "NO_TITLE",
            "details": "Parser could not extract a title from the document",
            "raw_hint": "Check the PDF or manually provide metadata",
            "action": "This file may need manual preprocessing"
        }

    # Generate the slug from metadata
    slug = generate_article_id(title, authors, year)

    # Check for collision with existing files
    final_json_path = CACHE_DIR / f"{slug}.json"
    final_parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if final_json_path.exists() and final_json_path != json_path:
        # Slug collision - append a suffix
        counter = 2
        base_slug = slug
        while (CACHE_DIR / f"{slug}.json").exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        final_json_path = CACHE_DIR / f"{slug}.json"
        final_parsed_path = CACHE_DIR / f"{slug}_parsed.json"
        logger.info(f"Slug collision detected, using: {slug}")

    # Rename the raw JSON to the proper slug
    if json_path != final_json_path:
        shutil.move(str(json_path), str(final_json_path))
        logger.info(f"Renamed {filename} -> {slug}.json")

    # Save the parsed result
    with open(final_parsed_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Update session
    session = get_session()
    session["current_slug"] = slug
    save_session(session)

    # Build summary
    body_html = result.get('body_html', '')
    summary = {
        "title": title[:100] + "..." if len(title) > 100 else title,
        "authors": authors,
        "year": year,
        "doi": result.get('doi'),
        "abstract_chars": len(result.get('abstract') or ''),
        "body_chars": len(body_html),
        "references_count": len(result.get('references', [])),
        "figures_count": len(result.get('figures', []))
    }

    return {
        "success": True,
        "progress": step_progress("parse", slug),
        "slug": slug,
        "json_path": str(final_json_path),
        "parsed_path": str(final_parsed_path),
        "summary": summary,
        "warnings": result.get('warnings', []),
        "next_step": f"Call step4_check_fields('{slug}') to begin AI enhancement."
    }


def extract_pdf(filename: str) -> dict[str, Any]:
    """
    Submit PDF to Datalab Marker API and wait for completion.

    This is a blocking operation that typically takes 30-120 seconds.
    Saves output as a temp file (datalab-output-{request_id}.json).
    Call parse_datalab_file() next to generate proper slug from metadata.

    Args:
        filename: Exact filename OR partial match (e.g., "O'Nions 2013" matches
                  "An examination of the behavioural features associated with PDA (O'Nions 2013).pdf")
    """
    # Try fuzzy match first
    match = find_pdf_by_query(filename)

    if not match["success"]:
        # Return the fuzzy match error with suggestions
        return {
            "success": False,
            "error": match.get("error", "NOT_FOUND"),
            "details": match.get("message", f"File not found: {filename}"),
            "matches": match.get("matches"),
            "available": match.get("available"),
            "action": "Check filename. Use list_intake_pdfs() to see available files."
        }

    pdf_path = match["path"]

    # Import extraction functions from batch_extract
    try:
        import sys
        scripts_dir = str(PROJECT_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from batch_extract import submit_pdf, poll_and_save
    except ImportError as e:
        return {
            "success": False,
            "error": "IMPORT_ERROR",
            "details": f"Could not import batch_extract.py: {e}",
            "action": "Ensure scripts/batch_extract.py is available"
        }

    api_key = os.environ.get('DATALAB_API_KEY')
    if not api_key:
        return {
            "success": False,
            "error": "NO_API_KEY",
            "details": "DATALAB_API_KEY environment variable not set",
            "action": "Set DATALAB_API_KEY environment variable"
        }

    logger.info(f"Submitting PDF for extraction: {filename}")

    try:
        result = submit_pdf(pdf_path)

        if result is None:
            return {
                "success": False,
                "error": "API_ERROR",
                "details": "submit_pdf returned None - API call may have failed",
                "action": "Check API key and network connection"
            }

        request_id = result.get('request_id')

        if not request_id:
            return {
                "success": False,
                "error": "SUBMISSION_FAILED",
                "details": f"API response: {result}",
                "action": "Check API key and try again"
            }

        # Save to temp filename using request_id (slug generated later from metadata)
        temp_filename = f"datalab-output-{request_id}.json"
        output_path = CACHE_DIR / temp_filename

        logger.info(f"Request ID: {request_id}, polling for completion...")
        success = poll_and_save(request_id, output_path)

        if not success:
            return {
                "success": False,
                "error": "EXTRACTION_FAILED",
                "details": "Polling timed out or extraction failed",
                "action": "Try again or check Datalab status"
            }

        # Read the result to get stats
        with open(output_path, 'r') as f:
            data = json.load(f)

        # Move PDF to processed folder
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        processed_path = PROCESSED_DIR / match["filename"]
        try:
            import shutil
            shutil.move(str(pdf_path), str(processed_path))
            logger.info(f"Moved {match['filename']} to intake/processed/")
            moved = True
        except Exception as move_err:
            logger.warning(f"Could not move {match['filename']} to processed: {move_err}")
            moved = False

        return {
            "success": True,
            "progress": step_progress("extract"),
            "temp_filename": temp_filename,
            "json_path": str(output_path),
            "stats": {
                "blocks": len(data.get('blocks', [])),
                "pages": data.get('page_count', 0),
                "images": data.get('images_count', 0)
            },
            "moved_to_processed": moved,
            "next_step": f"Call parse_datalab_file('{temp_filename}') to parse and generate slug."
        }

    except Exception as e:
        logger.exception(f"Extraction failed for {filename}")
        return {
            "success": False,
            "error": "DATALAB_ERROR",
            "details": str(e),
            "action": "Check error details and retry"
        }


def parse_extracted_article(slug: str) -> dict[str, Any]:
    """
    Run mechanical parser on Datalab JSON, create _parsed.json.
    """
    json_path = CACHE_DIR / f"{slug}.json"

    if not json_path.exists():
        return {
            "success": False,
            "error": "NOT_EXTRACTED",
            "details": f"No Datalab JSON found for slug '{slug}'",
            "action": "Call extract_pdf() first, or check the slug is correct."
        }

    try:
        # Run the parser
        result = parse_blocks(json_path)

        # Save parsed result
        parsed_path = CACHE_DIR / f"{slug}_parsed.json"

        # Store body_html separately (can be large)
        body_html = result.get('body_html', '')
        output = {**result}
        output['body_html_chars'] = len(body_html)

        # Save full result including body_html for later use
        with open(parsed_path, 'w', encoding='utf-8') as f:
            # Save everything including body_html
            json.dump(result, f, ensure_ascii=False, indent=2)

        # Build summary for response
        summary = {
            "title": result.get('title'),
            "authors": result.get('authors'),
            "year": result.get('year'),
            "doi": result.get('doi'),
            "abstract_chars": len(result.get('abstract') or ''),
            "body_chars": len(body_html),
            "references_count": len(result.get('references', [])),
            "figures_count": len(result.get('figures', [])),
            "tables_count": len(result.get('tables', []))
        }

        return {
            "success": True,
            "progress": step_progress("parse", slug),
            "slug": slug,
            "parsed_path": str(parsed_path),
            "summary": summary,
            "warnings": result.get('warnings', []),
            "next_step": f"Call get_article_for_review('{slug}') to review and classify."
        }

    except Exception as e:
        logger.exception(f"Parsing failed for {slug}")
        return {
            "success": False,
            "error": "PARSE_ERROR",
            "details": str(e),
            "action": "Check the JSON file and error details"
        }


def get_article_for_review(slug: str) -> dict[str, Any]:
    """
    Get parsed article data + raw blocks for Claude to review and classify.

    Returns:
    - parser_extracted: What the mechanical parser found (title, authors, abstract, year, doi, citation)
    - raw_blocks: First 2 pages of content for Claude to verify extractions and derive classifications

    Claude must:
    1. Verify parser extractions against raw_blocks (confirm or correct title/authors/abstract)
    2. Derive year from raw_blocks if not extracted
    3. Derive method from reading the content (empirical/synthesis/theoretical/lived_experience)
    4. Derive voice from reading the content (academic/practitioner/organization/individual)
    5. Derive peer_reviewed from evidence in raw_blocks (DOI, journal name, etc.)

    NO SUGGESTIONS PROVIDED. Claude must derive all classifications from the content.
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"
    json_path = CACHE_DIR / f"{slug}.json"

    if not parsed_path.exists():
        if json_path.exists():
            return {
                "success": False,
                "error": "NOT_PARSED",
                "details": f"Extracted but not parsed: {slug}",
                "action": f"Call parse_extracted_article('{slug}') first."
            }
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No article found for slug '{slug}'",
            "action": "Check the slug or use list_intake_pdfs() to see available files."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    # Load raw blocks
    with open(json_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    # Build raw blocks from pages 0-1 (where metadata lives)
    raw_blocks = []
    blocks = raw_data.get('blocks', [])
    for block in blocks:
        page = block.get('page', 0)
        if page > 1:
            break

        block_type = block.get('block_type', '')

        # Skip images/figures
        if block_type in ['Figure', 'Picture']:
            continue

        html = block.get('html', '')
        text = extract_text(html) if html else ''

        if text and len(text.strip()) > 5:
            raw_blocks.append({
                'type': block_type,
                'text': text[:500],  # Truncate long blocks
                'page': page
            })

    # What the parser extracted (claims to verify)
    body_html = parsed.get('body_html', '')
    parser_extracted = {
        'title': parsed.get('title'),
        'authors': parsed.get('authors'),
        'year': parsed.get('year'),
        'citation': parsed.get('citation'),
        'doi': parsed.get('doi'),
        'abstract': parsed.get('abstract'),
        'keywords': parsed.get('keywords'),
        'article_type': parsed.get('article_type'),  # e.g., "ORIGINAL RESEARCH" if present
    }

    # What needs to be filled (parser couldn't find)
    missing = []
    if not parser_extracted['title']:
        missing.append('title')
    if not parser_extracted['authors']:
        missing.append('authors')
    if not parser_extracted['abstract']:
        missing.append('abstract')
    if not parser_extracted['year']:
        missing.append('year')

    # Content stats
    stats = {
        'body_chars': len(body_html),
        'references_count': len(parsed.get('references', [])),
        'figures_count': len(parsed.get('figures', [])),
        'tables_count': len(parsed.get('tables', []))
    }

    # Classification definitions (so Claude knows what each means)
    taxonomy = get_taxonomy()
    classification_guide = {
        'method': {
            m: taxonomy.get_method_definition(m) for m in taxonomy.methods
        },
        'voice': {
            v: taxonomy.get_voice_definition(v) for v in taxonomy.voices
        },
        'peer_reviewed': "True if published in a peer-reviewed journal (look for: DOI, journal name in header/footer, volume/issue numbers)"
    }

    return {
        "success": True,
        "progress": step_progress("classify", slug),
        "slug": slug,
        "parser_extracted": parser_extracted,
        "missing": missing,
        "warnings": parsed.get('warnings', []),
        "stats": stats,
        "raw_blocks": raw_blocks,
        "classification_guide": classification_guide,
        "next_step": "1) Verify parser_extracted against raw_blocks. 2) Derive method, voice, peer_reviewed by reading the content. 3) Call complete_article_review() with all values."
    }


def complete_article_review(
    slug: str,
    # Metadata — Claude STATES what it found (ALL REQUIRED, no defaults)
    title: str,
    authors: str,
    year: str,
    # Abstract — too long to retype, confirm or correct
    abstract_confirmed: bool,
    # Classification — derive from content (ALL REQUIRED, no defaults)
    method: str,
    voice: str,
    peer_reviewed: bool,
    # Corrections and optional
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
        authors: REQUIRED — state the authors you found (e.g., "E. O'Nions, J. Gould, P. Christie")
        year: REQUIRED — state the publication year (4 digits, e.g., "2018")
        abstract_confirmed: True if parser's abstract is correct, False if you're correcting it
        method: REQUIRED — one of: empirical, synthesis, theoretical, lived_experience
        voice: REQUIRED — one of: academic, practitioner, organization, individual
        peer_reviewed: REQUIRED — True if peer-reviewed, False otherwise
        corrected_abstract: Only required if abstract_confirmed=False
        citation: Optional full citation string
        notes: Optional notes about issues or flags

    Returns on success:
        Moves _parsed.json to ready/ directory for human review in /admin/review

    Returns on error:
        Specific validation errors — fix and retry
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'",
            "action": f"Call parse_extracted_article('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Validate all required fields
    taxonomy = get_taxonomy()
    errors = []

    # Title must be non-empty
    if not title or not title.strip():
        errors.append("title is required — state the title you found in raw_blocks")

    # Authors must be non-empty
    if not authors or not authors.strip():
        errors.append("authors is required — state the authors you found in raw_blocks")

    # Year must be 4 digits
    if not re.match(r'^\d{4}$', year):
        errors.append(f"Invalid year '{year}'. Must be 4 digits (e.g., '2018')")

    # Method must be valid
    if method not in taxonomy.methods:
        errors.append(f"Invalid method '{method}'. Valid: {', '.join(taxonomy.methods)}")

    # Voice must be valid
    if voice not in taxonomy.voices:
        errors.append(f"Invalid voice '{voice}'. Valid: {', '.join(taxonomy.voices)}")

    # Abstract: if not confirmed, must provide correction
    if not abstract_confirmed and not corrected_abstract:
        errors.append("abstract_confirmed=False but no corrected_abstract provided")

    # Abstract: if confirmed, parser must have found one
    if abstract_confirmed and not data.get('abstract'):
        errors.append("abstract_confirmed=True but parser found no abstract — set abstract_confirmed=False and provide corrected_abstract")

    if errors:
        return {
            "success": False,
            "error": "VALIDATION_ERROR",
            "errors": errors,
            "action": "Fix the errors and call complete_article_review() again."
        }

    # Apply all values
    changes = []

    # Metadata — Claude stated these explicitly
    data['title'] = title.strip()
    changes.append(f"title: {title[:50]}..." if len(title) > 50 else f"title: {title}")

    data['authors'] = authors.strip()
    changes.append(f"authors: {authors}")

    data['year'] = year
    changes.append(f"year: {year}")

    # Abstract — confirm or correct
    if not abstract_confirmed:
        data['abstract'] = corrected_abstract
        changes.append(f"abstract: corrected ({len(corrected_abstract)} chars)")
    else:
        changes.append("abstract: confirmed")

    # Classification — Claude derived these from content
    data['method'] = method
    changes.append(f"method: {method}")

    data['voice'] = voice
    changes.append(f"voice: {voice}")

    data['peer_reviewed'] = peer_reviewed
    changes.append(f"peer_reviewed: {peer_reviewed}")

    # Optional metadata
    if citation is not None:
        data['citation'] = citation
        changes.append(f"citation: {citation[:50]}...")

    if notes is not None:
        data['review_notes'] = notes
        changes.append(f"notes: {notes[:50]}...")

    # Save updated JSON
    with open(parsed_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Build final state summary
    final_state = {
        'title': data.get('title'),
        'authors': data.get('authors'),
        'year': data.get('year'),
        'abstract_chars': len(data.get('abstract') or ''),
        'method': data.get('method'),
        'voice': data.get('voice'),
        'peer_reviewed': data.get('peer_reviewed'),
        'citation': data.get('citation'),
    }

    return {
        "success": True,
        "progress": step_progress("classify", slug),
        "slug": slug,
        "changes_applied": changes,
        "final_state": final_state,
        "next_step": f"Call get_body_for_review('{slug}') to review body structure."
    }


def get_body_for_review(slug: str, chunk: int = 0) -> dict[str, Any]:
    """
    Get body_html in chunks for structural review.

    Claude must review the body for:
    - Orphan paragraphs (start with lowercase — likely split from previous)
    - Incomplete sentences (don't end with .!?)
    - Weird mid-sentence breaks
    - Missing section headers
    - Table/figure remnants that shouldn't be in body

    Call with chunk=0, then chunk=1, etc. until all chunks reviewed.

    Args:
        slug: The article slug
        chunk: Which chunk to return (0-indexed)

    Returns:
        - paragraphs: List of paragraphs with index and potential issues flagged
        - chunk_info: Current chunk number, total chunks
        - next_step: What to do next
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'",
            "action": f"Call parse_extracted_article('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Check that article review is complete
    if not data.get('method') or not data.get('voice') or data.get('peer_reviewed') is None:
        return {
            "success": False,
            "error": "ARTICLE_REVIEW_INCOMPLETE",
            "details": "Must complete article review first (method, voice, peer_reviewed)",
            "action": f"Call complete_article_review('{slug}', ...) first."
        }

    body_html = data.get('body_html', '')
    if not body_html:
        return {
            "success": False,
            "error": "NO_BODY",
            "details": "No body_html found in parsed article",
            "action": "Check the parser output — body may not have been extracted."
        }

    # Use shared helper to detect issues
    paragraphs = detect_body_issues(body_html)

    # Chunk the paragraphs (10 per chunk)
    CHUNK_SIZE = 10
    total_chunks = (len(paragraphs) + CHUNK_SIZE - 1) // CHUNK_SIZE

    if chunk >= total_chunks:
        return {
            "success": False,
            "error": "INVALID_CHUNK",
            "details": f"Chunk {chunk} doesn't exist. Total chunks: {total_chunks}",
            "action": f"Use chunk 0 to {total_chunks - 1}"
        }

    start = chunk * CHUNK_SIZE
    end = min(start + CHUNK_SIZE, len(paragraphs))
    chunk_paragraphs = paragraphs[start:end]

    # Count issues in this chunk
    issues_in_chunk = sum(1 for p in chunk_paragraphs if p['issues'])

    return {
        "success": True,
        "progress": step_progress("body_review", slug),
        "slug": slug,
        "chunk_info": {
            "current": chunk,
            "total": total_chunks,
            "paragraphs_in_chunk": len(chunk_paragraphs),
            "total_paragraphs": len(paragraphs),
            "issues_in_chunk": issues_in_chunk
        },
        "paragraphs": chunk_paragraphs,
        "next_step": f"Review paragraphs for issues. Then call get_body_for_review('{slug}', chunk={chunk + 1}) for next chunk, or complete_body_review('{slug}', ...) when done." if chunk < total_chunks - 1 else f"This is the last chunk. Call complete_body_review('{slug}', ...) to confirm body is clean or provide fixes."
    }


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

    You cannot set body_approved=True while ignoring flagged issues.

    Args:
        slug: The article slug
        body_approved: True if body structure is clean (after fixes), False if issues remain
        fixes: List of fixes to apply. Each fix is:
            {"index": 5, "action": "join_previous"} — join paragraph 5 to paragraph 4
            {"index": 7, "action": "join_next"} — join paragraph 7 to paragraph 8
            {"index": 3, "action": "delete"} — remove paragraph 3 (cruft)
            {"index": 2, "action": "replace", "text": "Fixed text here"} — replace content
        issues_acknowledged: List of paragraph indices where flagged issues are false positives
            (e.g., [3, 7] means "issues at indices 3 and 7 are not real problems")
        notes: Explain why acknowledged issues are false positives

    Returns on success:
        Updates _parsed.json with body_reviewed=True and returns status
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'",
            "action": f"Call parse_extracted_article('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    body_html = data.get('body_html', '')

    # Re-scan body to find all flagged issues
    paragraphs = detect_body_issues(body_html)
    flagged_indices = {p['index'] for p in paragraphs if p['issues']}

    # Check that every flagged issue is addressed
    fixes_list = fixes or []
    ack_list = issues_acknowledged or []

    fixed_indices = {f['index'] for f in fixes_list}
    acknowledged_indices = set(ack_list)

    addressed_indices = fixed_indices | acknowledged_indices
    unaddressed = flagged_indices - addressed_indices

    if unaddressed:
        # Build helpful error message showing what's unaddressed
        unaddressed_details = []
        for p in paragraphs:
            if p['index'] in unaddressed:
                unaddressed_details.append({
                    'index': p['index'],
                    'issues': p['issues'],
                    'text_preview': p['text'][:100] + '...' if len(p['text']) > 100 else p['text']
                })

        return {
            "success": False,
            "error": "UNADDRESSED_ISSUES",
            "details": f"Found {len(unaddressed)} flagged issues that were not fixed or acknowledged",
            "unaddressed": unaddressed_details,
            "action": "Either fix these issues (add to fixes list) or acknowledge as false positives (add indices to issues_acknowledged)"
        }

    # Validate basic logic
    if not body_approved and not fixes_list:
        return {
            "success": False,
            "error": "VALIDATION_ERROR",
            "errors": ["body_approved=False but no fixes provided"],
            "action": "Either set body_approved=True or provide fixes list."
        }

    # Apply fixes if provided
    if fixes:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body_html, 'html.parser')

        # Get all paragraph elements
        elements = [e for e in soup.children if e.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'ul', 'ol']]

        # Sort fixes by index descending (so deletions don't shift indices)
        sorted_fixes = sorted(fixes, key=lambda f: f['index'], reverse=True)

        changes_made = []
        for fix in sorted_fixes:
            idx = fix['index']
            action = fix['action']

            if idx >= len(elements):
                changes_made.append(f"SKIPPED: index {idx} out of range")
                continue

            if action == 'delete':
                elements[idx].decompose()
                changes_made.append(f"Deleted paragraph {idx}")

            elif action == 'join_previous' and idx > 0:
                # Append text of current to previous
                current_text = elements[idx].get_text(strip=True)
                prev_text = elements[idx - 1].get_text(strip=True)
                elements[idx - 1].string = prev_text + ' ' + current_text
                elements[idx].decompose()
                changes_made.append(f"Joined paragraph {idx} to {idx - 1}")

            elif action == 'join_next' and idx < len(elements) - 1:
                # Append text of next to current
                current_text = elements[idx].get_text(strip=True)
                next_text = elements[idx + 1].get_text(strip=True)
                elements[idx].string = current_text + ' ' + next_text
                elements[idx + 1].decompose()
                changes_made.append(f"Joined paragraph {idx + 1} to {idx}")

            elif action == 'replace':
                new_text = fix.get('text', '')
                elements[idx].string = new_text
                changes_made.append(f"Replaced paragraph {idx}")

        # Rebuild body_html
        data['body_html'] = str(soup)
        data['body_fixes_applied'] = changes_made
    else:
        changes_made = []

    # Mark body as reviewed
    data['body_reviewed'] = True

    if notes:
        data['body_review_notes'] = notes

    # Ensure directories exist
    READY_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVED_DIR.mkdir(parents=True, exist_ok=True)

    # Move _parsed.json to ready directory (signals preprocessing complete)
    ready_path = READY_DIR / f"{slug}_parsed.json"
    with open(ready_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Remove from work-in-progress location
    parsed_path.unlink()

    # Move raw Datalab JSON to archived folder (cleanup)
    raw_json_path = CACHE_DIR / f"{slug}.json"
    archived_json_path = ARCHIVED_DIR / f"{slug}.json"
    json_archived = False
    if raw_json_path.exists():
        import shutil
        shutil.move(str(raw_json_path), str(archived_json_path))
        json_archived = True
        logger.info(f"Moved raw JSON to archived: {slug}.json")

    # Update session - this article is now ready for human review
    session = get_session()
    session["current_slug"] = None  # No longer working on this one
    # Don't increment completed_count yet - human still needs to approve
    save_session(session)

    return {
        "success": True,
        "progress": step_progress("ready", slug),
        "slug": slug,
        "body_approved": body_approved,
        "fixes_applied": changes_made,
        "ready_for_review": str(ready_path),
        "raw_json_archived": json_archived,
        "next_step": "Article moved to ready/ for human review. Human approves at /admin/review. Then call start_preprocessing() to continue with next article."
    }


def get_preprocessing_status() -> dict[str, Any]:
    """
    Get overview of preprocessing pipeline status.
    """
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    READY_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVED_DIR.mkdir(parents=True, exist_ok=True)

    # Intake PDFs
    intake_pdfs = list(INTAKE_DIR.glob("*.pdf"))

    # Processed PDFs (already extracted)
    processed_pdfs = list(PROCESSED_DIR.glob("*.pdf"))

    # Work in progress (have _parsed.json in main cache dir)
    wip_jsons = list(CACHE_DIR.glob("*_parsed.json"))

    # Ready for human review
    ready_jsons = list(READY_DIR.glob("*_parsed.json"))

    # Archived (approved and in database)
    archived_jsons = list(ARCHIVED_DIR.glob("*_parsed.json"))

    # Extracted but not yet parsed
    extracted_jsons = {f.stem for f in CACHE_DIR.glob("*.json") if not f.stem.endswith('_parsed')}
    parsed_slugs = {f.stem.replace('_parsed', '') for f in wip_jsons}
    not_parsed = extracted_jsons - parsed_slugs

    # Database status
    from .database import get_database
    db = get_database()
    progress = db.get_progress()

    return {
        "intake": {
            "pending_pdfs": len(intake_pdfs),
            "processed_pdfs": len(processed_pdfs),
            "files": [p.name for p in intake_pdfs[:10]]  # First 10
        },
        "preprocessing": {
            "extracted_not_parsed": len(not_parsed),
            "work_in_progress": len(wip_jsons),
            "ready_for_review": len(ready_jsons),
            "archived": len(archived_jsons),
            "ready_files": [f.stem.replace('_parsed', '') for f in ready_jsons[:10]]
        },
        "database": {
            "pending": progress.get('pending', 0),
            "in_progress": progress.get('in_progress', 0),
            "translated": progress.get('translated', 0),
            "skipped": progress.get('skipped', 0)
        },
        "next_step": "Call list_intake_pdfs() to see available PDFs, or check /admin/review for ready articles."
    }


# --- Helper functions ---

def detect_body_issues(body_html: str) -> list[dict]:
    """
    Scan body_html and return paragraphs with detected issues.

    Used by:
    - get_body_for_review() to show issues to Claude
    - complete_body_review() to verify Claude addressed all issues

    Returns list of dicts with: index, tag, text, full_length, issues
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body_html, 'html.parser')

    paragraphs = []
    for i, element in enumerate(soup.children):
        if element.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'ul', 'ol']:
            text = element.get_text(strip=True)
            if not text:
                continue

            # Flag potential issues — BE SPECIFIC
            issues = []

            # 1. ORPHAN: starts with lowercase — likely split from previous paragraph
            if text and text[0].islower() and len(text) > 20:
                issues.append("ORPHAN: Starts lowercase — likely split from previous. Action: join_previous")

            # 2. INCOMPLETE: doesn't end with sentence punctuation
            if text and text[-1] not in '.!?:;)' and len(text) > 50 and element.name == 'p':
                issues.append("INCOMPLETE: No ending punctuation — check if continues in next. Action: join_next?")

            # 3. CAPTION: looks like a figure/table caption
            if re.match(r'^(Figure|Fig\.?|Table|Tableau)\s*\d', text, re.IGNORECASE):
                issues.append("CAPTION: Looks like figure/table caption — should not be in body. Action: delete")

            # 4. PAGE_ARTIFACT: page numbers, headers, footers
            if re.match(r'^\d+$', text) or len(text) < 10 and re.match(r'^Page\s*\d+', text, re.IGNORECASE):
                issues.append("PAGE_ARTIFACT: Looks like page number/header. Action: delete")

            # 5. SHORT_FRAGMENT: very short text that may be cruft
            if element.name == 'p' and len(text) < 25 and not text.endswith(':'):
                issues.append("SHORT_FRAGMENT: Very short — cruft or orphan piece? Action: delete or join")

            # 6. REFERENCE_LEAK: reference that leaked into body
            if re.match(r'^\d+\.\s*[A-Z]', text) and 'et al' in text.lower():
                issues.append("REFERENCE_LEAK: Looks like a reference. Action: delete")

            paragraphs.append({
                'index': i,
                'tag': element.name,
                'text': text[:300] + '...' if len(text) > 300 else text,
                'full_length': len(text),
                'issues': issues
            })

    return paragraphs


# slugify is now imported from utils.py


# --- Step 4: AI Enhancement Tools ---
# These tools ENFORCE sequential checking. Claude must call each one in order.
# Each tool returns next_step telling Claude exactly what to call next.
# Cannot skip to step4_complete without passing through all checks.

# Session state keys for tracking which checks are done
# Each check has two phases: _checked (check function called) and complete (confirm called)
STEP4_CHECKS = ['fields', 'warnings', 'references', 'formulas']


def _get_step4_state(slug: str) -> dict[str, Any]:
    """Get Step 4 check state for an article.

    State tracks two phases per check:
    - {check}_checked: True after step4_check_{check}() is called
    - {check}: True after step4_confirm_{check}() completes (legacy key for completion)
    """
    state_file = CACHE_DIR / f"{slug}_step4_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except:
            pass
    # Initialize with both _checked and complete states
    state = {}
    for check in STEP4_CHECKS:
        state[f'{check}_checked'] = False
        state[check] = False
    return state


def _save_step4_state(slug: str, state: dict[str, Any]) -> None:
    """Save Step 4 check state."""
    state_file = CACHE_DIR / f"{slug}_step4_state.json"
    state_file.write_text(json.dumps(state, indent=2))


def _clear_step4_state(slug: str) -> None:
    """Clear Step 4 state when complete."""
    state_file = CACHE_DIR / f"{slug}_step4_state.json"
    if state_file.exists():
        state_file.unlink()


def step4_check_fields(slug: str) -> dict[str, Any]:
    """
    Step 4.1: Check for missing or empty metadata fields.

    MUST be called first in Step 4 sequence. Returns fields that need attention.

    Claude must review and either:
    - Confirm field is correctly empty (e.g., no authors found in document)
    - Provide the missing value extracted from raw HTML

    Args:
        slug: The article slug

    Returns:
        - fields_status: Dict of field -> status (ok, missing, empty)
        - raw_html_hint: First 2 pages of raw HTML for extraction
        - next_step: Call step4_confirm_fields() with your findings
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"
    json_path = CACHE_DIR / f"{slug}.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'",
            "action": f"Call parse_extracted_article('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    # Check required fields
    fields_to_check = ['title', 'authors', 'citation', 'abstract', 'year']
    fields_status = {}

    for field in fields_to_check:
        value = parsed.get(field)
        if value is None:
            fields_status[field] = {'status': 'missing', 'value': None}
        elif isinstance(value, str) and not value.strip():
            fields_status[field] = {'status': 'empty', 'value': ''}
        else:
            fields_status[field] = {'status': 'ok', 'value': value[:100] + '...' if isinstance(value, str) and len(value) > 100 else value}

    # Count issues
    issues = [f for f, s in fields_status.items() if s['status'] != 'ok']

    # Mark that this check was called (required before confirm can be called)
    state = _get_step4_state(slug)
    state['fields_checked'] = True
    state['fields_issues'] = issues  # Store issues so confirm can validate
    _save_step4_state(slug, state)

    # Load raw blocks for reference (first 2 pages)
    raw_hint = []
    if json_path.exists():
        with open(json_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        blocks = raw_data.get('blocks', [])
        for block in blocks[:20]:  # First 20 blocks usually cover pages 0-1
            if block.get('page', 0) > 1:
                break
            html = block.get('html', '')
            text = extract_text(html) if html else ''
            if text and len(text.strip()) > 10:
                raw_hint.append({
                    'type': block.get('block_type', ''),
                    'text': text[:200]
                })

    return {
        "success": True,
        "slug": slug,
        "check": "fields",
        "fields_status": fields_status,
        "issues_found": len(issues),
        "issue_fields": issues,
        "raw_blocks_hint": raw_hint[:10],  # First 10 relevant blocks
        "next_step": f"Review fields_status. For each missing/empty field, check raw_blocks_hint to find the value. Then call step4_confirm_fields('{slug}', ...) with your findings."
    }


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
    Confirm or provide missing field values.

    If step4_check_fields() showed issues, provide the corrected values.
    If all fields were ok, set all_fields_ok=True.

    Args:
        slug: The article slug
        title: Corrected title if it was missing/empty
        authors: Corrected authors if missing/empty
        year: Corrected year if missing/empty
        citation: Corrected citation if missing/empty
        abstract: Corrected abstract if missing/empty
        all_fields_ok: Set True if step4_check_fields showed no issues

    Returns:
        - Updates _parsed.json with corrections
        - Marks 'fields' check as complete
        - next_step: Call step4_check_warnings()
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check prerequisite: step4_check_fields must have been called
    state = _get_step4_state(slug)
    if not state.get('fields_checked'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must call step4_check_fields() first to see the field status",
            "action": f"Call step4_check_fields('{slug}') first."
        }

    # Validate: if there were issues, caller must provide fixes (not just all_fields_ok)
    stored_issues = state.get('fields_issues', [])
    if stored_issues and all_fields_ok:
        # Check if any corrections were actually provided
        corrections_provided = any([title, authors, year, citation, abstract])
        if not corrections_provided:
            return {
                "success": False,
                "error": "ISSUES_NOT_ADDRESSED",
                "details": f"step4_check_fields found issues with: {stored_issues}. Cannot use all_fields_ok=True without providing corrections.",
                "issue_fields": stored_issues,
                "action": "Provide the missing field values, or if they truly can't be found, set them to empty string explicitly."
            }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    changes = []

    # Apply corrections
    if title is not None:
        parsed['title'] = title
        changes.append(f"title: {title[:50]}...")

    if authors is not None:
        parsed['authors'] = authors
        changes.append(f"authors: {authors}")

    if year is not None:
        parsed['year'] = year
        changes.append(f"year: {year}")

    if citation is not None:
        parsed['citation'] = citation
        changes.append(f"citation: {citation[:50]}...")

    if abstract is not None:
        parsed['abstract'] = abstract
        changes.append(f"abstract: {len(abstract)} chars")

    # Save updates
    with open(parsed_path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    # Mark check complete (state already loaded above)
    state['fields'] = True
    _save_step4_state(slug, state)

    return {
        "success": True,
        "slug": slug,
        "check": "fields",
        "status": "complete",
        "changes_applied": changes if changes else ["No changes needed"],
        "next_step": f"Call step4_check_warnings('{slug}') to check for parser warnings."
    }


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
        - warnings: List of warnings with context
        - next_step: Call step4_confirm_warnings() with fixes
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check prerequisite
    state = _get_step4_state(slug)
    if not state.get('fields'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must complete fields check first",
            "action": f"Call step4_check_fields('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    warnings = parsed.get('warnings', [])

    # Also scan body for orphan-like patterns
    body_html = parsed.get('body_html', '')
    body_issues = detect_body_issues(body_html)
    orphan_issues = [p for p in body_issues if any('ORPHAN' in issue for issue in p.get('issues', []))]

    # Mark that this check was called (required before confirm can be called)
    state['warnings_checked'] = True
    state['warnings_has_issues'] = bool(warnings or orphan_issues)
    _save_step4_state(slug, state)

    return {
        "success": True,
        "slug": slug,
        "check": "warnings",
        "parser_warnings": warnings,
        "orphan_paragraphs": orphan_issues[:10],  # First 10
        "total_orphans": len(orphan_issues),
        "next_step": f"Review warnings. For each [ORPHAN?], decide if it should be joined to previous paragraph. Then call step4_confirm_warnings('{slug}', ...) with your decisions."
    }


def step4_confirm_warnings(
    slug: str,
    orphan_fixes: list[dict] | None = None,
    warnings_acknowledged: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Confirm warnings have been reviewed and apply any fixes.

    Args:
        slug: The article slug
        orphan_fixes: List of fixes for orphan paragraphs:
            [{"index": 5, "action": "join_previous"}, ...]
        warnings_acknowledged: True if warnings reviewed and no action needed
        notes: Optional notes about decisions

    Returns:
        - Applies fixes to body_html
        - Marks 'warnings' check as complete
        - next_step: Call step4_check_references()
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check prerequisite: fields must be complete AND warnings check must have been called
    state = _get_step4_state(slug)
    if not state.get('fields'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must complete fields check first",
            "action": f"Call step4_check_fields('{slug}') first."
        }

    if not state.get('warnings_checked'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must call step4_check_warnings() first to see the warnings",
            "action": f"Call step4_check_warnings('{slug}') first."
        }

    # Load and apply fixes
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    changes = []

    if orphan_fixes:
        from bs4 import BeautifulSoup
        body_html = parsed.get('body_html', '')
        soup = BeautifulSoup(body_html, 'html.parser')

        elements = list(soup.children)
        elements = [e for e in elements if hasattr(e, 'name') and e.name]

        # Sort by index descending to avoid index shifting
        sorted_fixes = sorted(orphan_fixes, key=lambda f: f.get('index', 0), reverse=True)

        for fix in sorted_fixes:
            idx = fix.get('index', -1)
            action = fix.get('action', '')

            if idx < 0 or idx >= len(elements):
                continue

            if action == 'join_previous' and idx > 0:
                current_el = elements[idx]
                prev_el = elements[idx - 1]

                # Preserve HTML formatting: append children instead of replacing with text
                # Add a space text node, then move all children from current to previous
                from bs4 import NavigableString
                prev_el.append(NavigableString(' '))
                for child in list(current_el.children):
                    prev_el.append(child.extract())
                current_el.decompose()
                changes.append(f"Joined paragraph {idx} to {idx - 1}")

        parsed['body_html'] = str(soup)

    if notes:
        parsed['warning_review_notes'] = notes
        changes.append(f"Notes: {notes[:50]}...")

    # Save updates
    with open(parsed_path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    # Mark check complete
    state['warnings'] = True
    _save_step4_state(slug, state)

    return {
        "success": True,
        "slug": slug,
        "check": "warnings",
        "status": "complete",
        "changes_applied": changes if changes else ["No changes needed"],
        "next_step": f"Call step4_check_references('{slug}') to check references extraction."
    }


def step4_check_references(slug: str) -> dict[str, Any]:
    """
    Step 4.3: Check if references were properly extracted.

    MUST call step4_confirm_warnings() first.

    Returns:
    - references: Currently extracted references
    - raw_reference_section: Raw HTML of reference section (if found)
    - issues: Any problems detected

    Args:
        slug: The article slug
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"
    json_path = CACHE_DIR / f"{slug}.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check prerequisites
    state = _get_step4_state(slug)
    if not state.get('warnings'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must complete warnings check first",
            "action": f"Call step4_check_warnings('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    references = parsed.get('references', [])

    # Try to find reference section in raw HTML if references are empty
    raw_reference_hint = None
    if not references and json_path.exists():
        with open(json_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)

        blocks = raw_data.get('blocks', [])
        in_refs = False
        ref_blocks = []

        for block in blocks:
            html = block.get('html', '')
            text = extract_text(html) if html else ''

            # Check if this is a reference header
            if re.search(r'^(References|Références|Bibliography|Bibliographie)', text, re.IGNORECASE):
                in_refs = True
                continue

            if in_refs:
                # Stop at next major section
                if re.match(r'^(Appendix|Annexe|Acknowledgements)', text, re.IGNORECASE):
                    break
                if text and len(text) > 20:
                    ref_blocks.append(text[:200])
                if len(ref_blocks) >= 10:
                    break

        if ref_blocks:
            raw_reference_hint = ref_blocks

    issues = []
    if not references:
        issues.append("No references extracted — check raw_reference_hint for missed references")

    # Mark that this check was called (required before confirm can be called)
    state['references_checked'] = True
    state['references_has_issues'] = bool(issues)
    _save_step4_state(slug, state)

    return {
        "success": True,
        "slug": slug,
        "check": "references",
        "references_count": len(references),
        "references_preview": references[:5] if references else [],
        "raw_reference_hint": raw_reference_hint,
        "issues": issues,
        "next_step": f"Review references. If references are missing, extract them from raw_reference_hint. Then call step4_confirm_references('{slug}', ...) with your findings."
    }


def step4_confirm_references(
    slug: str,
    references_ok: bool = False,
    additional_references: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Confirm references check is complete.

    Args:
        slug: The article slug
        references_ok: True if references are complete (or correctly empty)
        additional_references: List of references to add (extracted from raw HTML)
        notes: Optional notes
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check prerequisites: warnings must be complete AND references check must have been called
    state = _get_step4_state(slug)
    if not state.get('warnings'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must complete warnings check first",
            "action": f"Call step4_check_warnings('{slug}') first."
        }

    if not state.get('references_checked'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must call step4_check_references() first to see the references status",
            "action": f"Call step4_check_references('{slug}') first."
        }

    # Load and update
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    changes = []

    if additional_references:
        existing = parsed.get('references', [])
        parsed['references'] = existing + additional_references
        changes.append(f"Added {len(additional_references)} references")

    if notes:
        parsed['references_review_notes'] = notes

    # Save
    with open(parsed_path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    # Mark complete
    state['references'] = True
    _save_step4_state(slug, state)

    return {
        "success": True,
        "slug": slug,
        "check": "references",
        "status": "complete",
        "changes_applied": changes if changes else ["No changes needed"],
        "next_step": f"Call step4_check_formulas('{slug}') to check formula normalization."
    }


def step4_check_formulas(slug: str) -> dict[str, Any]:
    """
    Step 4.4: Check for unwrapped statistical formulas that need normalization.

    MUST call step4_confirm_references() first.

    Scans body_html for statistical patterns that aren't wrapped in <span class="formula">:
    - F-statistics: F(1, 156) = 4.07
    - t-tests: t(45) = 2.31
    - Chi-square: χ²(2) = 8.45
    - p-values: p < .05, p = .001
    - Effect sizes: η² = .12, d = 0.45
    - Correlations: r = .67
    - Means/SDs: M = 4.2, SD = 1.1

    Returns paragraphs containing unwrapped formulas for review.
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check prerequisites
    state = _get_step4_state(slug)
    if not state.get('references'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must complete references check first",
            "action": f"Call step4_check_references('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    body_html = parsed.get('body_html', '')

    # Patterns for statistical notation (not inside formula spans)
    stat_patterns = [
        r'F\s*\(\d+\s*,\s*\d+\)\s*=\s*[\d.]+',  # F(1, 156) = 4.07
        r't\s*\(\d+\)\s*=\s*-?[\d.]+',  # t(45) = 2.31
        r'χ²?\s*\(\d+\)\s*=\s*[\d.]+',  # χ²(2) = 8.45
        r'p\s*[<>=]\s*\.?\d+',  # p < .05
        r'η²?\s*=\s*\.?\d+',  # η² = .12
        r'd\s*=\s*-?[\d.]+',  # d = 0.45
        r'r\s*=\s*-?\.?\d+',  # r = .67
        r'M\s*=\s*[\d.]+',  # M = 4.2
        r'SD\s*=\s*[\d.]+',  # SD = 1.1
    ]

    combined_pattern = '|'.join(f'({p})' for p in stat_patterns)

    # Parse HTML and find unwrapped formulas
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body_html, 'html.parser')

    paragraphs_with_formulas = []

    for i, element in enumerate(soup.children):
        if not hasattr(element, 'name') or element.name not in ['p', 'td', 'li']:
            continue

        # Get text NOT inside formula spans
        text = element.get_text()

        # Check if there are formula spans already
        existing_formulas = element.find_all('span', class_='formula')

        # Find matches in text
        matches = re.findall(combined_pattern, text)

        if matches:
            # Flatten match groups
            flat_matches = []
            for m in matches:
                if isinstance(m, tuple):
                    flat_matches.extend([x for x in m if x])
                else:
                    flat_matches.append(m)

            # Check which are not already wrapped
            unwrapped = []
            for match in flat_matches:
                # See if this match is inside an existing formula span
                is_wrapped = False
                for span in existing_formulas:
                    if match in span.get_text():
                        is_wrapped = True
                        break
                if not is_wrapped:
                    unwrapped.append(match)

            if unwrapped:
                paragraphs_with_formulas.append({
                    'index': i,
                    'text_preview': text[:200] + '...' if len(text) > 200 else text,
                    'unwrapped_formulas': unwrapped[:5],  # First 5
                    'total_unwrapped': len(unwrapped)
                })

    total_unwrapped = sum(p['total_unwrapped'] for p in paragraphs_with_formulas)

    # Mark that this check was called (required before confirm can be called)
    state['formulas_checked'] = True
    state['formulas_found'] = total_unwrapped
    _save_step4_state(slug, state)

    return {
        "success": True,
        "slug": slug,
        "check": "formulas",
        "paragraphs_with_unwrapped_formulas": paragraphs_with_formulas[:20],
        "total_paragraphs_affected": len(paragraphs_with_formulas),
        "total_unwrapped_formulas": total_unwrapped,
        "note": "This is judgment-based work. Numbers like ages, sample sizes don't need wrapping. Wrap statistical test results.",
        "next_step": f"Review the unwrapped formulas. Decide which need <span class=\"formula\"> wrapping. Then call step4_confirm_formulas('{slug}', ...) with your decisions."
    }


def step4_confirm_formulas(
    slug: str,
    formulas_ok: bool = False,
    formula_wraps: list[dict] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Confirm formula normalization is complete.

    Args:
        slug: The article slug
        formulas_ok: True if no formula wrapping needed
        formula_wraps: List of formulas to wrap:
            [{"paragraph_index": 5, "formula_text": "F(1, 156) = 4.07"}, ...]
            Each will be wrapped in <span class="formula">
        notes: Optional notes about decisions
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check prerequisites: references must be complete AND formulas check must have been called
    state = _get_step4_state(slug)
    if not state.get('references'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must complete references check first",
            "action": f"Call step4_check_references('{slug}') first."
        }

    if not state.get('formulas_checked'):
        return {
            "success": False,
            "error": "PREREQUISITE_MISSING",
            "details": "Must call step4_check_formulas() first to see the formulas needing wrapping",
            "action": f"Call step4_check_formulas('{slug}') first."
        }

    # Load and update
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    changes = []

    if formula_wraps:
        body_html = parsed.get('body_html', '')

        # Apply wraps (simple text replacement)
        for wrap in formula_wraps:
            formula_text = wrap.get('formula_text', '')
            if formula_text and formula_text in body_html:
                wrapped = f'<span class="formula">{formula_text}</span>'
                body_html = body_html.replace(formula_text, wrapped, 1)
                changes.append(f"Wrapped: {formula_text[:30]}...")

        parsed['body_html'] = body_html

    if notes:
        parsed['formula_review_notes'] = notes

    # Save
    with open(parsed_path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    # Mark complete
    state['formulas'] = True
    _save_step4_state(slug, state)

    return {
        "success": True,
        "slug": slug,
        "check": "formulas",
        "status": "complete",
        "changes_applied": changes if changes else ["No wrapping needed"],
        "next_step": f"All Step 4 checks complete. Call step4_complete('{slug}') to finalize and move to human review."
    }


def step4_complete(slug: str) -> dict[str, Any]:
    """
    Finalize Step 4 and move article to ready/ for human review.

    Can ONLY be called after all four checks are complete:
    - fields
    - warnings
    - references
    - formulas

    Args:
        slug: The article slug

    Returns:
        - Moves _parsed.json to ready/ directory
        - Clears step4 state
        - Ready for human review at /admin/review
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'"
        }

    # Check ALL prerequisites
    state = _get_step4_state(slug)
    missing_checks = [check for check in STEP4_CHECKS if not state.get(check)]

    if missing_checks:
        return {
            "success": False,
            "error": "INCOMPLETE_CHECKS",
            "details": f"Must complete all Step 4 checks first",
            "missing_checks": missing_checks,
            "action": f"Call step4_check_{missing_checks[0]}('{slug}') to continue."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    # Mark step 4 as complete
    parsed['step4_complete'] = True
    parsed['step4_completed_at'] = datetime.now().isoformat()

    # Ensure ready directory exists
    READY_DIR.mkdir(parents=True, exist_ok=True)

    # Move BOTH files to ready/ together (they travel as a pair)
    import shutil

    # 1. Move parsed JSON
    ready_parsed_path = READY_DIR / f"{slug}_parsed.json"
    with open(ready_parsed_path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    parsed_path.unlink()

    # 2. Move raw JSON
    json_path = CACHE_DIR / f"{slug}.json"
    ready_json_path = READY_DIR / f"{slug}.json"
    raw_moved = False
    if json_path.exists():
        shutil.move(str(json_path), str(ready_json_path))
        raw_moved = True

    # 3. Move images folder if exists
    images_src = CACHE_DIR / "images" / slug
    images_dst = READY_DIR / "images" / slug
    images_moved = False
    if images_src.exists():
        images_dst.parent.mkdir(parents=True, exist_ok=True)
        if images_dst.exists():
            shutil.rmtree(images_dst)
        shutil.move(str(images_src), str(images_dst))
        images_moved = True

    # Clear step4 state
    _clear_step4_state(slug)

    return {
        "success": True,
        "slug": slug,
        "status": "ready_for_human_review",
        "ready_path": str(ready_parsed_path),
        "files_moved_to_ready": {
            "parsed": True,
            "raw": raw_moved,
            "images": images_moved
        },
        "checks_completed": STEP4_CHECKS,
        "next_step": "Article moved to ready/ for human review at /admin/review. Human approves there."
    }


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
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'",
            "action": "Cannot reset Step 4 for an article that doesn't exist in cache."
        }

    # Get current state before clearing (for reporting)
    state = _get_step4_state(slug)
    completed_checks = [check for check in STEP4_CHECKS if state.get(check)]
    started_checks = [check for check in STEP4_CHECKS if state.get(f'{check}_checked')]

    # Clear the state
    _clear_step4_state(slug)

    return {
        "success": True,
        "slug": slug,
        "status": "reset",
        "cleared_completed_checks": completed_checks,
        "cleared_started_checks": started_checks,
        "next_step": f"Step 4 state cleared. Call step4_check_fields('{slug}') to begin Step 4 fresh."
    }


def generate_article_id(title: str, authors: str, year: str | None) -> str:
    """Generate article ID from title, first author, and year."""
    # Extract first author's last name
    first_author = ''
    if authors:
        # Handle various formats: "Smith, J." or "J. Smith" or "John Smith"
        first_part = authors.split(',')[0].split('&')[0].strip()
        # Get last word (likely last name)
        words = first_part.split()
        if words:
            # Skip initials (single letters with or without period)
            last_name_words = [w for w in words if len(w.replace('.', '')) > 1]
            if last_name_words:
                # Slugify the author name to handle apostrophes etc.
                first_author = slugify(last_name_words[-1])

    # Get first few words of title
    title_slug = slugify(title)[:50] if title else 'untitled'

    # Combine
    parts = []
    if first_author:
        parts.append(first_author)
    if year:
        parts.append(year)
    parts.append(title_slug)

    return '-'.join(parts)
