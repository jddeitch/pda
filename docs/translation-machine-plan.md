# Plan: MCP-Based Translation Pipeline with Failure Mode Mitigation

## Executive Summary

Build an MCP server that enforces the translation pipeline deterministically, accounting for Claude's known failure modes. The server validates all inputs against taxonomy.yaml, enforces workflow order, and maintains state in SQLite so any session can resume.

**Core insight:** There are two risks with Claude during translation:
1. **Quantity problems** (summarizing, skipping) — solved by automated checks at save-time
2. **Quality problems** (editorial drift, "improvements") — mitigated by chunked delivery that prevents seeing the whole article at once

---

## Technical Decisions

These decisions resolve ambiguities in the implementation:

### D1: Chunk Accumulation
Claude holds translated chunks in memory during the loop. At save time, Claude concatenates and submits `translated_full_text`. If session crashes mid-article, work is lost — but this is acceptable because articles take 5-10 minutes max and `processing_status = 'in_progress'` means next session picks it up fresh.

**Crash recovery clarification:** Partial translations are NOT persisted. If a crash occurs mid-article, the next session starts fresh from chunk 1. The `in_progress` status signals "restart this article from the beginning" not "resume from where you left off."

### D2: Classification Signals
The `signals:` lists in taxonomy.yaml are decision aids for Claude, not machine-readable rules. Claude observes these while reading chunks (e.g., "University of X" suggests `academic`) and makes the classification call at the end. No server-side signal detection.

### D3: Chunk Boundaries
Split on double newlines (`\n\n`). Target 4 paragraphs per chunk. If a "paragraph" exceeds 500 words, split at ~400 words on a sentence boundary (using spaCy). Academic paper sections become natural chunks.

### D4: Chunk Translation Storage
No intermediate storage. Claude accumulates translations in its context window during the session. Submits complete `translated_full_text` at save time.

### D5: Admin Interface
Add pages to existing Astro site under `/admin/`. Uses same SQLite database via `src/lib/db.ts`. Static pages query DB at build time; client-side refresh button calls API route for live stats.

### D6: Session State
- **Chunk cache**: In-memory dict keyed by article_id. Lost on server restart but cheap to regenerate.
- **Session counter**: SQLite table `session_state` with `articles_processed_count` and `last_reset_at`. Resets via `reset_session_counter()` or automatically at midnight.

### D7: Claude's Complete Workflow Behavior

**This is the single source of truth for Claude's expected behavior during translation.**

Claude maintains these in working memory during the session:
- `translated_title: str` — translated after receiving article
- `translated_summary: str` — translated after receiving article
- `translated_chunks: list[str]` — accumulated during chunk loop
- `observed_signals: dict` — method/voice/peer_reviewed signals seen
- `flags_to_report: list[dict]` — flags with details

**Complete workflow:**

```
1. RECEIVE ARTICLE
   response = get_next_article()
   IF response.status == "SESSION_PAUSE": STOP, inform user
   IF response.status == "COMPLETE": STOP, inform user
   article = response.article

2. TRANSLATE TITLE + SUMMARY
   translated_title = translate(article.source_title)
   translated_summary = translate(article.summary_original)

3. CHUNK LOOP (if open_access = true)
   IF article.open_access == false: SKIP to step 4

   translated_chunks = []
   chunk_number = 1

   LOOP:
     response = get_chunk(article.id, chunk_number)

     IF response.error:
       # Extraction failed — skip this article
       skip_article(article.id, response.error, "PDFEXTRACT")
       GOTO step 1

     IF response.complete == true:
       # No more chunks — exit loop
       BREAK

     # Translate this chunk using provided glossary terms
     translation = translate(response.text, response.glossary_terms)
     translated_chunks.append(translation)

     # Note any signals for classification
     observe_signals(response.text)

     # Note any flags (tables, figures, ambiguity, etc.)
     note_flags(response.text)

     # Handle extraction warnings (proceed but flag)
     IF response.extraction_problems:
       flags_to_report.extend([
         {"code": p, "detail": "Extraction warning"}
         for p in response.extraction_problems
       ])

     chunk_number += 1

   translated_full_text = "\n\n".join(translated_chunks)

4. CLASSIFY
   # Synthesize classification from observed signals
   method = determine_method(observed_signals)
   voice = determine_voice(observed_signals)
   peer_reviewed = determine_peer_reviewed(observed_signals)
   categories = determine_categories(article_content)
   keywords = extract_keywords(article_content)

   response = validate_classification(
     article_id=article.id,
     method=method,
     voice=voice,
     peer_reviewed=peer_reviewed,
     open_access=article.open_access,
     primary_category=categories[0],
     secondary_categories=categories[1:],
     keywords=keywords
   )

   IF response.valid == false:
     # Fix errors and retry
     fix_errors(response.errors)
     RETRY validate_classification()

   validation_token = response.token

5. SAVE
   response = save_article(
     article_id=article.id,
     validation_token=validation_token,
     source=derive_source(),  # Or flag SRCUNK if unknown
     doi=article.doi,
     translated_title=translated_title,
     translated_summary=translated_summary,
     translated_full_text=translated_full_text,  # NULL if paywalled
     flags=flags_to_report
   )

   IF response.success == false AND response.blocking_flags:
     # Must fix translation issues
     fix_translation(response.blocking_flags)
     # Re-validate (token expired) and retry save
     RETRY from step 4

   IF response.success == false AND response.error == "INVALID_TOKEN":
     # Token expired — re-validate
     RETRY from step 4

6. CONTINUE OR PAUSE
   IF response contains SESSION_PAUSE indicator:
     STOP, inform user to review in /admin
   ELSE:
     GOTO step 1
```

**Key behaviors:**
- Title and summary are ALWAYS translated, even for paywalled articles
- Extraction warnings (COLUMNJUMBLE, etc.) don't stop translation — they become flags
- Extraction blockers (UNUSABLE, GARBLED) trigger skip_article()

**Error handling for BLOCKING flags:**
```
IF save_article() returns BLOCKING flags:
  1. Read the details (e.g., "Source: 45 sentences, Target: 32")
  2. Identify what was missed or added
  3. Fix the translation
  4. Re-validate and re-save
  5. IF same BLOCKING flag persists after fix:
     Call skip_article() with reason explaining the issue
     Do NOT retry more than once — one fix attempt, then skip
```

### D8: Validation Token Mechanism
`validate_classification()` returns a token on success that must be passed to `save_article()`:
```python
# validate_classification() returns:
{"valid": True, "token": "abc123-def456"}

# save_article() requires:
save_article(article_id, validation_token="abc123-def456", ...)
```
Tokens are single-use and expire after 30 minutes. This makes the workflow dependency explicit without server-side state tracking. The 30-minute window accommodates long articles with many chunks while still preventing stale tokens from being reused across sessions.

### D9: skip_article() Semantics
```python
def skip_article(article_id: str, reason: str, flag_code: str) -> dict:
    """
    - Sets processing_status = 'skipped'
    - Stores reason in processing_notes
    - Stores flag_code in processing_flags as JSON array
    - Does NOT increment session counter (skips don't count toward review interval)
    - Skipped articles can be reset to 'pending' via admin interface
    - Returns {"success": True, "article_id": "..."}
    """
```

### D10: Title and Summary Translation Timing
Title and summary are translated BEFORE the chunk loop:
1. `get_next_article()` returns `source_title` and `summary_original`
2. Claude translates title → `translated_title`
3. Claude translates summary → `translated_summary`
4. THEN Claude enters the chunk loop for full text (if open_access)
5. All three are submitted together in `save_article()`

This ensures title/summary are always translated, even for paywalled articles.

### D11: Paywalled Article Flow
For articles where `open_access = 0`:
1. `get_next_article()` returns the article with `open_access: false`
2. Claude translates title and summary (from metadata)
3. Claude does NOT call `get_chunk()` — there is no full text to chunk
4. Claude proceeds directly to `validate_classification()`
5. `save_article()` is called with `translated_full_text = null`

The server accepts `null` for `translated_full_text` when `open_access = 0`.

### D12: Glossary Matching Algorithm
```python
def find_glossary_terms_in_text(text: str, glossary: dict) -> dict[str, str]:
    """
    1. Normalize text: lowercase, replace hyphens with spaces
    2. For each glossary entry:
       a. Check exact match (case-insensitive)
       b. Check hyphenation variant ("demand avoidance" ↔ "demand-avoidance")
       c. Check lemmatized forms via spaCy (en_core_web_sm)
       d. Check abbreviation if defined in glossary entry
    3. Return {en_term: fr_term} for all matches found

    Glossary entries may have optional fields:
      demand_avoidance:
        en: "demand avoidance"
        fr: "évitement des demandes"
        fr_alt: ["évitement de la demande"]  # Acceptable variants
        abbreviation: "DA"  # optional
    """

def verify_glossary_terms(source_text: str, translation: str, glossary: dict) -> list[str]:
    """
    Returns list of missing terms for TERMMIS flag.
    Accepts primary fr term OR any fr_alt variant.
    """
    expected = find_glossary_terms_in_text(source_text, glossary)
    missing = []

    for en_term, entry in expected.items():
        # Get primary and alternative French terms
        fr_primary = entry if isinstance(entry, str) else entry.get('fr', '')
        fr_alternatives = []
        if isinstance(entry, dict) and 'fr_alt' in entry:
            fr_alternatives = entry['fr_alt']

        all_acceptable = [fr_primary.lower()] + [alt.lower() for alt in fr_alternatives]

        # Check if ANY acceptable variant appears in translation
        if not any(alt in translation.lower() for alt in all_acceptable if alt):
            missing.append(f"{en_term} → {fr_primary}")

    return missing
```

**Edge case: Terms in direct quotes.** If the source contains `as Smith called it, "demand avoidance"` and Claude correctly leaves the quoted term in English, TERMMIS will flag it. This is **intended behavior** — the human reviewer sees the flag, recognizes it's a quote, and marks it reviewed. We flag uncertainty rather than trying to detect quotes automatically.

### D13: PDF and Source Content Storage

**Two separate folders with distinct purposes:**

```
/Users/jd/Projects/pda/
├── cache/                    # AUTO-MANAGED: Downloaded/cached content for articles in DB
│   └── articles/
│       ├── post-id-16054.pdf     # Cached from source_url
│       ├── post-id-16049.html    # Cached HTML when PDF not available
│       └── post-id-11849.txt     # Pre-extracted text (manual preprocessing)
│
└── intake/                   # HUMAN-MANAGED: New articles to be added to DB
    └── articles/
        ├── new-paper-2024.pdf    # Dropped by human
        └── another-study.pdf     # Awaiting import
```

**cache/articles/**:
- MCP server writes here automatically when fetching from source_url
- Named by article_id from database
- Formats: .pdf, .html, .txt (preprocessed)
- Server checks here BEFORE fetching from URL

**intake/articles/**:
- Human drops new PDFs here
- `ingest_article()` tool reads these into database (see D21)
- After ingestion, PDF moves to cache/articles/

### D14: Source Field Derivation

The `source` field (journal/institution name) is derived through a cascade:

```python
def derive_source(article: dict, extracted_text: str, source_url: str) -> str:
    """
    Cascade of checks to populate source field.
    Returns best available source attribution.
    """
    # 1. PDF metadata — most authoritative
    if pdf_metadata := extract_pdf_metadata(article['cached_path']):
        if journal := pdf_metadata.get('journal'):
            return journal
        if publisher := pdf_metadata.get('publisher'):
            return publisher

    # 2. Text content — look for journal header/footer
    if journal := find_journal_in_text(extracted_text):
        return journal  # e.g., "Autism" from header

    # 3. DOI lookup — CrossRef API
    if article.get('doi'):
        if journal := lookup_doi_journal(article['doi']):
            return journal

    # 4. URL domain — fallback
    domain = urlparse(source_url).netloc
    source_map = {
        'pdasociety.org.uk': 'PDA Society',
        'sciencedirect.com': 'Elsevier',  # Will be overwritten by DOI lookup
        'tandfonline.com': 'Taylor & Francis',
        'onlinelibrary.wiley.com': 'Wiley',
    }
    return source_map.get(domain, domain)

def find_journal_in_text(text: str) -> str | None:
    """
    Look for journal name in first 500 chars (header area).
    Common patterns:
    - "Published in: Journal Name"
    - "Journal Name, Vol. X, No. Y"
    - "© 2023 Journal Name"
    """
    # Regex patterns for common journal header formats
    patterns = [
        r'Published in[:\s]+([A-Z][^,\n]{5,50})',
        r'([A-Z][A-Za-z\s&]+),?\s+Vol\.?\s*\d+',
        r'©\s*\d{4}\s+([A-Z][^,\n]{5,50})',
    ]
    first_500 = text[:500]
    for pattern in patterns:
        if match := re.search(pattern, first_500):
            return match.group(1).strip()
    return None
```

**When source derivation fails:** Flag with SRCUNK (source unknown) but proceed — human reviews later.

### D15: Classification Signals Are Documentation Only

The `signals:` lists in taxonomy.yaml are **documentation for Claude**, not machine logic.

- They are NOT returned by any MCP tool
- They are NOT used for automated classification
- They exist solely so Claude can read taxonomy.yaml and understand what each category means
- The MCP server loads taxonomy.yaml for *validation* (checking valid values), not *classification*

Claude makes classification decisions based on article content, using signals as mental reference. The server only validates that the chosen values are in the allowed set.

### D16: Article ID Format

Article IDs are **TEXT strings** like `"post-id-16054"` (from original PDA Society scrape).

```python
# All tool parameters use string IDs
def get_chunk(article_id: str, chunk_number: int) -> dict: ...
def validate_classification(article_id: str, ...) -> dict: ...
def save_article(article_id: str, ...) -> dict: ...
def skip_article(article_id: str, ...) -> dict: ...
```

New articles added via intake/ will use slugified titles: `"smith-2024-demand-avoidance"`.

### D17: Validation Token Storage

Tokens are stored in SQLite for crash recovery and expiry:

```sql
CREATE TABLE IF NOT EXISTS validation_tokens (
    token TEXT PRIMARY KEY,
    article_id TEXT NOT NULL,
    classification_data TEXT NOT NULL,  -- JSON blob of validated classification
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    used INTEGER DEFAULT 0,
    FOREIGN KEY (article_id) REFERENCES articles(id)
);

-- Cleanup expired tokens (run periodically or on server start)
DELETE FROM validation_tokens WHERE created_at < datetime('now', '-1 hour');
```

**Token classification_data contents:**

The `classification_data` JSON blob stores ALL validated classification fields:

```json
{
  "method": "empirical",
  "voice": "academic",
  "peer_reviewed": true,
  "open_access": true,
  "primary_category": "fondements",
  "secondary_categories": ["presentation_clinique"],
  "keywords": ["PDA", "demand avoidance", "autism", "anxiety", "children"]
}
```

This matches the `validate_classification()` parameter format (see D20).

This means:
- Claude passes classification to `validate_classification()` ONCE
- Server validates and stores in token
- `save_article()` retrieves classification FROM the token
- Claude does NOT re-pass classification params to `save_article()`

**Token lifecycle:**
1. `validate_classification()` creates token, stores classification data in `classification_data` JSON blob
2. `save_article()` validates token exists, not used, not expired (30 min)
3. `save_article()` retrieves classification from token — Claude doesn't re-pass classification params
4. On successful save, marks token as used
5. Expired/used tokens cleaned up hourly

**Context optimization:** Classification data is stored with the token, so Claude doesn't need to hold classification details in context between `validate_classification()` and `save_article()`. This reduces token usage during translation.

### D18: spaCy Model Loading

Models are loaded **once at server startup**, not per-request:

```python
# In mcp_server/quality_checks.py
import spacy

# Load at module import (server startup)
nlp_en = spacy.load("en_core_web_sm")
nlp_fr = spacy.load("fr_core_news_sm")

# Takes ~2-3 seconds total on first import
# Reused for all subsequent calls
```

If models aren't installed, server startup fails with clear error:
```
ERROR: spaCy model 'en_core_web_sm' not found.
Run: python -m spacy download en_core_web_sm
```

### D19: PDF Cache Path Convention

```python
CACHE_DIR = Path("/Users/jd/Projects/pda/cache/articles")

def get_cached_path(article_id: str) -> Path | None:
    """
    Returns path to cached content, or None if not cached.
    Checks for multiple formats in order of preference.
    """
    for ext in ['.txt', '.pdf', '.html']:  # .txt = preprocessed, preferred
        path = CACHE_DIR / f"{article_id}{ext}"
        if path.exists():
            return path
    return None

def cache_content(article_id: str, content: bytes, source_url: str) -> Path:
    """
    Saves fetched content to cache.
    Extension determined by content type or URL.
    """
    if source_url.endswith('.pdf') or content[:4] == b'%PDF':
        ext = '.pdf'
    elif b'<html' in content[:1000].lower():
        ext = '.html'
    else:
        ext = '.txt'

    path = CACHE_DIR / f"{article_id}{ext}"
    path.write_bytes(content)
    return path
```

**Manual preprocessing:** Human saves preprocessed text as `cache/articles/{article_id}.txt`. The `.txt` extension is checked first, so it takes precedence over `.pdf`.

### D20: Categories Parameter Format

```python
# In validate_classification()
primary_category: str = "fondements"
secondary_categories: list[str] = ["presentation_clinique"]  # May be empty

# Validation rules:
# - primary_category is required, must exist in taxonomy.yaml
# - secondary_categories is optional (0-2 items), all must exist in taxonomy.yaml
# - Total categories: 1-3
# - No duplicates between primary and secondary

# Server converts to SQL:
# INSERT INTO article_categories (article_id, category_id, is_primary)
# VALUES ('post-id-16054', 'fondements', 1),
#        ('post-id-16054', 'presentation_clinique', 0);
```

### D21: Article Ingestion from intake/ Folder

The `ingest_article()` tool completes the automation loop by allowing new PDFs to enter the translation pipeline without manual database insertion.

**Workflow:**
1. Human drops PDF into `intake/articles/`
2. Claude (or human) calls `ingest_article(filename)`
3. Server extracts metadata, derives source, creates article record
4. Article becomes available via `get_next_article()`

```python
def ingest_article(filename: str) -> dict:
    """
    Ingests a PDF from intake/articles/ into the database.

    Steps:
    1. Verify file exists in intake/articles/
    2. Extract PDF metadata (title, authors, DOI if present)
    3. Generate article_id from slugified title + year
    4. Derive source using D14 cascade
    5. Create article record with processing_status = 'pending'
    6. Move PDF to cache/articles/{article_id}.pdf
    7. Return article details for verification

    Parameters:
        filename: Name of PDF file in intake/articles/ (e.g., "smith-2024-pda.pdf")

    Returns on success:
    {
        "success": true,
        "article": {
            "id": "smith-2024-demand-avoidance-study",
            "source_title": "A Study of Demand Avoidance in Children",
            "authors": "Smith, J., Jones, M.",
            "source": "Journal of Autism Research",  # Derived or SRCUNK flag
            "doi": "10.1234/jar.2024.001",  # Or null
            "source_url": null,  # No URL for manually added PDFs
            "open_access": true  # We have the PDF, so we can translate full text
        },
        "next_step": "Verify details are correct, then call get_next_article() to begin translation."
    }

    Returns on failure:
    {
        "success": false,
        "error": "FILE_NOT_FOUND" | "EXTRACTION_FAILED" | "DUPLICATE",
        "details": "..."
    }

    Article ID generation:
    - Extract first author surname, year, and key title words
    - Slugify: lowercase, replace spaces with hyphens, remove special chars
    - Example: "Smith (2024) Demand Avoidance Study" → "smith-2024-demand-avoidance-study"
    - If duplicate, append -2, -3, etc.
    """
```

**PDF metadata extraction:**
```python
def extract_pdf_metadata(pdf_path: Path) -> dict:
    """
    Extracts metadata from PDF using PyMuPDF.
    Falls back to text extraction for title if metadata missing.
    """
    doc = fitz.open(pdf_path)
    metadata = doc.metadata

    result = {
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "subject": metadata.get("subject"),
        "keywords": metadata.get("keywords"),
        "creator": metadata.get("creator"),  # Often contains journal name
    }

    # If no title in metadata, try first page header
    if not result["title"]:
        first_page = doc[0].get_text()
        # Look for title-like text (large font, centered, first lines)
        result["title"] = extract_title_from_text(first_page)

    # Look for DOI in first page
    doi_match = re.search(r'10\.\d{4,}/[^\s]+', first_page)
    if doi_match:
        result["doi"] = doi_match.group(0).rstrip('.')

    return result
```

### D22: Flags Parameter Format

Flags are passed as a single list of dicts, each with `code` and `detail`:

```python
# In save_article()
flags: list[dict]  # [{"code": "TBL", "detail": "2 tables on pages 5-6"}, ...]

# Validation:
# - Each dict must have "code" (string) and "detail" (string)
# - "code" must be a valid flag from taxonomy.yaml processing_flags
# - "detail" can be empty string but must be present

# Server converts to storage:
# processing_flags = json.dumps([f["code"] for f in flags])
# processing_notes = "; ".join(f"[{f['code']}] {f['detail']}" for f in flags if f['detail'])

# Example input:
flags = [
    {"code": "TBL", "detail": "2 tables on pages 5-6"},
    {"code": "TERM", "detail": "Used 'interoception' not in glossary"},
    {"code": "COLUMNJUMBLE", "detail": ""}  # Extraction warning, no detail needed
]

# Stored as:
# processing_flags: ["TBL", "TERM", "COLUMNJUMBLE"]
# processing_notes: "[TBL] 2 tables on pages 5-6; [TERM] Used 'interoception' not in glossary"
```

### D23: Session State Timezone

The session counter uses **local time** for midnight reset to match human work patterns:

```sql
-- Use localtime for date comparison
CREATE TABLE IF NOT EXISTS session_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    articles_processed_count INTEGER DEFAULT 0,
    human_review_interval INTEGER DEFAULT 5,
    last_reset_at TEXT DEFAULT (datetime('now', 'localtime')),
    last_reset_date TEXT DEFAULT (date('now', 'localtime'))
);
```

```python
def check_session_limit() -> bool:
    """Returns True if SESSION_PAUSE should be returned."""
    row = db.execute("""
        SELECT articles_processed_count, human_review_interval, last_reset_date
        FROM session_state WHERE id = 1
    """).fetchone()

    # Auto-reset at local midnight
    today_local = date.today().isoformat()  # Uses system timezone
    if row['last_reset_date'] != today_local:
        db.execute("""
            UPDATE session_state
            SET articles_processed_count = 0,
                last_reset_date = date('now', 'localtime'),
                last_reset_at = datetime('now', 'localtime')
            WHERE id = 1
        """)
        return False

    return row['articles_processed_count'] >= row['human_review_interval']
```

### D24: open_access Field Population

```
For existing articles (from PDA Society scrape):
- Default to `open_access = NULL` (unknown)
- On first get_chunk() call, server attempts to fetch source_url
- If PDF/HTML is accessible and extractable: set `open_access = 1`
- If paywall detected or fetch fails: set `open_access = 0`
- Once set, value persists in database

Paywall detection heuristics:
- HTTP 403/401 response
- Page contains "purchase", "subscribe", "access denied"
- Content length < 1000 chars (stub page)
- Known paywall domains: sciencedirect.com, tandfonline.com, etc.

For ingested articles (from intake/):
- `open_access = 1` always (we have the PDF)

For manually added articles:
- Human sets `open_access` explicitly
```

### D25: Admin Interface Access

The `/admin/*` routes are **local development only** — not deployed to production.

```javascript
// astro.config.mjs
export default defineConfig({
  vite: {
    define: {
      'import.meta.env.ENABLE_ADMIN': JSON.stringify(process.env.ENABLE_ADMIN === 'true')
    }
  }
});
```

```astro
---
// site/src/pages/admin/[...path].astro
if (!import.meta.env.ENABLE_ADMIN) {
  return Astro.redirect('/');
}
---
```

**Usage:**
- Local: `ENABLE_ADMIN=true npm run dev`
- Production: `ENABLE_ADMIN` not set → admin routes redirect to home

### D26: Chunk Cache Lifecycle

```python
from datetime import datetime, timedelta

# In-memory cache with TTL
_chunk_cache: dict[str, tuple[list[str], datetime]] = {}
CACHE_TTL = timedelta(hours=1)

def get_chunks_for_article(article_id: str) -> list[str]:
    """
    Returns cached chunks or generates new ones.
    """
    if article_id in _chunk_cache:
        chunks, cached_at = _chunk_cache[article_id]
        if datetime.now() - cached_at < CACHE_TTL:
            return chunks
        # Expired — regenerate
        del _chunk_cache[article_id]

    # Generate fresh chunks
    text = extract_article_text(article_id)
    chunks = split_into_chunks(text)
    _chunk_cache[article_id] = (chunks, datetime.now())
    return chunks

def clear_chunk_cache(article_id: str = None):
    """
    Called after save_article() or skip_article().
    """
    if article_id:
        _chunk_cache.pop(article_id, None)
    else:
        _chunk_cache.clear()
```

**Cache cleared:**
- After successful `save_article()` — article is done
- After `skip_article()` — article is skipped
- On server restart — memory lost anyway
- After 1 hour — prevents stale data if session interrupted

### D27: Glossary Versioning

The glossary may be updated during the project (new terms added, corrections made). To track which glossary version was used for each translation:

```sql
-- Add to articles table (migration)
ALTER TABLE articles ADD COLUMN glossary_version TEXT;
```

**How it works:**
1. `glossary.yaml` includes a `version` field at the top (e.g., `version: "2025-01-11"`)
2. `save_article()` records the current glossary version in the article record
3. TERMMIS checks are evaluated against the glossary version active at translation time
4. If glossary is updated, previously translated articles are NOT automatically re-flagged

**Re-validation (optional admin feature):**
- Admin dashboard can show articles translated with older glossary versions
- Human can trigger re-validation against current glossary if needed
- This is informational only — doesn't invalidate existing translations

```python
# In glossary.py
def get_glossary_version() -> str:
    """Returns version string from glossary.yaml header."""
    with open(GLOSSARY_PATH) as f:
        glossary = yaml.safe_load(f)
    return glossary.get('version', 'unknown')

# In save_article()
article_data['glossary_version'] = get_glossary_version()
```

---

## Part 1: The Two Risks and How We Address Them

### Risk 1: Quantity Problems (Summarizing/Skipping)

**What it looks like:**
- Skip paragraphs entirely
- Compress 3 sentences into 1
- "Get the gist" instead of translating

**Detection (at save-time):**
- spaCy sentence count ratio — catches >15% variance
- Word count ratio — catches compression/expansion outside 0.9-1.5x
- Paragraph count — catches missing sections

**These automated checks work.** No per-paragraph logging needed.

### Risk 2: Quality Problems (Editorial Drift)

**What it looks like:**
- "Clarify" ambiguous phrasing
- Add or remove hedging
- Smooth awkward sentences
- Impose a different register

**Detection:** Weak. No automated check catches "you added a hedge."

**Prevention:** Chunked delivery. The MCP server feeds source text in small chunks (3-5 paragraphs). Claude never sees the whole article at once, so:
- Can't "skim and summarize"
- Must stay close to the source text
- Each chunk forces fresh attention

**Human review interval** catches drift before it compounds.

---

## Part 2: The Ideal Translation Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ARTICLE WORKFLOW                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. SELECT                                                                   │
│     Claude calls: get_next_article()                                        │
│     Server returns: article metadata + fresh taxonomy + open_access status  │
│     State: article.processing_status = 'in_progress'                        │
│                                                                              │
│  2. TRANSLATE TITLE + SUMMARY                                                │
│     Claude translates source_title → translated_title                       │
│     Claude translates summary_original → translated_summary                 │
│     These are ALWAYS translated, even for paywalled articles               │
│                                                                              │
│  3. CHUNK LOOP (if open_access = true)                                       │
│     IF open_access = false: SKIP to step 4                                  │
│                                                                              │
│     3a. Claude calls: get_chunk(article_id, chunk=1)                        │
│         Server returns: chunk + glossary terms + instruction                │
│                                                                              │
│     3b. Claude translates chunk, appends to translated_chunks list          │
│         Notes classification signals, notes any flags                       │
│                                                                              │
│     3c. Repeat 3a-3b until server returns {complete: true}                  │
│                                                                              │
│     3d. Claude joins translated_chunks → translated_full_text               │
│                                                                              │
│  4. CLASSIFY                                                                 │
│     Claude synthesizes: method, voice, peer_reviewed from signals           │
│     Claude assigns: categories (1-3, one primary)                           │
│     Claude extracts: keywords (5-15)                                        │
│     Claude calls: validate_classification(...)                              │
│     Server: checks all values, returns validation_token on success          │
│                                                                              │
│  5. SAVE                                                                     │
│     Claude calls: save_article(validation_token, ...)                       │
│     Server: validates token (rejects if invalid/expired)                    │
│     Server: runs quality checks (sentence count, word ratio, Jaccard)       │
│     Server: if BLOCKING flags → REJECT, Claude must fix                     │
│     Server: if WARNING flags → ACCEPT, flags stored for human review       │
│     Server: writes processing_flags + processing_notes to articles table   │
│     Server: saves all tables in single transaction                          │
│                                                                              │
│  6. CHECK BATCH LIMIT                                                        │
│     Server checks: articles_this_session < human_review_interval            │
│     If limit reached: return SESSION_PAUSE                                  │
│     If not: continue to next article                                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Why Chunked Delivery Prevents Quality Drift

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CHUNKED DELIVERY = QUALITY CONTROL                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  WITHOUT CHUNKS (whole article at once):                                    │
│    Claude sees entire 5000-word article                                     │
│    Temptation: "I understand what this is about, let me express it better" │
│    Result: Editorial drift, smoothing, "improvements"                       │
│                                                                              │
│  WITH CHUNKS (3-5 paragraphs at a time):                                    │
│    Claude sees only current chunk                                           │
│    No overview to "understand and improve"                                  │
│    Must translate what's in front of it                                     │
│    Each chunk is fresh attention to source text                             │
│                                                                              │
│  THE LOOP:                                                                   │
│    get_chunk(1) → translate → get_chunk(2) → translate → ... → save        │
│                                                                              │
│  Server controls the pacing. Claude cannot skip ahead.                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Translation Principle: Match the Source

**The register is IN the source text. Don't infer it and apply it — just match what's there.**

- If the author writes formally, translate formally
- If the author writes warmly, translate warmly
- If the author uses passive voice, use passive voice
- If the author uses first person, use first person

Classification (academic/practitioner/organization/individual) is metadata for the database. It helps users find and filter content. It does NOT drive how you translate — the source text itself does that.

**What must be consistent:** Glossary terms. Use "évitement des demandes" not "évitement de la demande" regardless of register.

**What varies with the source:** Sentence structure, tone, formality, voice (active/passive), complexity.

### What "Success" Looks Like

For each article, after save_article() returns SUCCESS:

**articles table:**
- `method` = one of: empirical, synthesis, theoretical, lived_experience
- `voice` = one of: academic, practitioner, organization, individual
- `peer_reviewed` = 0 or 1
- `open_access` = 0 or 1
- `source` = non-empty string (journal/institution name)
- `processing_status` = 'translated'
- `processing_flags` = JSON array of flag codes
- `processed_at` = ISO timestamp

**translations table:**
- `translated_title` = non-empty French title
- `translated_summary` = non-empty French summary (150+ chars)
- `translated_full_text` = French text if open_access=1, else NULL
- `status` = 'translated'

*(Note: Flag information stored in articles.processing_flags and articles.processing_notes — no duplication in translations table)*

**article_categories table:**
- 1-3 rows for this article
- Exactly 1 row has `is_primary = 1`
- All category_ids exist in categories table

**article_keywords table:**
- 5-15 rows for this article
- All keyword_ids exist in keywords table

---

## Part 3: Claude's Failure Modes and Mitigations

### Failure Mode → Mitigation Matrix

#### Memory and Context Failures

| Failure Mode | Description | Mitigations |
|--------------|-------------|-------------|
| **Context decay** | As conversation grows, earlier instructions get summarized | **Belt:** MCP returns fresh taxonomy with each get_next_article(). **Braces:** Chunked delivery keeps each unit small. |
| **Session discontinuity** | New session doesn't know what previous session did | **Belt:** All state in SQLite. **Braces:** get_next_article() queries DB for next pending. |
| **Glossary forgetting** | Forget to check glossary.yaml before translating | **Belt:** MCP provides glossary terms with each chunk. **Braces:** TERMMIS flag at save-time catches missing terms. |
| **Taxonomy forgetting** | Forget exact taxonomy values | **Belt:** MCP validate_classification() rejects invalid values. **Braces:** get_next_article() returns valid values list. |

#### Translation Quality Failures

| Failure Mode | Description | Mitigations |
|--------------|-------------|-------------|
| **Summarizing/skipping** | Skip sentences or paragraphs | **Belt:** spaCy sentence count at save-time. **Braces:** Word count ratio check. |
| **Editorial drift** | "Improve" or "clarify" the original | **Belt:** Chunked delivery prevents overview. **Braces:** Human review interval. |
| **Register imposition** | Impose a register instead of matching source | **Belt:** "Match the source" principle. **Braces:** Chunked delivery keeps attention on source. |
| **Term invention** | Create new French terms instead of using glossary | **Belt:** Glossary terms provided with each chunk. **Braces:** TERMMIS flag catches missing terms. |

#### Process Failures

| Failure Mode | Description | Mitigations |
|--------------|-------------|-------------|
| **Step skipping** | Skip validation or classification | **Belt:** MCP enforces workflow order — save_article() rejected without validate_classification(). |
| **Premature completion** | Mark article done before all fields populated | **Belt:** save_article() validates ALL required fields. |
| **Batch enthusiasm** | Try to do too many articles at once | **Belt:** Human review interval enforces pauses. |

### Automated Quality Checks (at save-time)

| Check | What It Does | Catches |
|-------|--------------|---------|
| **Sentence count** | spaCy tokenizer on source vs target. Flag if ratio outside 0.85-1.15 | Omissions, over-splitting |
| **Word count ratio** | EN→FR typically 1.1-1.2x. Flag if outside 0.9-1.5 | Major content changes |
| **Glossary term recall** | Measure what percentage of expected glossary terms appear in translation | Creative rephrasing, editorial drift |
| **Glossary terms** | Verify expected French terms appear in translation | Term invention, inconsistent terms |
| **Statistics** | Regex for numbers in source, verify same in target | Accidental modification of data |

#### Glossary Term Recall Check

This catches "creative" translation where sentence count is preserved but glossary terms are missing or replaced:

```python
def check_glossary_recall(source_en: str, translation_fr: str, glossary: dict) -> dict:
    """
    Measures what percentage of expected glossary terms appear in translation.

    Uses RECALL (intersection / expected) rather than Jaccard (intersection / union).
    Recall is the right metric because:
    - Jaccard penalizes translations for having additional content words (normal)
    - Recall only checks if expected terms are present (what we actually care about)

    Empirically validated on known-good translations:
    - Good translations: 0.77-0.88 recall
    - Drifted translations: ~0.23 recall
    - Threshold 0.7 correctly separates good from drifted
    """
    # Extract expected French terms from glossary matches in source
    expected_terms = find_glossary_terms_in_text(source_en, glossary)
    expected_fr_words = set()
    for fr_term in expected_terms.values():
        # Lemmatize expected French terms
        doc = nlp_fr(fr_term.lower())
        expected_fr_words.update(token.lemma_ for token in doc if token.pos_ in ('NOUN', 'VERB', 'ADJ'))

    # Skip check if too few terms to be meaningful
    if len(expected_fr_words) < 3:
        return {"recall": 1.0, "flag": None}

    # Extract actual content words from translation
    doc = nlp_fr(translation_fr.lower())
    actual_fr_words = set(token.lemma_ for token in doc if token.pos_ in ('NOUN', 'VERB', 'ADJ'))

    # RECALL: what percentage of expected terms appeared?
    intersection = expected_fr_words & actual_fr_words
    recall = len(intersection) / len(expected_fr_words)

    return {
        "recall": round(recall, 2),
        "missing_expected": list(expected_fr_words - actual_fr_words)[:10],
        "flag": "WORDDRIFT" if recall < 0.7 else None
    }
```

**Why Recall over Jaccard:** Jaccard (intersection/union) penalizes translations for having additional content words beyond the glossary — but this is normal behavior for any real translation. Recall (intersection/expected) correctly measures "of the glossary terms we expected, what percentage appeared?" Empirically tested on known-good translations from our database showed 0.77-0.88 recall, while a synthetically drifted translation scored 0.23. The 0.7 threshold correctly separates the two.

**Glossary coverage note:** The glossary contains ~200 domain-specific terms across 18 categories (core PDA terms, behavioral, clinical, emotional, etc.). For PDA research articles, expect 5-15 glossary terms per chunk — sufficient coverage for the recall check to be meaningful. The ≥3 term minimum handles edge cases like methodology sections with fewer domain terms.

**BLOCKING FLAGS (save rejected):**
- `SENTMIS` — sentence count mismatch >15%
- `WORDMIS` — word count ratio outside 0.9-1.5

**WARNING FLAGS (save allowed, human reviews later):**
- `WORDDRIFT` — glossary term recall < 0.7 (possible editorial drift)
- `TERMMIS` — expected glossary term missing
- `STATMIS` — statistics may have been modified
- Content flags: `TBL`, `FIG`, `META`, `LONG`, `TERM`, `AMBIG`
- Access flags: `PAYWALL`, `404`, `NOURL`, `PDFEXTRACT`
- Classification flags: `METHUNC`, `VOICEUNC`, `PEERUNC`

---

## Part 4: MCP Server Specification

### 4.1 Tools to Implement

```python
# Tool 1: Get next article to process
def get_next_article() -> dict:
    """
    Returns next article needing work, plus fresh taxonomy data.
    Prioritizes in_progress (crash recovery) over pending.
    Checks human_review_interval — returns SESSION_PAUSE if limit reached.

    WORKFLOW REMINDER (included in every response):
    1. Translate title and summary FIRST (even for paywalled articles)
    2. IF open_access: call get_chunk() in loop until complete
    3. Call validate_classification() with method, voice, peer_reviewed, categories, keywords
    4. Call save_article() with validation_token

    Returns:
    {
        "article": {
            "id": "post-id-16054",
            "source_title": "...",
            "source_url": "...",
            "summary_original": "...",
            "open_access": true,
            "doi": "10.1234/..." | null
        },
        "progress": {"current": 3, "pending": 47, "translated": 2},
        "taxonomy": {"methods": [...], "voices": [...], "categories": [...]},
        "workflow_reminder": "1. Translate title+summary. 2. If open_access, chunk loop. 3. validate_classification(). 4. save_article()."
    }

    Returns on SESSION_PAUSE:
    {
        "status": "SESSION_PAUSE",
        "articles_processed": 5,
        "message": "Human review interval reached. Please review in /admin before continuing."
    }

    Returns when all complete:
    {
        "status": "COMPLETE",
        "translated": 52,
        "skipped": 3,
        "message": "All articles processed."
    }
    """

# Tool 2: Get a chunk of the article for translation
def get_chunk(article_id: str, chunk_number: int) -> dict:
    """
    Returns one chunk (3-5 paragraphs) of the article.
    First call triggers PDF fetch (from cache or URL) and extraction.

    CLAUDE'S BEHAVIOR FOR EACH CHUNK:
    1. Read the instruction field — it contains translation rules
    2. Translate the chunk faithfully
    3. Append translation to your running translated_chunks list
    4. Note any classification signals (method, voice, peer_reviewed)
    5. Note any flags (TBL if tables, FIG if figures, TERM if new terminology)
    6. Call get_chunk(article_id, chunk_number + 1)
    7. Repeat until response contains "complete": true

    RESPONSE SCHEMAS:

    SUCCESS (more chunks remain):
    {
        "chunk_number": 1,
        "total_chunks": 5,
        "text": "...",
        "glossary_terms": {"demand avoidance": "évitement des demandes"},
        "instruction": "Translate this chunk faithfully. Match the author's register...",
        "extraction_warnings": [],  # May contain: ["COLUMNJUMBLE", "NOPARAGRAPHS"]
        "complete": false
    }

    SUCCESS (no more chunks):
    {
        "complete": true,
        "total_chunks": 5,
        "next_step": "Call validate_classification() with your classification decisions."
    }

    EXTRACTION FAILED (blocking — cannot proceed):
    {
        "error": true,
        "error_code": "EXTRACTION_FAILED",
        "problems": ["GARBLED", "TOOSHORT"],
        "action": "Call skip_article(article_id, 'PDF extraction failed: GARBLED', 'PDFEXTRACT')"
    }

    ARTICLE NOT FOUND:
    {
        "error": true,
        "error_code": "ARTICLE_NOT_FOUND",
        "action": "Call get_next_article() to get a valid article."
    }

    KEY DISTINCTIONS:
    - `error: true` distinguishes error responses from success
    - `extraction_warnings` in SUCCESS responses are non-blocking — note as flags but continue
    - `problems` in ERROR responses are blocking — must skip article
    - The 'instruction' field is repeated with EVERY chunk to prevent context decay
    """

# Tool 3: Validate classification before saving
def validate_classification(
    article_id: str,
    method: str,
    voice: str,
    peer_reviewed: bool,
    open_access: bool,
    primary_category: str,              # Required, e.g., "fondements"
    secondary_categories: list[str],    # 0-2 items, e.g., ["presentation_clinique"]
    keywords: list[str]
) -> dict:
    """
    Validates all classification fields against taxonomy.yaml.
    MUST be called before save_article() — returns token required for save.
    See D20 for categories parameter format.

    Checks:
    - method is one of: empirical, synthesis, theoretical, lived_experience
    - voice is one of: academic, practitioner, organization, individual
    - primary_category: required, must exist in taxonomy.yaml
    - secondary_categories: 0-2 items, all must exist, no duplicates with primary
    - keywords: 5-15 entries

    Returns on success:
    {
        "valid": true,
        "token": "abc123-def456",  # Required for save_article()
        "next_step": "Call save_article() with this token within 30 minutes."
    }

    Returns on failure:
    {
        "valid": false,
        "errors": ["Invalid method: 'empiric' — did you mean 'empirical'?", ...],
        "action": "Fix the errors and call validate_classification() again."
    }

    ERROR HANDLING:
    - If errors are returned, fix them and retry validate_classification()
    - Do NOT proceed to save_article() without a valid token
    - Token expires after 30 minutes — if you take too long, re-validate

    Token expires after 30 minutes. Single use.
    """

# Tool 4: Save completed article
def save_article(
    article_id: str,
    validation_token: str,          # Required — from validate_classification()
    source: str,
    doi: str | None,
    translated_title: str,
    translated_summary: str,
    translated_full_text: str | None,  # NULL allowed if open_access=0
    flags: list[dict]               # [{"code": "TBL", "detail": "2 tables on pages 5-6"}, ...]
) -> dict:
    """
    Saves article to database in single transaction.

    Workflow enforcement:
    - Validates token from validate_classification() — rejects if invalid/expired
    - Tokens are single-use and expire after 30 minutes

    Quality checks (run automatically):
    - Sentence count ratio (SENTMIS if >15% variance)
    - Word count ratio (WORDMIS if outside 0.9-1.5)
    - Glossary term recall (WORDDRIFT if < 0.7)
    - Glossary term verification (TERMMIS if missing)
    - Statistics preservation (STATMIS if numbers differ)

    Flag handling (see D22 for format):
    - Claude provides flags as list of {"code": str, "detail": str}
    - Server validates each code against taxonomy.yaml
    - Server stores processing_flags = JSON array of codes
    - Server stores processing_notes = formatted string from details
    - NO duplication in translations table — all flag info on articles table only

    Returns on success:
    {
        "success": true,
        "warning_flags": ["TERMMIS"],  # Empty if none
        "next_step": "Call get_next_article() to continue, or stop if SESSION_PAUSE."
    }

    Returns if BLOCKING flags:
    {
        "success": false,
        "blocking_flags": ["SENTMIS"],
        "details": {"SENTMIS": "Source: 45 sentences, Target: 32 sentences (ratio: 0.71)"},
        "action": "Fix the translation to address the blocking issue, then re-validate and save."
    }

    Returns if token invalid:
    {
        "success": false,
        "error": "INVALID_TOKEN",
        "action": "Call validate_classification() again to get a fresh token."
    }

    ERROR HANDLING:
    - BLOCKING flags: Fix the translation, call validate_classification() again, then save_article()
    - INVALID_TOKEN: Re-validate and retry
    - After 2 failed attempts on same article: call skip_article() with justification
    """

# Tool 5: Skip an article
def skip_article(article_id: str, reason: str, flag_code: str) -> dict:
    """
    Marks article as skipped with reason.
    """

# Tool 6: Get progress summary
def get_progress() -> dict:
    """
    Returns counts by processing_status.
    """

# Tool 7: Set human review interval
def set_human_review_interval(interval: int) -> dict:
    """
    Set how many articles to process before pausing.
    Range: 1-20. Default: 5.
    """

# Tool 8: Reset session counter
def reset_session_counter() -> dict:
    """
    Called after human review. Resets counter, allows processing to continue.
    """

# Tool 9: Ingest new article from intake folder
def ingest_article(filename: str) -> dict:
    """
    Ingests a PDF from intake/articles/ into the database.
    See D21 for full specification.

    Parameters:
        filename: Name of PDF file in intake/articles/ (e.g., "smith-2024-pda.pdf")

    Returns on success:
    {
        "success": true,
        "article": {
            "id": "smith-2024-demand-avoidance-study",
            "source_title": "A Study of Demand Avoidance in Children",
            "authors": "Smith, J., Jones, M.",
            "source": "Journal of Autism Research",
            "doi": "10.1234/jar.2024.001",
            "open_access": true
        },
        "next_step": "Verify details, then call get_next_article() to begin translation."
    }

    Returns on failure:
    {
        "success": false,
        "error": "FILE_NOT_FOUND" | "EXTRACTION_FAILED" | "DUPLICATE",
        "details": "..."
    }
    """
```

### 4.2 The Chunk Delivery Design

The key insight: **Claude cannot skip ahead because the server controls what it sees.**

```python
# In-memory chunk cache (lost on restart, regenerated on demand)
_chunk_cache: dict[str, list[str]] = {}

def split_into_chunks(text: str, target_paragraphs: int = 4) -> list[str]:
    """
    Split text into chunks of ~4 paragraphs each.
    Handles oversized paragraphs by splitting on sentence boundaries.
    """
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    chunks = []
    current_chunk = []

    for para in paragraphs:
        # If paragraph is too long (>500 words), split it
        if len(para.split()) > 500:
            sentences = list(nlp_en(para).sents)
            sub_paras = []
            current_sub = []
            word_count = 0
            for sent in sentences:
                sent_words = len(sent.text.split())
                if word_count + sent_words > 400 and current_sub:
                    sub_paras.append(' '.join(s.text for s in current_sub))
                    current_sub = [sent]
                    word_count = sent_words
                else:
                    current_sub.append(sent)
                    word_count += sent_words
            if current_sub:
                sub_paras.append(' '.join(s.text for s in current_sub))
            # Each sub-paragraph counts as a paragraph
            for sub in sub_paras:
                current_chunk.append(sub)
                if len(current_chunk) >= target_paragraphs:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
        else:
            current_chunk.append(para)
            if len(current_chunk) >= target_paragraphs:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []

    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    return chunks

def get_chunk(article_id: str, chunk_number: int) -> dict:
    """
    Server-side chunking logic.
    """
    # First chunk request triggers extraction and caching
    if article_id not in _chunk_cache:
        text = extract_article_text(article_id)  # PDF extraction with fallbacks
        _chunk_cache[article_id] = split_into_chunks(text)

    chunks = _chunk_cache[article_id]

    if chunk_number > len(chunks):
        # Clear cache after article complete
        del _chunk_cache[article_id]
        return {"complete": True}

    chunk_text = chunks[chunk_number - 1]

    # Find glossary terms in THIS chunk only
    glossary_terms = find_glossary_terms_in_text(chunk_text, glossary_index)

    return {
        "chunk_number": chunk_number,
        "total_chunks": len(chunks),
        "text": chunk_text,
        "glossary_terms": glossary_terms,
        "complete": False
    }
```

### 4.3 Session State Schema

```sql
-- Add to pda.db
CREATE TABLE IF NOT EXISTS session_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    articles_processed_count INTEGER DEFAULT 0,
    human_review_interval INTEGER DEFAULT 5,
    last_reset_at TEXT DEFAULT (datetime('now')),
    last_reset_date TEXT DEFAULT (date('now'))  -- for midnight auto-reset
);

-- Insert singleton if not exists
INSERT OR IGNORE INTO session_state (id) VALUES (1);
```

```python
def check_session_limit() -> bool:
    """Returns True if SESSION_PAUSE should be returned."""
    row = db.execute("""
        SELECT articles_processed_count, human_review_interval, last_reset_date
        FROM session_state WHERE id = 1
    """).fetchone()

    # Auto-reset at midnight
    if row['last_reset_date'] != date.today().isoformat():
        db.execute("""
            UPDATE session_state
            SET articles_processed_count = 0,
                last_reset_date = date('now'),
                last_reset_at = datetime('now')
            WHERE id = 1
        """)
        return False

    return row['articles_processed_count'] >= row['human_review_interval']

def increment_session_count():
    """Called after successful save_article()."""
    db.execute("""
        UPDATE session_state
        SET articles_processed_count = articles_processed_count + 1
        WHERE id = 1
    """)
```

### 4.4 File Structure

```
/Users/jd/Projects/pda/
├── mcp_server/
│   ├── __init__.py
│   ├── server.py           # Main MCP server entry point
│   ├── tools.py            # Tool implementations
│   ├── validation.py       # Validation logic
│   ├── database.py         # SQLite operations + session state
│   ├── taxonomy.py         # Loads taxonomy.yaml (single source of truth)
│   ├── pdf_extraction.py   # PDF extraction with fallback chain
│   ├── glossary.py         # Glossary matching with variants
│   └── quality_checks.py   # spaCy sentence counting, etc.
├── pyproject.toml
└── ... existing files ...
```

### 4.5 Dependencies

```toml
[project]
dependencies = [
    "mcp",
    "PyMuPDF",                # Primary PDF extraction
    "pdfminer.six",           # Fallback 1: better for two-column layouts
    "pdfplumber",             # Fallback 2: different algorithm
    "spacy>=3.7,<4.0",        # Sentence tokenization + glossary lemmatization (pinned major)
    "rapidfuzz",              # Fuzzy matching for glossary
    "pyyaml",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
]
```

**spaCy models required:**
```bash
# Standard installation — models auto-resolve to compatible versions
python -m spacy download en_core_web_sm
python -m spacy download fr_core_news_sm

# Verify installed versions
python -c "import spacy; nlp = spacy.load('en_core_web_sm'); print(f'en: {nlp.meta[\"version\"]}')"
python -c "import spacy; nlp = spacy.load('fr_core_news_sm'); print(f'fr: {nlp.meta[\"version\"]}')"
```

**Setup script (recommended):**
```python
# scripts/setup.py
import subprocess

def download_spacy_models():
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
    subprocess.run(["python", "-m", "spacy", "download", "fr_core_news_sm"])

if __name__ == "__main__":
    download_spacy_models()
```

**Why pin spaCy (not models)?** Sentence tokenization rules change between major versions. Pinning `spacy>=3.7,<4.0` ensures consistent behavior. The models auto-install compatible versions for your spaCy installation, so we pin the library, not individual model versions.

---

## Part 5: PDF Extraction Pipeline

### 5.1 Extraction Chain

```
PRIMARY: PyMuPDF (fitz)
  ↓ if extraction fails or text is unusable
FALLBACK 1: pdfminer.six (better for two-column layouts)
  ↓ if extraction fails or text is unusable
FALLBACK 2: pdfplumber
  ↓ if extraction fails or text is unusable
FLAG: PDFEXTRACT — human preprocesses manually
```

Why three extractors:
- **PyMuPDF**: Fast, handles most single-column PDFs well
- **pdfminer.six**: Better layout analysis for two-column academic papers
- **pdfplumber**: Good for table extraction, different text flow algorithm

### 5.2 Extraction Quality Detection (No Confidence Scores)

Instead of manufacturing confidence scores, we detect **specific, observable problems**:

```python
@dataclass
class ExtractionResult:
    text: str
    extractor_used: str
    problems: list[str]  # List of problem codes
    usable: bool         # Binary: can we proceed or not?

def extract_article_text(pdf_path: str) -> ExtractionResult:
    """
    Tries each extractor, returns first usable result.
    Records which extractor succeeded and any problems detected.
    """
    extractors = [
        ("pymupdf", extract_pymupdf),
        ("pdfminer", extract_pdfminer),
        ("pdfplumber", extract_pdfplumber),
    ]

    for name, extract_fn in extractors:
        try:
            text = extract_fn(pdf_path)
            problems = detect_extraction_problems(text)

            # If no BLOCKING problems, use this extraction
            if "UNUSABLE" not in problems:
                return ExtractionResult(
                    text=text,
                    extractor_used=name,
                    problems=problems,
                    usable=True
                )
        except Exception as e:
            continue

    # All extractors failed
    return ExtractionResult(
        text="",
        extractor_used="none",
        problems=["PDFEXTRACT"],
        usable=False
    )


def detect_extraction_problems(text: str) -> list[str]:
    """
    Detects SPECIFIC, OBSERVABLE problems in extracted text.
    No scores — just problem codes that describe what's wrong.
    """
    problems = []
    words = text.split()

    # BLOCKING: Too short to be a real article
    if len(words) < 100:
        problems.append("UNUSABLE")
        problems.append("TOOSHORT")
        return problems  # No point checking further

    # BLOCKING: Majority garbage characters (encoding failure)
    garbage_chars = set('\ufffd\u2588\u2591\u2592\u2593\x00')
    garbage_count = sum(1 for c in text if c in garbage_chars)
    if garbage_count > len(text) * 0.05:  # >5% garbage
        problems.append("UNUSABLE")
        problems.append("GARBLED")
        return problems

    # WARNING: Column jumbling (lines too short = bad layout detection)
    lines = [l for l in text.split('\n') if l.strip()]
    if lines:
        avg_line_length = sum(len(l) for l in lines) / len(lines)
        if avg_line_length < 40:
            problems.append("COLUMNJUMBLE")

    # WARNING: No paragraph structure (everything ran together)
    paragraphs = [p for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) < 3 and len(words) > 500:
        problems.append("NOPARAGRAPHS")

    # WARNING: Repeated text blocks (extraction loop bug)
    if has_repeated_blocks(text):
        problems.append("REPEATEDTEXT")

    # WARNING: References section missing (possible truncation)
    if len(words) > 2000 and not any(
        marker in text.lower()
        for marker in ['references', 'bibliography', 'works cited']
    ):
        problems.append("NOREFSSECTION")

    return problems


def has_repeated_blocks(text: str, min_block_size: int = 100) -> bool:
    """
    Detects if the same text block appears multiple times.
    Indicates extraction bug (common with headers/footers).
    """
    # Split into chunks and look for duplicates
    chunks = [text[i:i+min_block_size] for i in range(0, len(text)-min_block_size, min_block_size)]
    seen = set()
    for chunk in chunks:
        normalized = ' '.join(chunk.split())  # Normalize whitespace
        if normalized in seen:
            return True
        seen.add(normalized)
    return False
```

**Problem codes:**

| Code | Type | Meaning |
|------|------|---------|
| UNUSABLE | Blocking | Cannot proceed — needs manual preprocessing |
| TOOSHORT | Blocking | Less than 100 words extracted |
| GARBLED | Blocking | >5% garbage/encoding characters |
| COLUMNJUMBLE | Warning | Lines avg <40 chars — likely column detection issue |
| NOPARAGRAPHS | Warning | No paragraph breaks detected |
| REPEATEDTEXT | Warning | Same text block appears multiple times |
| NOREFSSECTION | Warning | Long article missing references (possible truncation) |

### 5.3 Extraction Method in Database

Store extraction method (not confidence) for debugging:

```sql
-- Add to articles table (migration)
ALTER TABLE articles ADD COLUMN extraction_method TEXT;
ALTER TABLE articles ADD COLUMN extraction_problems TEXT;  -- JSON array of problem codes

-- Query articles with extraction issues for review
SELECT id, source_title, extraction_method, extraction_problems
FROM articles
WHERE json_array_length(extraction_problems) > 0
ORDER BY processed_at DESC;
```

### 5.4 Manual Preprocessing Workflow

When extraction fails:
1. `get_chunk()` returns `{"error": "PDFEXTRACT", "pdf_path": "..."}`
2. Claude calls `skip_article()` with PDFEXTRACT flag
3. Admin dashboard shows article in preprocessing queue
4. Human converts PDF to markdown, saves to `/external/pda/preprocessed/{article_id}.md`
5. Human marks article as ready
6. Next `get_next_article()` picks it up

---

## Part 6: Glossary Matching

### 6.1 Variant Detection

Simple string matching misses inflections. The glossary index includes:
- Hyphenation variants: "demand avoidance" ↔ "demand-avoidance"
- Morphological variants: "avoidance" ↔ "avoidant"
- Plural/singular
- Abbreviations: "PDA" extracted from "Pathological Demand Avoidance (PDA)"

### 6.2 Per-Chunk Extraction

```python
def get_chunk(article_id, chunk_number):
    chunk_text = get_chunk_text(article_id, chunk_number)

    # Only terms relevant to THIS chunk
    glossary_terms = find_glossary_terms_in_text(chunk_text, glossary_index)

    return {
        "text": chunk_text,
        "glossary_terms": glossary_terms,  # {"demand avoidance": "évitement des demandes", ...}
        ...
    }
```

### 6.3 Verification at Save

```python
def verify_glossary_terms(source_text: str, translation: str) -> list[str]:
    """
    Returns list of missing terms for TERMMIS flag.
    """
    expected = find_glossary_terms_in_text(source_text, glossary_index)
    missing = []

    for en_term, fr_term in expected.items():
        fr_alternatives = [alt.strip().lower() for alt in fr_term.split('/')]
        if not any(alt in translation.lower() for alt in fr_alternatives):
            missing.append(f"{en_term} → {fr_term}")

    return missing
```

---

## Part 7: Sentence Tokenization

Using spaCy instead of regex for accurate sentence counts.

```python
import spacy

nlp_en = spacy.load("en_core_web_sm")
nlp_fr = spacy.load("fr_core_news_sm")

def compare_sentence_counts(source: str, target: str) -> dict:
    source_count = len(list(nlp_en(source).sents))
    target_count = len(list(nlp_fr(target).sents))

    ratio = target_count / max(source_count, 1)
    acceptable = 0.85 <= ratio <= 1.15

    return {
        "source_count": source_count,
        "target_count": target_count,
        "ratio": round(ratio, 2),
        "flag": None if acceptable else "SENTMIS"
    }
```

Why spaCy over regex:
- `Dr. Smith found...` → 1 sentence (regex: 2)
- `p < 0.05 was significant.` → 1 sentence (regex: 2)
- `The U.S.A. is...` → 1 sentence (regex: 4)

---

## Part 8: Human Review Interval

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    HUMAN REVIEW INTERVAL                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PURPOSE:                                                                    │
│    - Catch quality drift before it affects many articles                    │
│    - Force regular human attention                                          │
│    - Natural breakpoints for review                                         │
│                                                                              │
│  CONFIGURATION:                                                              │
│    Default: 5 articles                                                       │
│    Range: 1-20                                                               │
│                                                                              │
│  HOW IT WORKS:                                                               │
│    After each save_article(): session_count += 1                            │
│    When session_count >= human_review_interval:                             │
│      get_next_article() returns SESSION_PAUSE                               │
│      Human reviews in admin dashboard                                       │
│      Human calls reset_session_counter() or starts new session             │
│                                                                              │
│  RECOMMENDED RAMP-UP:                                                        │
│    First 5 articles: interval = 1 (approve each one)                       │
│    Next 10 articles: interval = 3                                           │
│    Steady state: interval = 5-10                                            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 9: Admin Interface

The admin interface lives in the existing Astro site under `/admin/`. Uses the same SQLite database via `src/lib/db.ts`.

### Implementation

```
site/src/pages/admin/
├── index.astro           # Dashboard
├── articles/
│   ├── index.astro       # Article list with filters
│   └── [id].astro        # Side-by-side review
├── preprocessing.astro   # PDF queue
└── settings.astro        # human_review_interval
```

**Dashboard queries (run at build time + client refresh):**
```typescript
// src/lib/db.ts additions
export function getProgress() {
  return db.prepare(`
    SELECT processing_status, COUNT(*) as count
    FROM articles GROUP BY processing_status
  `).all();
}

export function getSessionState() {
  return db.prepare(`
    SELECT * FROM session_state WHERE id = 1
  `).get();
}

export function getFlaggedArticles() {
  return db.prepare(`
    SELECT id, source_title, processing_flags, processed_at
    FROM articles
    WHERE processing_status = 'translated'
      AND json_array_length(processing_flags) > 0
    ORDER BY processed_at DESC
    LIMIT 20
  `).all();
}
```

**Client-side refresh:** Add API route at `site/src/pages/api/progress.ts` that returns live stats. Dashboard polls every 30 seconds during active translation sessions.

### Pages

| Page | Purpose |
|------|---------|
| `/admin` | Dashboard: progress bar, session status, recent completions, needs attention |
| `/admin/articles` | Article list with filters (status, flags, category, method) |
| `/admin/articles/[id]` | Side-by-side view, quality metrics, mark reviewed |
| `/admin/preprocessing` | PDF preprocessing queue |
| `/admin/settings` | Set human_review_interval |

### Review Workflow

1. Claude processes N articles until human_review_interval
2. Claude reports: "Processed 5 articles. Pausing for review."
3. Human opens `/admin`
4. Dashboard shows which articles have flags
5. Human reviews flagged articles, marks as reviewed
6. Human clicks "Continue" or starts new session

---

## Part 10: Single Source of Truth for Flags

All flags defined in `data/taxonomy.yaml`:

```yaml
processing_flags:
  content:
    TBL: {description: "Article contains tables"}
    FIG: {description: "Article contains figures"}
    META: {description: "Significant metadata (appendices)"}
    LONG: {description: "Exceeds 5000 words"}
    TERM: {description: "Used term not in glossary"}
    AMBIG: {description: "Source text ambiguous"}

  access:
    PAYWALL: {description: "Full text behind paywall"}
    404: {description: "Source URL returned 404"}
    NOURL: {description: "No source URL available"}
    PDFEXTRACT: {description: "PDF extraction failed"}

  classification:
    METHUNC: {description: "Uncertain about method"}
    VOICEUNC: {description: "Uncertain about voice"}
    PEERUNC: {description: "Uncertain about peer-reviewed"}

  relevance:
    TANGENT: {description: "Tangentially related to PDA"}
    QUALITY: {description: "Source quality concerns"}
    SKIP: {description: "Should be skipped"}

  automated:
    blocking:
      SENTMIS: {description: "Sentence count mismatch >15%", action: "Must fix"}
      WORDMIS: {description: "Word ratio outside 0.9-1.5", action: "Must fix"}
    warning:
      WORDDRIFT: {description: "Glossary term recall < 0.7", action: "Human review — possible editorial drift"}
      TERMMIS: {description: "Glossary term missing", action: "Human review"}
      STATMIS: {description: "Statistics may be modified", action: "Human review"}
```

---

## Part 11: What Automation Can and Cannot Catch

**CAN catch (high confidence):**
- Gross omissions (sentence count)
- Major content changes (word ratio)
- Missing glossary terms
- Changed statistics

**CANNOT catch:**
- Subtle meaning shifts
- Register drift
- Awkward but accurate phrasing
- Wrong classification judgment

**Honest expectation:** 20-30% of articles will need human review for reasons beyond automation's reach. This is the system working correctly — flagging uncertainty rather than hiding it.

---

## Part 12: Implementation Plan

**Each phase is a gate.** A phase is complete when:
1. All code for that phase is implemented
2. All tests for that phase pass (`test_phase{N}.py`)
3. All tests from previous phases still pass (regression check)

Run `pytest tests/ -v` after completing each phase.

### Phase 1: MCP Server Core

**Implement:**
- `server.py` — MCP server entry point with FastMCP
- `database.py` — SQLite operations, session state, validation tokens
- `taxonomy.py` — Load and validate against taxonomy.yaml
- Tools: `get_next_article()`, `get_progress()`, `skip_article()`, `set_human_review_interval()`, `reset_session_counter()`

**Test (`tests/test_phase1.py`):**
- `TestGetNextArticle`: returns pending article, marks in_progress, prioritizes crash recovery, SESSION_PAUSE when limit reached, COMPLETE when all done
- `TestGetProgress`: accurate counts, includes session state
- `TestSkipArticle`: marks skipped with flag, rejects invalid flags, doesn't increment counter
- `TestSessionManagement`: interval validation (1-20), reset counter
- `TestTaxonomy`: loads from YAML, validates methods/voices/flags, identifies blocking flags
- `TestDatabase`: token lifecycle (create/validate/use), wrong article rejection, cleanup

### Phase 2: Chunked Delivery

**Implement:**
- `pdf_extraction.py` — PyMuPDF → pdfminer.six → pdfplumber fallback chain
- `glossary.py` — Load glossary, match terms, verify translations
- `tools.py` additions: `get_chunk()`, chunk splitting, cache management

**Test (`tests/test_phase2.py`):**
- `TestGetChunk`: article not found, paywalled returns error, no cache returns NOT_CACHED, response schema, chunk caching, complete response, extraction metadata
- `TestPdfExtraction`: real PDF extraction, problem detection (TOOSHORT, NOPARAGRAPHS), .txt precedence, cache path lookup
- `TestGlossary`: loads from YAML, has version, exact match, case-insensitive, word boundaries, French translations, verify missing terms, accepts fr_alt variants
- `TestChunking`: splits on paragraphs, respects target count, handles long paragraphs, empty text
- `TestChunkCache`: cleared on skip, article isolation, stores extraction metadata

### Phase 3: Quality Checks

**Implement:**
- `quality_checks.py` — spaCy sentence counting, word ratios, Jaccard similarity, statistics check
- Integrate checks into save workflow

**Test (`tests/test_phase3.py`):**
- `TestSentenceCounting`: accurate count for EN and FR, handles abbreviations (Dr., U.S.A.), ratio calculation, SENTMIS flag when >15% variance
- `TestWordRatio`: calculates correctly, WORDMIS flag outside 0.9-1.5 range
- `TestGlossaryRecall`: content word extraction, recall calculation, WORDDRIFT flag when <0.7
- `TestGlossaryVerification`: TERMMIS for missing terms, accepts fr_alt variants, handles quotes edge case
- `TestStatisticsCheck`: detects numbers in source, STATMIS when numbers differ
- `TestBlockingVsWarning`: correctly classifies blocking (SENTMIS, WORDMIS) vs warning flags

### Phase 4: Validation + Save

**Implement:**
- `tools.py` additions: `validate_classification()`, `save_article()`
- Transaction handling, token validation, review interval enforcement

**Test (`tests/test_phase4.py`):**
- `TestValidateClassification`: validates method/voice/categories/keywords, rejects invalid values, returns token, suggests corrections
- `TestSaveArticle`: requires valid token, rejects expired token, rejects used token, runs quality checks, blocks on SENTMIS/WORDMIS, accepts with warnings, increments session counter, writes all tables in transaction
- `TestWorkflowEnforcement`: save without validate fails, token expires after 30 min, SESSION_PAUSE after interval reached
- `TestTransactionRollback`: partial failure rolls back all changes
- `TestCategoryStorage`: primary vs secondary, no duplicates, 1-3 categories

### Phase 5: Admin Interface

**Implement:**
- Astro pages: `/admin/`, `/admin/articles/`, `/admin/articles/[id]`, `/admin/preprocessing`, `/admin/settings`
- API routes for live stats refresh
- Local-only access (ENABLE_ADMIN env var)

**Test (`tests/test_phase5.py`):**
- `TestAdminQueries`: getProgress, getSessionState, getFlaggedArticles return correct data
- `TestAdminAccess`: routes redirect when ENABLE_ADMIN not set (if testable)
- Integration testing via browser/Playwright is optional; focus on data layer

### Phase 6: Integration Testing

**Implement:**
- End-to-end workflow tests with real or realistic data
- Crash recovery scenarios
- Full translation cycle

**Test (`tests/test_phase6.py`):**
- `TestEndToEnd`: complete article workflow (get_next → chunks → validate → save)
- `TestCrashRecovery`: in_progress article picked up on restart, chunk cache regenerated
- `TestSessionPause`: pause triggers at interval, continues after reset
- `TestPaywalledFlow`: title/summary only, no chunks, save with null full_text
- `TestSkipFlow`: extraction failure → skip → next article
- `TestMultipleArticles`: process 3 articles sequentially, verify state consistency

---

## Summary

This plan creates a translation machine that:

1. **Prevents quantity problems** via automated checks at save-time
   - spaCy sentence count catches omissions (SENTMIS)
   - Word ratio catches compression (WORDMIS)
   - Glossary term recall catches "creative" rephrasing (WORDDRIFT)

2. **Prevents quality problems** via chunked delivery + repeated instruction
   - Claude only sees 3-5 paragraphs at a time
   - Each chunk includes anti-editorial instruction to prevent context decay
   - Cannot "skim and summarize"
   - Must stay close to source text

3. **Enforces consistency** via MCP server
   - taxonomy.yaml is single source of truth
   - Validation tokens enforce workflow order
   - Flags stored on articles table only (no duplication)

4. **Maintains human oversight** via review interval
   - Configurable pause every N articles
   - Dashboard shows what needs attention (flagged articles, extraction problems)
   - Catches drift before it compounds

5. **Handles failures gracefully**
   - Three-stage PDF extraction: PyMuPDF → pdfminer.six → pdfplumber
   - Specific problem detection (COLUMNJUMBLE, GARBLED, etc.) — no manufactured confidence scores
   - PDFEXTRACT flag for manual preprocessing
   - Session recovery from crashes

6. **Handles edge cases explicitly**
   - Paywalled articles: title + summary translated, chunk loop skipped
   - Title/summary always translated BEFORE chunk loop
   - Validation tokens expire after 30 minutes, single-use

The key insight: **chunked delivery is the quality control mechanism**, not logging or paragraph-by-paragraph enforcement. By controlling what Claude sees, we prevent the failure modes that matter.

---

## Part 13: Multi-Language Support

The Translation Machine is designed to support additional target languages (Spanish, German, etc.) with minimal changes.

### What's Language-Agnostic (No Changes Needed)

- MCP server architecture (chunking, validation tokens, workflow enforcement)
- Database schema (`translations` table already has `language` column)
- PDF extraction pipeline
- Human review interval mechanism
- Admin interface structure
- Validation token system

### What Requires Language-Specific Additions

| Component | Current State | For New Language |
|-----------|---------------|------------------|
| Glossary files | `data/glossary_fr.yaml` | Add `glossary_es.yaml`, `glossary_de.yaml` |
| spaCy models | `fr_core_news_sm` | Add `es_core_news_sm`, `de_core_news_sm` |
| Taxonomy labels | FR labels in `taxonomy.yaml` | Add ES/DE label columns |
| Astro i18n | FR/EN in `translations.ts` | Add ES/DE UI strings |
| Quality check ratios | FR word ratio 1.1-1.2x | Calibrate per language |

### Glossary File Structure

Separate files per language (easier to manage, cleaner diffs, different reviewers):

```
data/
├── glossary_fr.yaml    # EN→FR (~200 terms)
├── glossary_es.yaml    # EN→ES (future)
├── glossary_de.yaml    # EN→DE (future)
└── glossary_schema.yaml # Shared structure definition
```

Each glossary file:
```yaml
version: "2025-01-11"
language: "fr"  # or "es", "de"
terms:
  demand_avoidance:
    en: "demand avoidance"
    target: "évitement des demandes"
    target_alt: ["évitement de la demande"]
    abbreviation: "DA"
  # ...
```

### Tool Changes for Multi-Language

Add `target_language` parameter to:

```python
def get_next_article(target_language: str = "fr") -> dict:
    """
    Returns next article needing translation into target_language.
    Filters by: translations table WHERE language = target_language AND status != 'translated'
    """

def get_chunk(article_id: str, chunk_number: int, target_language: str = "fr") -> dict:
    """
    Returns glossary terms from glossary_{target_language}.yaml
    """

def save_article(..., target_language: str = "fr") -> dict:
    """
    Saves to translations table with language = target_language
    Uses appropriate spaCy model for quality checks
    """
```

### spaCy Model Loading

```python
# Load models for all configured languages at startup
LANGUAGE_MODELS = {
    "en": spacy.load("en_core_web_sm"),
    "fr": spacy.load("fr_core_news_sm"),
    # Add as needed:
    # "es": spacy.load("es_core_news_sm"),
    # "de": spacy.load("de_core_news_sm"),
}

def get_target_nlp(language: str):
    if language not in LANGUAGE_MODELS:
        raise ValueError(f"No spaCy model configured for language: {language}")
    return LANGUAGE_MODELS[language]
```

### Word Ratio Calibration

Different language pairs have different expansion ratios:

| Source → Target | Typical Ratio | Acceptable Range |
|-----------------|---------------|------------------|
| EN → FR | 1.1-1.2x | 0.9-1.5 |
| EN → ES | 1.2-1.3x | 0.95-1.6 |
| EN → DE | 1.1-1.2x | 0.9-1.5 |

Store in config, not hardcoded:
```python
WORD_RATIO_RANGES = {
    "fr": (0.9, 1.5),
    "es": (0.95, 1.6),
    "de": (0.9, 1.5),
}
```

### The Real Work: Glossaries

The ~200 clinical terms need expert translation for each language. This is domain expertise, not engineering:

1. **Find a domain expert** — Clinician or academic translator familiar with autism/PDA literature in target language
2. **Translate the glossary** — Not just words, but the right clinical register
3. **Find reference material** — Equivalent of Philippe & Contejean for each language (if it exists)
4. **Validate with native speakers** — Clinicians in target country

The Translation Machine handles the workflow; the glossary handles the quality.

---

## Part 14: Testing Strategy

### Philosophy

**Tests are phase gates.** A phase is not complete until:
1. All tests for that phase pass
2. All tests from previous phases still pass (regression check)

This prevents "it works in isolation but breaks integration" problems. Run the full test suite after every phase.

### Test Structure

```
tests/
├── __init__.py
├── conftest.py           # Shared fixtures: test_db, sample_articles, cached_pdf, etc.
├── test_phase1.py        # Core tools, session management, taxonomy validation
├── test_phase2.py        # Chunked delivery, PDF extraction, glossary matching
├── test_phase3.py        # Quality checks (spaCy sentence counting, ratios, Jaccard)
├── test_phase4.py        # Validation + save workflow, transactions, token lifecycle
├── test_phase5.py        # Admin interface data layer (optional: browser tests)
└── test_phase6.py        # Integration: end-to-end workflows, crash recovery
```

### Running Tests

```bash
# Run all tests (do this after every phase)
/opt/homebrew/bin/python3.11 -m pytest tests/ -v

# Run specific phase tests
/opt/homebrew/bin/python3.11 -m pytest tests/test_phase3.py -v

# Run with coverage report
/opt/homebrew/bin/python3.11 -m pytest tests/ --cov=mcp_server --cov-report=term-missing

# Run tests matching a pattern
/opt/homebrew/bin/python3.11 -m pytest tests/ -k "sentence" -v
```

### Fixtures (conftest.py)

The shared fixtures provide:

| Fixture | Purpose |
|---------|---------|
| `test_db` | Fresh SQLite database with schema, isolated per test |
| `sample_articles` | Inserts 5 test articles (pending, translated, skipped, paywalled) |
| `db_with_articles` | Database with articles + patches module to use it |
| `cached_pdf` | Real or mock PDF in cache directory for extraction tests |
| `sample_text` | Multi-paragraph English text for chunking tests |
| `clear_chunk_cache` | Clears chunk cache before/after test |

### Test Dependencies

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio",
    "pytest-cov",
]
```

### Writing Good Tests

1. **One assertion per concept** — Test one behavior, even if it needs multiple asserts
2. **Use descriptive names** — `test_returns_session_pause_when_limit_reached` not `test_limit`
3. **Test edge cases** — Empty strings, None values, boundary conditions
4. **Test error paths** — Invalid inputs should return structured errors, not exceptions
5. **Don't test implementation** — Test behavior, not internal state

### Continuous Integration (Future)

When ready, add GitHub Actions:
```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: python -m spacy download en_core_web_sm
      - run: python -m spacy download fr_core_news_sm
      - run: pytest tests/ -v --cov=mcp_server
```

---

## Changelog

| Date | Change |
|------|--------|
| 2025-01-10 | Initial plan created |
| 2025-01-11 | Added D7-D12: Claude behavior, validation tokens, skip semantics, title/summary timing, paywalled flow, glossary matching |
| 2025-01-11 | Added pdfminer.six as third extractor with confidence scoring |
| 2025-01-11 | Added WORDDRIFT Jaccard check for editorial drift detection |
| 2025-01-11 | Added anti-editorial instruction repeated per chunk |
| 2025-01-11 | Updated tool signatures with validation_token, flags, flag_details |
| 2025-01-11 | Updated workflow diagram to show title/summary timing |
| 2025-01-11 | Updated operational procedures with detailed error handling |
| 2025-01-11 | **Review changes:** Added D13 (cache/ and intake/ folder structure), D14 (source field derivation cascade), D15 (classification signals are docs only) |
| 2025-01-11 | **Review changes:** Eliminated translator_notes duplication — flags stored on articles table only |
| 2025-01-11 | **Review changes:** Replaced confidence scoring with specific problem detection (COLUMNJUMBLE, GARBLED, etc.) |
| 2025-01-11 | **Review changes:** Moved Part 11 operational procedures into tool docstrings |
| 2025-01-11 | **Review changes:** Enhanced tool docstrings with workflow reminders, next_step hints, and error handling instructions |
| 2025-01-11 | **Ambiguity resolutions:** Added D16 (article_id is TEXT string), D17 (validation token SQLite storage), D18 (spaCy loads at startup), D19 (cache path convention with .txt precedence), D20 (categories parameter format) |
| 2025-01-11 | **Ambiguity resolutions:** Added SRCUNK flag to taxonomy.yaml for source derivation failures |
| 2025-01-11 | **Plan review edits:** Expanded D7 to complete workflow behavior (single source of truth for Claude behavior) |
| 2025-01-11 | **Plan review edits:** Added D21 (ingest_article tool for intake/ folder), D22 (simplified flags parameter format), D23 (session state timezone as localtime) |
| 2025-01-11 | **Plan review edits:** Made D17 token contents explicit with JSON schema |
| 2025-01-11 | **Plan review edits:** Removed Part 11 (consolidated into D7 and tool docstrings), renumbered Part 12 |
| 2025-01-11 | **Plan review edits:** Pinned spaCy version (>=3.7,<4.0) and model versions in dependencies |
| 2025-01-11 | **Plan review edits:** Updated save_article() signature to use simplified flags: list[dict] format |
| 2025-01-11 | **Plan review edits:** Added Tool 9 (ingest_article) to Part 4.1 tools list |
| 2025-01-11 | **Final review:** Confirmed Jaccard check stays at 0.6 threshold with ≥3 term minimum; glossary (~200 terms) provides sufficient coverage. No rollback mechanism needed — re-translation overwrites. |
| 2025-01-11 | **Consistency fix:** Aligned validate_classification() signature with D20 — uses `primary_category: str` + `secondary_categories: list[str]` instead of `categories: list[dict]` |
| 2025-01-11 | **Ambiguity resolutions:** Added D24 (open_access field population), D25 (admin local-only access), D26 (chunk cache lifecycle with 1-hour TTL) |
| 2025-01-11 | **Ambiguity resolutions:** Updated D7 error handling — one fix attempt for BLOCKING flags, then skip (no attempt counting) |
| 2025-01-11 | **Ambiguity resolutions:** Updated D12 with fr_alt variant support and quote edge case documentation |
| 2025-01-11 | **Ambiguity resolutions:** Clarified get_chunk() response schema with error/success distinction and extraction_warnings field |
| 2025-01-11 | **Ambiguity resolutions:** Fixed spaCy installation instructions — use standard download commands, pin library not models |
| 2025-01-11 | **Pre-build review:** Extended validation token expiry from 10 to 30 minutes (accommodates long articles) |
| 2025-01-11 | **Pre-build review:** Added D27 (glossary versioning) — tracks which glossary version was used per article |
| 2025-01-11 | **Pre-build review:** Added crash recovery clarification to D1 — partial translations not persisted, restart from chunk 1 |
| 2025-01-11 | Added Part 13 (Multi-Language Support) — glossary file structure, tool parameters, spaCy model loading, word ratio calibration |
| 2026-01-11 | **Testing requirements:** Expanded Part 12 phases with explicit test requirements per phase; added Part 14 (Testing Strategy) with test structure, fixtures, commands, and CI config |
| 2026-01-11 | **Metric change:** Replaced Jaccard similarity with Recall for WORDDRIFT check. Jaccard (intersection/union) penalized translations for having additional content words — normal behavior. Recall (intersection/expected) correctly measures "what % of expected glossary terms appeared." Empirically validated: good translations 0.77-0.88 recall, drifted 0.23. Threshold changed from 0.6 to 0.7. |
