# Translation Process — Deterministic Pipeline

> **Status:** COMPLETE — all steps defined, ready for execution
>
> **Goal:** A process Claude can run overnight without human intervention.
> Every decision point has a rule. No judgment calls at runtime.

---

## Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  ┌────────┐   ┌────────┐   ┌────────┐   ┌──────────┐   ┌────────┐      │
│  │ SELECT │──▶│ ACCESS │──▶│  READ  │──▶│ CLASSIFY │──▶│TRANSLATE│     │
│  └────────┘   └────────┘   └────────┘   └──────────┘   └─────────┘     │
│       │            │            │             │              │          │
│       ▼            ▼            ▼             ▼              ▼          │
│   Pick next    Fetch URL    Extract       Assign         EN → FR       │
│   article      from web     content       metadata                     │
│                                                                         │
│                                     ┌────────┐   ┌────────┐            │
│                                ────▶│  SAVE  │──▶│  LOG   │            │
│                                     └────────┘   └────────┘            │
│                                          │            │                 │
│                                          ▼            ▼                 │
│                                     Write to DB   Record what          │
│                                                   happened             │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Definition: "Finished" Article

An article is **finished** when ALL of the following are populated:

### `articles` table
| Field | Required | Notes |
|-------|----------|-------|
| `method` | YES | One of: empirical, synthesis, theoretical, lived_experience |
| `voice` | YES | One of: academic, practitioner, organization, individual |
| `peer_reviewed` | YES | Boolean (0 or 1) |
| `open_access` | YES | Boolean (0 or 1) |
| `source` | YES | Where this came from (see examples below) |
| `source_url` | YES | URL to the document |
| `authors` | YES | May already exist from import |
| `year` | YES | May already exist from import |
| `doi` | IF available | NULL ok if none exists |

#### `source` Field Examples

| Article Type | `source` value |
|--------------|----------------|
| Journal article | "Journal of Autism and Developmental Disorders" |
| PhD thesis | "King's College London" |
| Master's thesis | "University of Birmingham" |
| Charity report | "PDA Society" |
| Charity-hosted research | "Cerebra" |
| Conference paper | "National Autistic Society Conference 2019" |
| Book chapter | "Autism and Asperger Syndrome (Cambridge University Press)" |
| Website/blog | "PDA Society website" |
| Government report | "Haute Autorité de Santé" |

### `translations` table (for target_language = 'fr')
| Field | Required | Notes |
|-------|----------|-------|
| `translated_title` | YES | French title |
| `translated_summary` | YES | French summary |
| `translated_full_text` | IF open_access | Only translate full text if freely available |
| `status` | YES | Set to 'translated' when done |

### `article_categories` junction table
| Field | Required | Notes |
|-------|----------|-------|
| At least 1 category | YES | From taxonomy.yaml |
| `is_primary` | YES | Exactly one category marked primary |

### `article_keywords` junction table
| Field | Required | Notes |
|-------|----------|-------|
| At least 1 keyword | YES | For searchability |

---

## Step 1: SELECT

**Purpose:** Pick the next article to process.

**Input:** `articles` table

**Output:** One article row, or SKIP, or HALT if none left

### Decision Points

| Question | Options | Decision |
|----------|---------|----------|
| Processing order? | by year, by ID, random | **By ID ascending** (original PDA Society sequence) |
| What needs processing? | no translation? no classification? both? | **Not "finished"** per definition above |
| Skip list? | exclude off-topic articles? | **See skip/flag rules below** |

### Skip/Flag Rules

| Condition | Action |
|-----------|--------|
| PDA/demand avoidance not mentioned in title OR summary | **SKIP** — not relevant |
| PDA mentioned but not the focus of the article | **FLAG** — human decides if worth including |
| PDA is central topic but questionable quality | **FLAG** — process anyway, human reviews later |
| PDA is central topic | **PROCESS** |

### Rules

```sql
-- Get next article to process (not finished, not skipped)
SELECT a.id, a.source_title, a.source_url, a.summary_original
FROM articles a
WHERE
  -- Not fully classified
  (a.method IS NULL OR a.voice IS NULL)
  -- OR no French translation
  OR NOT EXISTS (
    SELECT 1 FROM translations t
    WHERE t.article_id = a.id
    AND t.target_language = 'fr'
    AND t.status = 'translated'
  )
  -- OR no categories assigned
  OR NOT EXISTS (
    SELECT 1 FROM article_categories ac
    WHERE ac.article_id = a.id
  )
ORDER BY a.id ASC
LIMIT 1;
```

### Before Processing: Check Relevance

```
IF "PDA" not in title AND "demand avoidance" not in title
   AND "PDA" not in summary AND "demand avoidance" not in summary:
   → SKIP (log reason: "not PDA-related")

IF PDA mentioned but clearly not the focus:
   → FLAG (log: "PDA tangential — needs human review")
   → Still process, but mark for review
```

---

## Step 2: ACCESS

**Purpose:** Get the source content.

**Input:** `source_url` from selected article

**Output:** Document content, or FLAG for human intervention

### Source Types (observed in DB)

| Domain | Type | Expected Access |
|--------|------|-----------------|
| pdasociety.org.uk/wp-content/uploads/*.pdf | Hosted PDF | Direct download |
| sciencedirect.com | Journal | Often paywalled |
| tandfonline.com | Journal | Often paywalled |
| Other | Varies | Check at runtime |

### Decision Points

| Question | Options | Decision |
|----------|---------|----------|
| URL returns 200 + readable? | continue | **PROCESS** |
| URL returns 404? | skip? retry? flag? | **FLAG** for human |
| URL is paywalled? | summary only? try harder? | **TRY HARDER** (see below) |
| URL requires login? | same as paywall | **TRY HARDER** |
| No URL at all? | flag | **FLAG** for human |
| PDF vs HTML? | handle differently? | **Both OK** — extract text either way |

### Rules

```
1. IF source_url IS NULL:
   → FLAG (log: "no source URL — need human to provide")
   → HALT this article, continue to next

2. IF source_url points to pdasociety.org.uk PDF:
   → Fetch directly (should work)
   → IF fails: FLAG

3. IF source_url points to journal site:
   → Try to fetch
   → IF paywalled or login required:
      a. Search for open access version:
         - Try: article title + "PDF"
         - Try: article title + "ResearchGate"
         - Try: article title + "PubMed Central"
         - Try: DOI + "sci-hub" (if DOI known)
      b. IF found:
         → Use that URL instead
         → Set open_access = 1 (we found an accessible copy)
      c. IF not found: FLAG (log: "paywalled, couldn't find open version")
         → Set open_access = 0
         → Still process with summary_original only
         → Mark in translator_notes: "summary only — full text paywalled"

4. IF URL returns 404 or other error:
   → FLAG (log: "URL broken: [error]")
   → HALT this article, continue to next
```

### Fallback Behavior

When full text is inaccessible but we have `summary_original`:
- Translate the summary we have
- Set `open_access = 0` if not already
- Add translator_note: "Translated from summary only; full text not accessible"
- Article is still "finished" for translation purposes

---

## Step 3: READ

**Purpose:** Extract the relevant content from the document.

**Input:** Raw document (HTML/PDF)

**Output:** Structured content ready for translation

### What We Extract

| Element | Source | Action |
|---------|--------|--------|
| Title | PDF/HTML | Verify against `source_title` in DB; FLAG if mismatch |
| Authors | PDF/HTML | Verify against `authors` in DB; FLAG if mismatch |
| Year | PDF/HTML | Verify against `year` in DB; FLAG if mismatch |
| Source | PDF header/footer, URL domain | Extract publication/institution name; store in `source` |
| DOI | PDF/HTML | Extract if present; store in `doi` |
| Abstract | PDF/HTML | Use for `summary_original` if DB is empty |
| Full text | PDF/HTML | Extract for translation if open_access |

#### Extracting `source`

```
1. IF PDF has journal name in header/footer:
   → Use journal name (e.g., "Autism: The International Journal of Research and Practice")

2. ELSE IF PDF is thesis:
   → Use university name (e.g., "King's College London")

3. ELSE IF URL domain is pdasociety.org.uk:
   → Check if it's hosted research or their own content
   → Hosted research: extract original source from PDF
   → Their own content: use "PDA Society"

4. ELSE IF URL domain is known charity:
   → Use charity name (e.g., "Cerebra", "National Autistic Society")

5. ELSE:
   → Extract from document metadata or header
   → FLAG with code META if unclear
```

### Decision Points

| Question | Options | Decision |
|----------|---------|----------|
| What do we extract? | abstract only? full text? | **Full text** if open access |
| Content in DB already? | trust it? re-extract? | **Verify** and FLAG mismatches |
| No abstract in source? | skip? write one? | **Write summary** in EN, then translate |

### Content Structure Detection

```
FOR each document:
  1. Identify section headers (Introduction, Methods, Results, Discussion, etc.)
  2. Identify tables → FLAG with code TBL
  3. Identify figures → FLAG with code FIG
  4. Identify appendices → note for separate handling
  5. Identify references section → do NOT translate
```

### Rules

```
1. Verify metadata:
   - Compare extracted title/authors/year with DB
   - IF mismatch: FLAG with code META, log discrepancy, use extracted version

2. Handle missing abstract:
   - IF no abstract in source AND summary_original is empty:
     → Write a 150-200 word summary in English
     → Store in summary_original
     → Then translate to French

3. Detect complex content:
   - IF document contains tables: FLAG with code TBL
   - IF document contains figures: FLAG with code FIG
   - Continue processing, but mark for human review

4. Structure for translation:
   - Convert to markdown with section headers
   - Preserve paragraph breaks
   - Note table/figure locations with placeholders
```

---

## Step 4: CLASSIFY

**Purpose:** Assign method, voice, peer_reviewed, categories, keywords.

**Input:** Article content + taxonomy.yaml

**Output:** Classification metadata

### Decision Points

| Question | Options | Decision |
|----------|---------|----------|
| Method unclear? | best guess? flag for review? skip? | **Best guess + FLAG** with code METHUNC |
| Voice unclear? | best guess? flag for review? skip? | **Best guess + FLAG** with code VOICEUNC |
| Peer-reviewed unclear? | assume no? check DOI? | **Check signals below + FLAG** with code PEERUNC if uncertain |
| How many categories? | minimum? maximum? | **Minimum 1, maximum 3** — pick what truly fits |
| Keyword source? | extract from text? controlled vocab? | **Both** — extract key terms, normalize to controlled vocab |

### Classification: Method

Consult `taxonomy.yaml` for signals. Apply this decision tree:

```
FIRST: Is this a first-person narrative from person with PDA or family member?
   → YES: method = lived_experience, voice = individual (STOP — no further checks)
   → NO: Continue...

1. Does it report original data collection (N participants, surveys, interviews)?
   → empirical

2. Does it analyze/synthesize existing literature (review, meta-analysis)?
   → synthesis

3. Does it present conceptual argument, framework, or critique (no new data)?
   → theoretical

IF UNCLEAR after applying signals:
   → Use best judgment
   → FLAG with code METHUNC
   → Log: "[METHUNC] Could not determine method | signals present: X, Y"
```

**Note:** `lived_experience` trumps all other methods. If someone with PDA writes about their experience AND includes data, it's still `lived_experience` — the first-person perspective is defining.

### Classification: Voice

Consult `taxonomy.yaml` for signals. Apply this decision tree:

```
1. Is author university-affiliated, presenting research findings?
   → academic

2. Is author a clinician/educator offering practice guidance?
   → practitioner
   NOTE: If academic doing clinical guidance, use practitioner

3. Is it published by a charity/society (no individual author attribution)?
   → organization

4. Is it a personal narrative from individual/family?
   → individual

IF UNCLEAR after applying signals:
   → Use best judgment
   → FLAG with code VOICEUNC
   → Log: "[VOICEUNC] Could not determine voice | signals present: X, Y"
```

### Classification: Peer-Reviewed

```
PEER-REVIEWED = TRUE if ANY of:
  - DOI present and resolves to journal article
  - Journal name in header/footer of PDF
  - Volume/issue numbers present
  - "Published in [Journal Name]" statement
  - Known peer-reviewed journal (JADD, Autism, etc.)

PEER-REVIEWED = FALSE if:
  - Published by charity/society directly
  - Blog post or website content
  - Thesis or dissertation (institutional review, not peer review)
  - Conference presentation (unless explicitly stated as peer-reviewed proceedings)
  - Book chapter (editorial review, not peer review)

IF UNCLEAR:
  → Default to FALSE
  → FLAG with code PEERUNC
  → Log: "[PEERUNC] Peer-review status uncertain | reason: X"
```

### Classification: Categories

Choose from `taxonomy.yaml`:
- `fondements` — What is PDA? Core definitions, history
- `evaluation` — Screening tools, assessment
- `presentation_clinique` — Behavioral profiles, case studies
- `etiologie` — Neurobiological underpinnings, mechanisms
- `prise_en_charge` — Treatment, educational strategies
- `comorbidites` — Anxiety, ADHD overlap
- `trajectoire` — Children, adolescents, adults

```
1. Read article to identify primary focus
2. Assign 1 PRIMARY category (what the article is MAINLY about)
3. Assign 0-2 SECONDARY categories (other topics covered substantially)

Primary category: Set is_primary = 1 in article_categories
Secondary categories: Set is_primary = 0

TOTAL: 1-3 categories per article
```

### Classification: Keywords

Keywords support search. Extract from:
1. Article title
2. Abstract/summary
3. Section headers
4. Key concepts discussed

```
KEYWORD RULES:
1. Use noun phrases, not sentences
2. Use lowercase except proper nouns
3. Include both English form and French equivalent if relevant
4. Aim for 5-15 keywords per article

EXAMPLES:
- "demand avoidance" / "évitement des demandes"
- "EDA-Q" (assessment tool name)
- "anxiety"
- "parent experiences"
- "school exclusion"
- "diagnostic validity"

Check if keyword already exists in keywords table:
  - IF EXISTS: reuse keyword_id
  - IF NOT EXISTS: create new keyword entry
```

### Rules

```
1. Read taxonomy.yaml signals before classifying
2. Apply decision trees in order: method → voice → peer_reviewed → categories
3. When uncertain, make best guess AND flag
4. Flag codes: METHUNC, VOICEUNC, PEERUNC
5. Always assign at least 1 category with is_primary = 1
6. Extract keywords after reading full content
```

---

## Step 5: TRANSLATE

**Purpose:** Translate title, summary, and full text from English to French.

**Input:** English text + glossary.yaml + training docs

**Output:** French title + French summary + French full text (if open access)

### What Gets Translated

| Content | Always? | Notes |
|---------|---------|-------|
| Title | YES | Single sentence |
| Summary/Abstract | YES | 150-300 words typically |
| Full text | IF open_access | Can be 5,000-10,000 words |
| Tables | IF present | Translate content, preserve structure |
| Figure captions | IF present | Translate captions only |
| References | NEVER | Keep in original language |
| Appendices | YES | If part of main content |

### Translation Method: Section-by-Section with Paragraph Attention

```
FOR each article:

  1. TRANSLATE TITLE
     - Single pass
     - Check glossary for key terms

  2. TRANSLATE SUMMARY
     - Single pass
     - Check glossary for key terms
     - Verify register matches Philippe & Contejean

  3. TRANSLATE FULL TEXT (if open_access)
     FOR each section (Introduction, Methods, Results, Discussion, etc.):

       a. Translate paragraph by paragraph
          - Check glossary at each paragraph boundary
          - Use exact terms from glossary.yaml
          - Match register to Philippe & Contejean 2018

       b. Review pass after section complete
          - Re-read French against English
          - Verify no omissions
          - Check terminology consistency within section

       c. Handle special content:
          - Quoted speech: use « guillemets »
          - Test names (ADOS, DISCO, etc.): keep in English
          - Statistics: preserve exact numbers and formatting
          - In-text citations: keep as-is (Author, Year)

  4. TRANSLATE TABLES (if present)
     - Cell by cell
     - Preserve structure as markdown table
     - FLAG with code TBL for human review

  5. FINAL REVIEW PASS
     - Read entire French translation
     - Check section-to-section consistency
     - Verify glossary terms used throughout
```

### Decision Points

| Question | Options | Decision |
|----------|---------|----------|
| Term not in glossary? | use best judgment? flag? add to glossary? | **Use judgment + FLAG** with code TERM |
| Very long document? | truncate? summarize? translate all? | **Translate all** |
| Full text translation? | only if open_access? never? | **Only if open_access = 1** (set in Step 2 after finding accessible copy) |
| Quality check? | self-review? human review? none? | **Self-review** after each section |

### Register and Style Requirements

**Must match:**
- Philippe & Contejean 2018 register (formal clinical French)
- HAS (Haute Autorité de Santé) conventions

**DO:**
- Use long, complex sentences with subordinate clauses (this is normal in French academic writing)
- Use hedging language: "peut se manifester", "semble", "paraissent"
- Use "nous" for authors when appropriate
- Use passive voice where source does

**DO NOT:**
- Add translator notes or commentary
- "Improve" or "clarify" the original
- Use informal register
- Simplify complex sentences
- Omit anything, even redundancy

### Storage Format: Markdown

```markdown
# [Translated Title]

## Résumé

[Translated abstract]

## Introduction

[Translated paragraphs...]

## Méthodes

### Participants

[Translated paragraphs...]

### Procédure

[Translated paragraphs...]

## Résultats

[Translated paragraphs...]

| Col 1 | Col 2 | Col 3 |
|-------|-------|-------|
| data  | data  | data  |

## Discussion

[Translated paragraphs...]

## Références

[NOT TRANSLATED - kept as-is]
```

### Rules

```
1. Before starting any translation:
   - Read glossary.yaml (load into working memory)
   - Review Philippe & Contejean register

2. During translation:
   - Work section by section
   - Work paragraph by paragraph within sections
   - Check glossary at paragraph boundaries
   - Note any terms not in glossary → FLAG with code TERM

3. After each section:
   - Re-read against original
   - Check for omissions
   - Verify terminology consistency

4. After complete translation:
   - Full read-through of French
   - Cross-section consistency check

5. If document has tables/figures:
   - FLAG with codes TBL/FIG
   - Still translate, but mark for human review
```

---

## Step 6: SAVE

**Purpose:** Write results to database.

**Input:** Classification + translation

**Output:** Updated database rows

### Decision Points

| Question | Options | Decision |
|----------|---------|----------|
| Which tables? | articles, translations, others? | **All four**: articles, translations, article_categories, article_keywords |
| Overwrite existing? | yes? no? merge? | **Update if exists**, INSERT if not |
| Transaction handling? | all-or-nothing? partial OK? | **All-or-nothing** per article |

### Tables Modified

| Table | Fields Updated |
|-------|----------------|
| `articles` | method, voice, peer_reviewed, source, open_access, doi, processing_status, processing_flags, processing_notes, processed_at, updated_at |
| `translations` | translated_title, translated_summary, translated_full_text, status, translator_notes |
| `article_categories` | article_id, category_id, is_primary |
| `article_keywords` | article_id, keyword_id |

### SQL Templates

#### Update Article Classification and Processing Status

```sql
UPDATE articles
SET
    method = :method,
    voice = :voice,
    peer_reviewed = :peer_reviewed,
    source = :source,
    open_access = :open_access,
    doi = :doi,
    processing_status = :processing_status,
    processing_flags = :processing_flags,
    processing_notes = :processing_notes,
    processed_at = datetime('now'),
    updated_at = datetime('now')
WHERE id = :article_id;
```

#### Insert or Update Translation

```sql
-- Check if translation exists
SELECT id FROM translations
WHERE article_id = :article_id AND target_language = 'fr';

-- If exists, UPDATE:
UPDATE translations
SET
    translated_title = :title_fr,
    translated_summary = :summary_fr,
    translated_full_text = :full_text_fr,
    status = 'translated',
    translator_notes = :flags,
    updated_at = datetime('now')
WHERE article_id = :article_id AND target_language = 'fr';

-- If not exists, INSERT:
INSERT INTO translations (
    article_id, target_language, translated_title, translated_summary,
    translated_full_text, status, translator_notes
) VALUES (
    :article_id, 'fr', :title_fr, :summary_fr,
    :full_text_fr, 'translated', :flags
);
```

#### Insert Categories (Delete-then-Insert Pattern)

```sql
-- Clear existing categories for this article
DELETE FROM article_categories WHERE article_id = :article_id;

-- Insert primary category
INSERT INTO article_categories (article_id, category_id, is_primary)
VALUES (:article_id, :primary_category_id, 1);

-- Insert secondary categories (0-2)
INSERT INTO article_categories (article_id, category_id, is_primary)
VALUES (:article_id, :secondary_category_id, 0);
```

#### Insert Keywords (Get-or-Create Pattern)

```sql
-- For each keyword:

-- 1. Check if keyword exists
SELECT id FROM keywords WHERE term = :keyword_term;

-- 2. If not exists, create it
INSERT INTO keywords (term) VALUES (:keyword_term);

-- 3. Link to article (ignore if already linked)
INSERT OR IGNORE INTO article_keywords (article_id, keyword_id)
VALUES (:article_id, :keyword_id);
```

### Transaction Wrapper

All saves for one article must succeed together or fail together:

```sql
BEGIN TRANSACTION;

-- 1. Update articles table
UPDATE articles SET ... WHERE id = :article_id;

-- 2. Upsert translation
INSERT OR REPLACE INTO translations ...;

-- 3. Replace categories
DELETE FROM article_categories WHERE article_id = :article_id;
INSERT INTO article_categories ...;

-- 4. Add keywords
INSERT OR IGNORE INTO article_keywords ...;

COMMIT;
-- On any error: ROLLBACK;
```

### Rules

```
1. Always use transaction for each article
2. If any SQL fails: ROLLBACK entire article, FLAG, continue to next
3. Update updated_at timestamp on every modification
4. translation.status = 'translated' when complete
5. translator_notes accumulates all FLAG codes for the article
```

---

## Step 7: LOG

**Purpose:** Record what happened for debugging and progress tracking.

**Input:** Results of all previous steps

**Output:** Updated article record with processing status

### Decision Points

| Question | Options | Decision |
|----------|---------|----------|
| Log format? | structured? prose? | **Structured** — fields on article record |
| Log location? | file? DB table? stdout? | **Database**: `articles` table |
| What to capture? | success only? errors? timing? | **Everything**: status, flags, notes, timestamp |

### Processing Fields on `articles` Table

| Field | Type | Description |
|-------|------|-------------|
| `processing_status` | TEXT | pending / translated / reviewed / skipped / error |
| `processing_flags` | TEXT | JSON array of flag codes (e.g., `["TBL", "PAYWALL"]`) |
| `processing_notes` | TEXT | Free text for details, skip reasons, error messages |
| `processed_at` | TEXT | ISO 8601 timestamp when processing completed |

### Status Values

| Status | Meaning | Next Action |
|--------|---------|-------------|
| `pending` | Not yet processed | Process it |
| `translated` | Processed, may have flags | Human reviews if flags present |
| `reviewed` | Human reviewed and approved | Done |
| `skipped` | Not processed (not PDA-related, etc.) | Check skip reason |
| `error` | Processing failed | Human fixes and re-runs |

### Flag Storage

Flags are stored as JSON array in `processing_flags`:

```sql
-- Article with table and terminology flags
UPDATE articles
SET processing_flags = '["TBL", "TERM"]'
WHERE id = 'post-id-16049';

-- Article with no flags
UPDATE articles
SET processing_flags = '[]'
WHERE id = 'post-id-16050';
```

### Processing Notes

Free text field for context. Examples:

```sql
-- Paywalled article
UPDATE articles
SET processing_notes = 'Full text behind paywall at sciencedirect.com. Translated summary only.'
WHERE id = 'post-id-16049';

-- Skipped article
UPDATE articles
SET processing_notes = 'Not PDA-related. Article is about general autism, PDA mentioned once in passing.'
WHERE id = 'post-id-16050';

-- Error
UPDATE articles
SET processing_notes = 'URL returned 404: https://example.com/paper.pdf'
WHERE id = 'post-id-16051';
```

### Rules

```
1. Update processing fields on EVERY article attempted
2. Set processing_status appropriately
3. Store flags as JSON array in processing_flags
4. Use processing_notes for human-readable context
5. Set processed_at to current timestamp
6. Flags in processing_flags should match flags in translator_notes
```

### Querying Processing Status

```sql
-- Count by status
SELECT processing_status, COUNT(*) as count
FROM articles
GROUP BY processing_status;

-- Find all flagged articles
SELECT id, source_title, processing_flags, processing_notes
FROM articles
WHERE processing_flags <> '[]';

-- Find errors
SELECT id, source_title, processing_notes
FROM articles
WHERE processing_status = 'error';

-- Articles needing human review (have flags)
SELECT id, source_title, processing_flags
FROM articles
WHERE processing_status = 'translated'
AND processing_flags LIKE '%TBL%'
   OR processing_flags LIKE '%FIG%'
   OR processing_flags LIKE '%META%'
   OR processing_flags LIKE '%TERM%';

-- Progress overview
SELECT
  COUNT(*) as total,
  SUM(CASE WHEN processing_status = 'translated' THEN 1 ELSE 0 END) as translated,
  SUM(CASE WHEN processing_status = 'reviewed' THEN 1 ELSE 0 END) as reviewed,
  SUM(CASE WHEN processing_status = 'pending' THEN 1 ELSE 0 END) as pending,
  SUM(CASE WHEN processing_status = 'skipped' THEN 1 ELSE 0 END) as skipped,
  SUM(CASE WHEN processing_status = 'error' THEN 1 ELSE 0 END) as errors
FROM articles;
```

---

## Flag Coding System

Flags are stored in `translator_notes` field using a structured format.

### Flag Format

```
[CODE] Description of issue | context or details
```

Multiple flags separated by semicolons:
```
[TBL] Contains 2 data tables | pages 16-17; [TERM] "interoception" not in glossary | used "intéroception"
```

### Flag Codes

| Code | Meaning | Severity | Action Required |
|------|---------|----------|-----------------|
| **Content Flags** |
| `TBL` | Contains tables | Review | Human checks table translation |
| `FIG` | Contains figures | Review | Human checks figure captions |
| `META` | Metadata mismatch | Review | Human verifies title/author/year |
| `LONG` | Unusually long document (>15 pages) | Info | Human may want to spot-check |
| **Access Flags** |
| `PAYWALL` | Full text behind paywall | Info | Summary-only translation |
| `404` | URL broken | Blocking | Human must provide source |
| `NOURL` | No source URL in DB | Blocking | Human must provide source |
| **Relevance Flags** |
| `TANGENT` | PDA mentioned but not central topic | Review | Human decides if worth including |
| `QUALITY` | Questionable quality/rigor | Review | Human decides if worth including |
| `SKIP` | Not PDA-related, skipped | Info | No action needed |
| **Translation Flags** |
| `TERM` | Term not in glossary | Review | Human may want to add to glossary |
| `AMBIG` | Ambiguous source text | Review | Human checks translation choice |
| `NOSUMM` | No abstract, summary written | Info | Human may want to verify summary |
| **Classification Flags** |
| `METHUNC` | Method classification uncertain | Review | Human verifies method |
| `VOICEUNC` | Voice classification uncertain | Review | Human verifies voice |
| `PEERUNC` | Peer-review status uncertain | Review | Human verifies |

### Flag Storage

Flags go in `translations.translator_notes` field:

```sql
UPDATE translations
SET translator_notes = '[TBL] Contains 2 data tables | Appendix 5-1; [TERM] "interoception" not in glossary | used "intéroception"'
WHERE article_id = 'post-id-XXXXX' AND target_language = 'fr';
```

### Querying Flagged Articles

```sql
-- All articles needing human review
SELECT a.id, a.source_title, t.translator_notes
FROM articles a
JOIN translations t ON a.id = t.article_id
WHERE t.translator_notes LIKE '%[TBL]%'
   OR t.translator_notes LIKE '%[FIG]%'
   OR t.translator_notes LIKE '%[META]%'
   OR t.translator_notes LIKE '%[TERM]%'
   OR t.translator_notes LIKE '%[TANGENT]%'
   OR t.translator_notes LIKE '%[QUALITY]%';

-- Blocking issues (cannot proceed)
SELECT a.id, a.source_title, t.translator_notes
FROM articles a
JOIN translations t ON a.id = t.article_id
WHERE t.translator_notes LIKE '%[404]%'
   OR t.translator_notes LIKE '%[NOURL]%';
```

---

## Error Handling

| Error Type | Flag Code | Response |
|------------|-----------|----------|
| URL fetch fails (404) | `404` | Log error, skip article, continue to next |
| URL fetch fails (paywall) | `PAYWALL` | Translate summary only, continue |
| No URL in database | `NOURL` | Log, skip article, continue to next |
| Content unreadable | `404` | Log error, skip article, continue to next |
| Classification uncertain | `METHUNC`/`VOICEUNC` | Make best guess, flag for review, continue |
| Translation term unknown | `TERM` | Use best judgment, flag for review, continue |
| DB write fails | — | Retry once, then halt with error message |

### Principle: Flag and Continue

Unless the error is truly blocking (DB failure), the process should:
1. Log the issue with appropriate flag code
2. Do what it can (e.g., translate summary if full text unavailable)
3. Continue to next article

This allows overnight runs to make progress even when some articles have issues.

---

## Resolved Questions

1. **Where should the processing log live?**
   → **Database**: `articles` table with `processing_status`, `processing_flags`, `processing_notes`, `processed_at` fields

2. **Do we need a separate "skipped articles" table?**
   → No. Skipped articles are in `articles` table with `processing_status = 'skipped'` and reason in `processing_notes`.

3. **Should flags trigger notifications or just accumulate for batch review?**
   → Accumulate for batch review. Flags are stored in:
   - `articles.processing_flags` (JSON array for querying)
   - `translations.translator_notes` (human-readable format)

   Build review interface to query articles by status and flags.

---

## Changelog

| Date | Change |
|------|--------|
| 2025-01-10 | Initial skeleton created |
| 2025-01-10 | Added Steps 1-2 with decisions |
| 2025-01-10 | Added "Finished" definition |
| 2025-01-10 | Added Step 3 (READ) with verification rules |
| 2025-01-10 | Added Step 5 (TRANSLATE) with section-by-section method |
| 2025-01-10 | Added Flag Coding System |
| 2025-01-10 | Completed Step 4 (CLASSIFY) with decision trees |
| 2025-01-10 | Completed Step 6 (SAVE) with SQL templates |
| 2025-01-10 | Completed Step 7 (LOG) with JSONL format |
| 2025-01-10 | Resolved open questions, marked status COMPLETE |
