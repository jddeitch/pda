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
7. complete_body_review(slug, body_approved, fixes) — confirm clean or apply fixes
8. submit_for_review(slug) — create DB record for human review

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

logger = logging.getLogger(__name__)

# Directories
PROJECT_ROOT = Path(__file__).parent.parent
INTAKE_DIR = PROJECT_ROOT / "intake" / "articles"
PROCESSED_DIR = PROJECT_ROOT / "intake" / "processed"
CACHE_DIR = PROJECT_ROOT / "cache" / "articles"


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
        "counts": {
            "available": len(available),
            "extracted": len(already_extracted),
            "processed": len(processed_pdfs)
        },
        "next_step": "Call extract_pdf('<filename>') to process a PDF, or parse_extracted_article('<slug>') for already-extracted files."
    }


def extract_pdf(filename: str) -> dict[str, Any]:
    """
    Submit PDF to Datalab Marker API and wait for completion.

    This is a blocking operation that typically takes 30-120 seconds.
    """
    pdf_path = INTAKE_DIR / filename

    if not pdf_path.exists():
        return {
            "success": False,
            "error": "FILE_NOT_FOUND",
            "details": f"File not found: {pdf_path}",
            "action": "Check filename. Use list_intake_pdfs() to see available files."
        }

    slug = slugify(pdf_path.stem)
    output_path = CACHE_DIR / f"{slug}.json"

    # Check if already extracted
    if output_path.exists():
        return {
            "success": False,
            "error": "ALREADY_EXTRACTED",
            "details": f"Already extracted: {slug}",
            "slug": slug,
            "action": f"Call parse_extracted_article('{slug}') to parse it."
        }

    # Import extraction functions from batch_extract
    try:
        from batch_extract import submit_pdf, poll_and_save
    except ImportError:
        return {
            "success": False,
            "error": "IMPORT_ERROR",
            "details": "Could not import batch_extract.py functions",
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
        request_id = result.get('request_id')

        if not request_id:
            return {
                "success": False,
                "error": "SUBMISSION_FAILED",
                "details": f"API response: {result}",
                "action": "Check API key and try again"
            }

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
        processed_path = PROCESSED_DIR / filename
        try:
            import shutil
            shutil.move(str(pdf_path), str(processed_path))
            logger.info(f"Moved {filename} to intake/processed/")
            moved = True
        except Exception as move_err:
            logger.warning(f"Could not move {filename} to processed: {move_err}")
            moved = False

        return {
            "success": True,
            "slug": slug,
            "json_path": str(output_path),
            "stats": {
                "blocks": len(data.get('blocks', [])),
                "pages": data.get('page_count', 0),
                "images": data.get('images_count', 0)
            },
            "moved_to_processed": moved,
            "next_step": f"Call parse_extracted_article('{slug}') to structure the content."
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
            "slug": slug,
            "parsed_path": str(parsed_path),
            "summary": summary,
            "warnings": result.get('warnings', []),
            "next_step": f"Call get_article_for_review('{slug}') to review and enhance."
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
        Updates _parsed.json and returns final state ready for submit_for_review()

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
        "slug": slug,
        "changes_applied": changes,
        "final_state": final_state,
        "next_step": f"Call get_body_for_review('{slug}') to review body structure before submitting."
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

    # Save
    with open(parsed_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "slug": slug,
        "body_approved": body_approved,
        "fixes_applied": changes_made,
        "next_step": f"Call submit_for_review('{slug}') to add to database for human review."
    }


def submit_for_review(slug: str) -> dict[str, Any]:
    """
    Create article record in database with status='preprocessing'.

    Requires:
    - All metadata fields (title, authors, abstract, method, voice, peer_reviewed)
    - Body review completed (body_reviewed=True)
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

    # Check body review is complete
    if not data.get('body_reviewed'):
        return {
            "success": False,
            "error": "BODY_NOT_REVIEWED",
            "details": "Body review not completed",
            "action": f"Call get_body_for_review('{slug}') then complete_body_review('{slug}', ...) first."
        }

    # Validate required fields
    missing = []
    for field in ['title', 'authors', 'abstract', 'method', 'voice']:
        if not data.get(field):
            missing.append(field)
    if data.get('peer_reviewed') is None:
        missing.append('peer_reviewed')

    if missing:
        return {
            "success": False,
            "error": "MISSING_REQUIRED",
            "details": f"Cannot submit: missing required fields: {', '.join(missing)}",
            "action": "Call complete_article_review() to fill missing fields first."
        }

    # Generate article_id from title and authors
    article_id = generate_article_id(data.get('title', ''), data.get('authors', ''), data.get('year', ''))

    # Import database
    from .database import get_database
    db = get_database()

    try:
        # Create the database record
        result = db.create_preprocessing_article(
            article_id=article_id,
            source_title=data.get('title'),
            authors=data.get('authors'),
            abstract=data.get('abstract'),
            body_html=data.get('body_html', ''),
            doi=data.get('doi'),
            citation=data.get('citation'),
            year=data.get('year'),
            method=data.get('method'),
            voice=data.get('voice'),
            peer_reviewed=data.get('peer_reviewed'),
            references_json=json.dumps(data.get('references', [])) if data.get('references') else None,
        )

        # Check if database operation succeeded
        if not result.get('success'):
            return {
                "success": False,
                "error": result.get('error', 'DATABASE_ERROR'),
                "details": result.get('details', 'Unknown error'),
                "action": "Check error details. Article may already exist."
            }

        return {
            "success": True,
            "article_id": article_id,
            "slug": slug,
            "status": "preprocessing",
            "message": "Article submitted for human review.",
            "admin_url": f"/admin/review/{slug}",
            "next_step": "Human reviews at /admin/review. After approval, article becomes 'pending' for translation."
        }

    except Exception as e:
        logger.exception(f"Failed to create database record for {slug}")
        return {
            "success": False,
            "error": "DATABASE_ERROR",
            "details": str(e),
            "action": "Check error details and database connection."
        }


def get_preprocessing_status() -> dict[str, Any]:
    """
    Get overview of preprocessing pipeline status.
    """
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Intake PDFs
    intake_pdfs = list(INTAKE_DIR.glob("*.pdf"))

    # Processed PDFs (already extracted)
    processed_pdfs = list(PROCESSED_DIR.glob("*.pdf"))

    # Extracted (have .json but not _parsed.json)
    extracted_jsons = {f.stem for f in CACHE_DIR.glob("*.json") if not f.stem.endswith('_parsed')}
    parsed_jsons = {f.stem.replace('_parsed', '') for f in CACHE_DIR.glob("*_parsed.json")}

    not_parsed = extracted_jsons - parsed_jsons

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
        "cache": {
            "extracted": len(extracted_jsons),
            "parsed": len(parsed_jsons),
            "not_parsed": list(not_parsed)[:10]  # First 10
        },
        "database": {
            "preprocessing": progress.get('preprocessing', 0),
            "pending": progress.get('pending', 0),
            "in_progress": progress.get('in_progress', 0),
            "translated": progress.get('translated', 0),
            "skipped": progress.get('skipped', 0)
        },
        "next_step": "Call list_intake_pdfs() to see available PDFs, or get_next_article() to start translating."
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


def slugify(text: str) -> str:
    """Convert text to a clean URL-safe slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    text = re.sub(r'-+', '-', text)
    return text


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
