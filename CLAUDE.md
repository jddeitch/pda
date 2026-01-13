# PDA France — French-Language PDA Resource

## Who JD Is

- 20+ years in SaaS/tech, but not a software engineer by trade
- Wants to understand WHY before WHAT
- Values robust thinking before coding
- Prefers direct, confident communication - no hedging or unnecessary apologies

## How We Work

You are an expert who double-checks things. You are skeptical and you do research. I am not always right. Neither are you, but we both strive for accuracy.

## Development Environment

**Python:** Use Homebrew Python, not system Python.

```bash
# CORRECT
/opt/homebrew/bin/python3.11 -c "..."
/opt/homebrew/bin/python3.11 -m pytest ...

# WRONG — will fail
python ...
python3 ...
```

Don't fumble around with `python` and `python3` — use the explicit brew path.

**Communication style:**

- Principles first - explain reasoning before showing code
- Use analogies - connect to real-world examples
- Be direct - no "I apologize but..." or excessive hedging
- Ask when unclear - clarify ambiguous requirements rather than guessing
- Think operationally - consider production consequences, not just implementation

**Before significant work, address:**

1. What assumptions are you making?
2. What other parts of the system does this affect?
3. What could this break?
4. How does this fit with existing patterns?

## STOP. READ FIRST. DON'T ASSUME.

**This is non-negotiable. Violating this wastes JD's time and breaks things.**

Before making ANY code changes:

### 1. Read the affected files FIRST

- Don't assume you know what's there
- Don't assume the problem is what you think it is
- Actually open and read the files you're about to change

### 2. Check the scope of impact

- What other files consume this code?
- What other lessons/components/pages use this pattern?
- Will your change break existing functionality?

### 3. Make one change, verify, then proceed

- Don't batch multiple changes hoping they all work
- Test incrementally
- If you can't test, at least explain what you're assuming

### Anti-patterns (NEVER DO THESE)

```
❌ BAD: "I'll add this feature to the page"
   *immediately starts editing without reading existing code*

❌ BAD: "This model needs a new field"
   *adds field and restructures template in one go without checking dependencies*

❌ BAD: "This should work for all cases"
   *changes shared code without verifying all consumers*
```

### Correct patterns (ALWAYS DO THESE)

```
✅ GOOD: "Let me first read the view to understand the current structure"
   *reads the view file, identifies the rendering logic*

✅ GOOD: "Before changing this, let me check what other templates use this pattern"
   *runs glob to see the actual files*

✅ GOOD: "This change affects a shared template - let me verify existing pages still work"
   *tests affected pages after making changes*
```

### When you catch yourself assuming

If you notice thoughts like:

- "I know this file does X..."
- "This should be simple..."
- "I'll just add..."

**STOP. Open the file. Read it. Then proceed.**

---

## Before Writing Any Code

State out loud:

1. **What I'm about to change** (specific files)
2. **Why this is the right place** (not just where the error appears)
3. **What else uses this code** (dependencies, consumers)
4. **What could break** (side effects)

If you can't answer these, you haven't read enough yet.

---

## Fix Things Properly (No Workarounds)

**The error is not the problem. The error is a symptom telling you where to look.**

Before writing any fix:

1. **Trace to source** - Not where the error appears, but where the bad state originates
2. **Explain the causal chain** - What sequence of events led to this failure?
3. **Fix at the appropriate level** - Address root cause, not symptom

### Never Do These

- Adding null checks/fallbacks for data that *should* be correct
- Fixing in component B because component A is broken
- Making changes until errors disappear without understanding why
- Logging errors and continuing as if nothing happened
- Duplicating logic to avoid touching fragile code
- Adding layers to isolate broken code instead of fixing it

### When a Proper Fix Is Big

If the root cause fix requires significant refactoring, **surface it explicitly**:

> "The proper fix requires X. A workaround would do Y but leaves Z unfixed. Which path do you want?"

Never silently choose the workaround. JD decides whether to accept technical debt.

---

## No Hardcoded Strings

**Language-specific strings belong in YAML, not code.**

This project handles multilingual content (English, French, potentially more). Any string used for pattern matching, section detection, or language-specific logic must be in a YAML config file, not hardcoded in Python.

**Config files:**
- `data/section_headings.yaml` — section header patterns (abstract, references, etc.)
- `data/glossary.yaml` — terminology translations
- `data/taxonomy.yaml` — classification terms

**Anti-pattern:**
```python
# BAD - hardcoded strings
SECTIONS = ['abstract', 'résumé', 'references', 'références']
```

**Correct pattern:**
```python
# GOOD - load from YAML
with open('data/section_headings.yaml') as f:
    config = yaml.safe_load(f)
```

When adding new pattern-matching logic, check if an appropriate YAML file exists. If not, create one.

---

## Project Overview

This project creates an authoritative French-language resource on Pathological Demand Avoidance (PDA), a behavioral profile within autism that is virtually unknown in France. The goal is to make the English-language research literature accessible to French clinicians (psychiatrists, pediatricians, psychologists) who would otherwise never encounter it.

**Domain:** pda.expert

### Why This Matters

- There is ONE peer-reviewed French article on PDA (Philippe & Contejean, 2018)
- French clinicians searching in French find almost nothing
- Children with PDA in France are being misdiagnosed or not helped in part because the professionals don't know PDA exists, in part because the profession's roots through psychoanalysis means that doctors are ill- or un-equipped, and in part because professional arrogance, and in part because of the "blame-the-parents" issues that traumatizes parents the world around.
- The project owner has a son with PDA and lives in France

### Success Criteria

1. A French doctor searching "évitement pathologique des demandes" finds this site
2. AI systems (ChatGPT, Claude, Perplexity) cite these translations when asked about PDA in French
3. A clinician reads a translated paper and recognizes a patient they couldn't previously understand

### What This Is NOT

- Not a parent support community (browser translation of PDA Society handles that)
- Not original research
- Not a forum or discussion platform

---

## Architecture

### Tech Stack

```
SQLite (data/pda.db)     ← articles, translations, categories, keywords
        ↓
Astro (site/)            ← reads DB at build time, generates static HTML
        ↓
Pagefind                 ← indexes the HTML for client-side search
        ↓
Vercel                   ← hosts static files at pda.expert
```

### Data Model

See `docs/schema.md` for full schema reference.

**Core tables:**
- `articles` — source material with classification
- `translations` — one per article per target language
- `categories` — clinical topic tags (not hierarchical)
- `keywords` — searchable terms

**Article Classification (two dimensions, both required):**

| Dimension | Tags | Meaning |
|-----------|------|---------|
| **Method** | `empirical` | Original data collection (surveys, interviews, trials) |
| | `synthesis` | Reviewing/analyzing existing work |
| | `theoretical` | Conceptual argument, critique |
| | `lived_experience` | First-hand personal or family experience |
| **Voice** | `academic` | Researchers, scholars, university-affiliated |
| | `practitioner` | Clinicians, educators, healthcare professionals |
| | `organization` | Charities, societies, institutions |
| | `individual` | Person or family speaking for themselves |

**Flags:**
- `peer_reviewed` — boolean
- `open_access` — boolean (determines if we can translate full text)

**Examples:**
- Philippe & Contejean 2018: `synthesis` + `practitioner` + `peer_reviewed`
- Cerebra SGT Report: `empirical` + `organization`
- EDA-Q Development: `empirical` + `academic` + `peer_reviewed`
- Gillberg Commentary: `theoretical` + `academic` + `peer_reviewed`
- Parent essay: `lived_experience` + `individual`

### Project Structure

```
/Users/jd/Projects/pda/
├── CLAUDE.md                          # This file — project instructions
├── data/
│   ├── pda.db                         # SQLite database (source of truth)
│   ├── taxonomy.yaml                  # CANONICAL: method, voice, categories, flags with translations
│   ├── glossary.yaml                  # EN→FR terminology for article content
│   ├── pda_research.yaml              # Original import data (archived)
│   └── categories.yaml                # Legacy — superseded by taxonomy.yaml
├── docs/
│   ├── decisions.md                   # Architecture and workflow decisions
│   ├── design-system.html             # Complete visual reference (fonts, colors, components)
│   ├── schema.md                      # Database schema reference
│   └── translation-machine-plan.md    # MCP server specification (THE translation workflow)
├── training/
│   ├── philippe_contejean_2018.md     # Reference French PDA paper — GOLD STANDARD
│   ├── has_terminology_2017.md        # HAS guidelines terminology
│   └── style_notes.md                 # Human feedback on translations
├── external/
│   └── pda/                           # Collection of PDA research PDFs
├── cache/                             # AUTO-MANAGED by MCP server
│   └── articles/                      # Downloaded/cached PDFs and extracted text
│       ├── {article_id}.pdf           # Cached from source_url
│       ├── {article_id}.html          # Cached HTML when PDF not available
│       └── {article_id}.txt           # Pre-extracted text (takes precedence)
├── intake/                            # HUMAN-MANAGED: New articles to add
│   └── articles/                      # Drop PDFs here for ingestion
├── mcp_server/                        # Translation Machine MCP server
│   ├── __init__.py
│   ├── server.py                      # Main MCP server entry point
│   ├── tools.py                       # Tool implementations
│   ├── validation.py                  # Classification validation
│   ├── database.py                    # SQLite operations + session state
│   ├── taxonomy.py                    # Loads taxonomy.yaml
│   ├── pdf_extraction.py              # PDF extraction with fallback chain
│   ├── glossary.py                    # Glossary matching with variants
│   └── quality_checks.py              # spaCy sentence counting, etc.
├── scripts/
│   ├── init_db.py                     # Database schema initialization
│   ├── migrate_schema.py              # One-time migration for method/voice/peer_reviewed
│   ├── migrate_yaml_to_db.py          # YAML→SQLite migration
│   └── parse_pda_research.py          # HTML parser for PDA Society
└── site/                              # Astro static site
    ├── astro.config.mjs               # Astro configuration
    ├── postcss.config.js              # PostCSS for Tailwind v4
    └── src/
        ├── i18n/
        │   ├── config.ts              # Language config, supported languages
        │   └── translations.ts        # All UI strings (FR/EN)
        ├── layouts/
        │   └── BaseLayout.astro       # Common layout with hreflang tags
        ├── middleware.ts              # Language detection + redirect
        ├── styles/
        │   └── global.css             # Tailwind v4 imports + theme vars
        ├── lib/
        │   └── db.ts                  # Database query layer
        ├── components/
        │   ├── LanguageSwitcher.astro
        │   ├── ICPCard.astro          # ICP audience cards
        │   ├── CategoryCard.astro
        │   ├── ArticleCard.astro
        │   ├── AnswerCapsule.astro    # AI-optimized key findings
        │   └── ClassificationBadges.astro
        └── pages/
            ├── index.astro            # Root redirect to /fr
            ├── admin/                 # Admin interface (local dev only)
            │   ├── index.astro        # Dashboard
            │   ├── articles/          # Article review
            │   ├── preprocessing.astro # PDF queue
            │   └── settings.astro     # Review interval config
            └── [lang]/
                ├── index.astro        # Homepage with ICP cards
                ├── professionnels.astro
                ├── familles.astro
                ├── recherche.astro    # Pagefind search
                ├── a-propos.astro
                ├── articles/[slug].astro
                └── categories/
                    ├── index.astro
                    └── [category].astro
```

---

## Target Audience

**Primary:** French-speaking clinicians who have never heard of PDA

- Psychiatrists
- Pediatricians
- Clinical psychologists
- School psychologists

**Secondary:** French-speaking parents who want to share professional literature with their child's care team

---

## Content Categories

Articles are tagged by clinical topic (not hierarchical — an article can have multiple):

| ID | French | Purpose |
|----|--------|---------|
| fondements | Fondements | What is PDA? Core definitions, history |
| evaluation | Évaluation | Screening tools, assessment |
| presentation_clinique | Présentation clinique | Behavioral profiles, case studies |
| etiologie | Étiologie et mécanismes | Neurobiological underpinnings |
| prise_en_charge | Prise en charge | Treatment, educational strategies |
| comorbidites | Comorbidités | Anxiety, ADHD overlap |
| trajectoire | Trajectoire développementale | Children, adolescents, adults |

---

## Translation Machine

**Translation is handled by the MCP server.** See `docs/translation-machine-plan.md` for the complete specification.

### How It Works

The Translation Machine is an MCP server that enforces the translation pipeline deterministically. It:

1. **Controls what Claude sees** — Feeds source text in chunks (3-5 paragraphs), preventing "skim and summarize" behavior
2. **Validates all inputs** — Rejects invalid taxonomy values, enforces workflow order
3. **Runs quality checks** — Sentence count ratios, word ratios, glossary term verification
4. **Maintains state** — All progress in SQLite; any session can resume

### MCP Tools

| Tool | Purpose |
|------|---------|
| `get_next_article()` | Returns next article + fresh taxonomy |
| `get_chunk(article_id, n)` | Returns chunk n + relevant glossary terms |
| `validate_classification(...)` | Validates classification, returns token |
| `save_article(token, ...)` | Saves with quality checks |
| `skip_article(id, reason, flag)` | Marks article as skipped |
| `get_progress()` | Returns status counts |
| `ingest_article(filename)` | Imports PDF from intake/ folder |

### Workflow (Claude's Perspective)

```
1. Call get_next_article()
2. Translate title + summary
3. IF open_access: loop get_chunk() until complete
4. Call validate_classification() → get token
5. Call save_article() with token
6. Repeat until SESSION_PAUSE or COMPLETE
```

### Human Review Interval

After every N articles (default: 5), the server returns `SESSION_PAUSE`. Human reviews flagged articles in `/admin` before continuing. This catches drift before it compounds.

### Quality Checks (Automated)

| Check | Flag | Blocks Save? |
|-------|------|--------------|
| Sentence count >15% variance | SENTMIS | Yes |
| Word ratio outside 0.9-1.5 | WORDMIS | Yes |
| Content word Jaccard < 0.6 | WORDDRIFT | No (warning) |
| Glossary term missing | TERMMIS | No (warning) |
| Statistics modified | STATMIS | No (warning) |

### Translation Principle

**Match the source.** The register is IN the source text — don't infer it and apply it, just match what's there. Glossary terms must be consistent; everything else follows the author's style.

---

## Design System

See `docs/design-system.html` for the complete visual reference.

### Fonts
- **Lexend** (sans-serif) — UI, navigation, metadata, authors, key findings content
- **Literata** (serif) — Page H1s, article body text, card descriptions

### Colors
- **Primary (Deep Blue)** — trust, authority, actions (`primary-500: #285589`)
- **Accent (Teal)** — highlights, trust signals (`accent-300: #5CC5CE`)
- **Stone** — borders, metadata, secondary text
- **Cream** — warm background (`cream-200: #F4EEE8`)
- **Dark** — footer (`#241E1E`)

### Wordmark
No icon. Text-only with raised teal dot:
- **Light background:** `pda` (primary-500) `•` (accent-500) `expert` (accent-500)
- **Dark background:** `pda` (white) `•` (accent-200) `expert` (accent-200)
- Dot position: `relative top-px` (1px below center, at e crossbar level)

### Card Typography Hierarchy
| Element | Size | Font |
|---------|------|------|
| Title | `text-xl` (20px) | Lexend bold |
| Authors | `text-xs` (12px) | Lexend |
| Description | `text-base` (16px) | Literata |

---

## Key Decisions

See `docs/decisions.md` for rationale. Summary:

1. **Host originals AND link to source** — complete resource with full attribution (see Copyright Position below)
2. **Article-by-article workflow** — keeps context tight, nothing half-done
3. **Multi-language support** — French now, structure supports Spanish/German later
4. **SQLite over YAML at scale** — queryable, handles relationships
5. **Static site** — no runtime, fast, cheap hosting
6. **Single source of truth for taxonomy** — `data/taxonomy.yaml` is canonical for all classification terms and their translations
7. **MCP-based Translation Machine** — enforces workflow deterministically, prevents Claude failure modes (summarizing, skipping, editorial drift) via chunked delivery and automated quality checks

---

## Copyright Position

**Decision (January 2025):** Host original English articles alongside French translations, with full attribution and links to canonical sources.

### Rationale

The original position ("link to originals, don't host") was based on a flawed premise — that linking somehow reduces copyright exposure. It doesn't. **The translation itself is the derivative work.** Whether we host the original alongside it is legally irrelevant to that infringement.

What hosting the original DOES provide:
- **Scholarly completeness** — readers can verify translations against source
- **User utility** — no paywalls, no link rot, no broken URLs
- **Resilience** — academic URLs die; our archive survives
- **Honesty** — we have the content in our cache anyway; pretending otherwise is theater

### Legal Landscape (France/EU)

**France's Loi République Numérique (2016), Article 30:**
- Researchers have an inalienable right to republish publicly-funded work after 6-12 months embargo
- This right is "d'ordre public" — contract clauses contradicting it are void
- Applies to research funded ≥50% by public money (grants, EU funds, universities)

**Secondary Publication Rights in EU:**
- Six member states (Germany, France, Austria, Belgium, Netherlands, Bulgaria) have similar laws
- EU moving toward mandatory research exceptions
- 2024 European Commission study recommends EU-wide secondary publication right

**Article Tiers:**
| Tier | Source | Status |
|------|--------|--------|
| Open Access | CC-BY or similar | Fully clear to host + translate |
| Publicly-funded | University researchers on grants | Authors have secondary publication rights |
| Proprietary | Private publishers | Technical infringement, but... |

### Practical Enforcement Reality

For Tier 3 (proprietary) articles, we accept calculated risk based on:

1. **No damages** — We're not selling anything, not diverting subscription revenue
2. **Reputation cost to plaintiff** — Suing a father's free resource helping autistic kids? PR disaster
3. **Cost-benefit** — International litigation for niche autism papers exceeds any recovery
4. **Actual effect** — We increase citation/visibility, credit authors, link to originals

**The posture:** We're building a public educational resource. We link to canonical sources, we credit fully, we make no money. If anyone objects, we comply with takedown requests promptly and politely.

### Implementation

Each article displays:
- Full citation (authors, journal, year, pages)
- Link to canonical source (journal/publisher URL)
- Archived PDF copy for reference
- French translation

**Example attribution block:**
> **Source:** Philippe, A. & Contejean, Y. (2018). Le syndrome d'évitement pathologique des demandes. *Neuropsychiatrie de l'enfance et de l'adolescence*, 66, 103-108.
>
> [Article original (journal)] | [Version PDF archivée]

### References

- [Loi République Numérique, Article 30](https://www.ouvrirlascience.fr/guide-application-loi-republique-numerique-article-30-ecrits-scientifiques-version-courte/)
- [Secondary Publication Rights in Europe](https://link.springer.com/article/10.1007/s40319-025-01620-6)
- [EU Copyright Exceptions for Research](https://libereurope.eu/wp-content/uploads/2020/09/A-Basic-Guide-to-Limitations-and-Exceptions-in-EU-Copyright-Law-for-Libraries-Educational-and-Research-FINAL-ONLINE-1.pdf)

---

## Consistency Rules

**CRITICAL: Inconsistency will make this resource worthless.**

1. **Taxonomy terms**: Always use exact values from `data/taxonomy.yaml`. Never paraphrase, abbreviate, or "improve" them.

2. **Before any classification or translation work**, read:
   - `data/taxonomy.yaml` — method, voice, category terms
   - `data/glossary.yaml` — content terminology

3. **French labels are fixed**: Use "Empirique" not "Données empiriques". Use "Synthèse" not "Revue de littérature". The YAML is law.

4. **When in doubt, check the YAML**. If a term isn't there, ask before inventing one.

---

## Commands

When working on this project:

### Preprocessing (PDF → Database)

- **"Let's preprocess"** — Call `start_preprocessing()` which shows available work and counts:
  1. Shows datalab files ready to parse and PDFs in intake/
  2. Shows counts: archived (done), pending review, available to process
  3. **ASK THE USER** which file to process — do not pick automatically
  4. Each tool returns `next_step` telling you exactly what to call next

- **"Preprocess [filename]"** — Call `start_preprocessing(filename="...")` for a specific file

The workflow enforces order: no skipping steps, no starting new articles until current one is done.

### Translation (Database → French)

- **"Let's translate"** — Start the Translation Machine (call `get_next_article()`)
- **"Check progress"** — Call `get_progress()` to show translation status
- **"Add source [filename]"** — Call `ingest_article(filename)` for PDFs in `intake/articles/`

---

## Key Files to Read

When starting ANY work on this project:
1. `data/taxonomy.yaml` — CANONICAL classification terms and French labels
2. `data/glossary.yaml` — terminology for article content
3. `training/style_notes.md` — human feedback and corrections

When working on the Translation Machine:
4. `docs/translation-machine-plan.md` — MCP server specification (single source of truth)

---

## Article Intake Workflow

### File Flow (IMPORTANT)

Files travel together through stages. Each folder represents a state:

```
cache/articles/                    ← Working directory
├── datalab-output-*.json          ← Raw Datalab output (Stage 1)
├── {slug}.json                    ← Renamed raw JSON (Stage 2+)
├── {slug}_parsed.json             ← Parser output (Stage 2+)
└── images/{slug}/                 ← Extracted figures

cache/articles/ready/              ← Awaiting human review
├── {slug}.json                    ← Raw JSON (moved here at Stage 3)
├── {slug}_parsed.json             ← Parsed JSON (moved here at Stage 3)
└── images/{slug}/                 ← Figures

cache/articles/archived/           ← Approved and in database
├── {slug}.json                    ← Raw JSON (backup)
├── {slug}_parsed.json             ← Final parsed content
└── images/{slug}/                 ← Figures
```

**The principle:** Files travel together. All three items ({slug}.json, {slug}_parsed.json, images/{slug}/) move as a unit between folders.

### Stage Transitions

| Stage | Trigger | Files Move |
|-------|---------|------------|
| **1. Downloaded** | Manual download or `extract_pdf()` | `datalab-output-*.json` lands in `cache/articles/` |
| **2. Parsed** | `parse_datalab_file()` | Renamed to `{slug}.json`, creates `{slug}_parsed.json` |
| **3. Step 4 Complete** | `step4_complete()` | ALL files move to `ready/` |
| **4. Human Approved** | `/admin/review` approve | ALL files move to `archived/` |
| **4b. Human Rejected** | `/admin/review` reject | ALL files move back to `cache/articles/` |

### Preprocessing Steps

```
1. PDF arrives (intake/articles/ OR manual Datalab download)

2. Extract: extract_pdf() OR manual download from Datalab website
   → datalab-output-{id}.json in cache/articles/

3. Parse: parse_datalab_file()
   → {slug}.json (renamed)
   → {slug}_parsed.json (new)

4. AI Enhancement: step4_check_* / step4_confirm_* tools
   → Fills missing metadata, fixes warnings, wraps formulas

5. Complete: step4_complete()
   → ALL files move to ready/

6. Human Review: /admin/review
   → Approve: ALL files move to archived/, data inserted to SQLite
   → Reject: ALL files move back to cache/articles/ for rework
```

### Translation (After Approval)

```
SQLite articles                    ← Translation Machine reads from here
        ↓
get_chunk() serves body_html       ← Pre-cleaned, cruft removed
        ↓
Claude translates chunks           ← Quality checks on each chunk
        ↓
SQLite translations                ← Translated content saved
        ↓
/admin/articles                    ← Human review of flagged translations
```

### Step 4: AI Enhancement (Tool-Enforced)

Step 4 is enforced by MCP tools. You MUST call them in sequence — each tool blocks until the previous step is complete.

**The tools enforce this order:**

```
step4_check_fields(slug)       → Returns missing/empty fields
step4_confirm_fields(slug, ...) → You provide corrections or confirm OK
                                  ↓ Cannot proceed until complete
step4_check_warnings(slug)      → Returns orphan paragraphs, parser warnings
step4_confirm_warnings(slug, ...)→ You provide fixes or acknowledge
                                  ↓ Cannot proceed until complete
step4_check_references(slug)    → Returns reference extraction status
step4_confirm_references(slug, ...)→ You add missing refs or confirm OK
                                  ↓ Cannot proceed until complete
step4_check_formulas(slug)      → Returns unwrapped statistical formulas
step4_confirm_formulas(slug, ...)→ You wrap formulas or confirm none needed
                                  ↓ Cannot proceed until complete
step4_complete(slug)            → Moves article to ready/ for human review

step4_reset(slug)               → Clears state, allows re-running Step 4 from start
```

**Enforcement rules:**
- Each `check` function must be called before its corresponding `confirm`
- Each `confirm` marks that step complete, unlocking the next `check`
- `step4_complete` requires ALL four checks to be complete
- Use `step4_reset` if you need to start over (state file persists across sessions)

**What each check looks for:**

1. **Fields** — title, authors, year, citation, abstract (missing or empty?)
2. **Warnings** — [ORPHAN?] paragraphs starting with lowercase (split from previous?)
3. **References** — Were they extracted? French articles use "Références" or "Bibliographie"
4. **Formulas** — Unwrapped statistical notation:
   - F-statistics: `F(1, 156) = 4.07`
   - t-tests: `t(45) = 2.31`
   - Chi-square: `χ²(2) = 8.45`
   - p-values: `p < .05`, `p = .001`
   - Effect sizes: `η² = .12`, `d = 0.45`
   - Correlations: `r = .67`
   - Means/SDs: `M = 4.2, SD = 1.1`

**Formula normalization is judgment-based:** Ages, sample sizes in prose don't need wrapping. Wrap statistical test results and their parameters.

**Example formula wrapping:**
```html
<!-- Before -->
There was no significant difference, F(1, 156) = 2.31, p = .13.

<!-- After -->
There was no significant difference, <span class="formula">F(1, 156) = 2.31, p = .13</span>.
```

### Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/batch_extract.py` | Batch process PDFs through Datalab Marker API |
| `scripts/parse_article_structure.py` | Parse HTML into title, authors, abstract, body, refs |
| `scripts/enhance_parsed_article.py` | Apply AI-extracted metadata corrections to JSON |
| `scripts/batch_runner.py` | Autonomous batch processor daemon |

---

## Batch Processing (Fire and Forget)

Batch processing lets you process multiple articles autonomously without interactive approval. Click a button, walk away, come back to results.

### How to Use (Admin UI)

1. Start dev server with admin enabled:
   ```bash
   ENABLE_ADMIN=true npm run dev
   ```

2. Go to http://localhost:4321/admin

3. In the "Batch Processing" section:
   - Click **Start Preprocessing** or **Start Translation**
   - Enter count (1-50 articles)
   - Click confirm

4. Watch progress update every 5 seconds:
   - Progress bar shows articles completed
   - Event log shows each article as it finishes
   - Current article being processed is displayed

5. Cancel anytime with the Cancel button

### Architecture

```
Admin UI "Start" button
        ↓
POST /api/batch/start
        ↓ (spawns detached process, returns immediately)
scripts/batch_runner.py (daemonizes)
        ↓
claude --print --dangerously-skip-permissions --tools "" --mcp-config batch-mcp-config.json
        ↓
Progress markers parsed → SQLite batch_jobs table
        ↓
GET /api/batch/status (polled every 5s by admin UI)
```

### Security Model

**CRITICAL: This system runs Claude autonomously without permission prompts.**

Security measures in place:

1. **`--tools ""`** — Disables ALL built-in Claude Code tools (Bash, Write, Edit, Read, etc.)
2. **MCP-only** — Claude can ONLY use tools from `pda-translation-machine` MCP server
3. **MCP constraints** — Those tools only write to:
   - `cache/articles/` directory
   - `intake/articles/` directory
   - `data/pda.db` SQLite database
4. **No shell access** — Claude cannot execute arbitrary commands
5. **Single job** — Only one batch job can run at a time
6. **Full logging** — Every Claude output goes to `logs/batch/{job-id}.log`

**What Claude CAN do in batch mode:**
- Call MCP tools to process articles through preprocessing pipeline
- Call MCP tools to translate articles
- Read/write files in cache/ and intake/ via MCP tools
- Update SQLite database via MCP tools

**What Claude CANNOT do in batch mode:**
- Run Bash commands
- Write arbitrary files
- Read files outside what MCP tools expose
- Make network requests (except through MCP tools)
- Access system resources

### Files

| File | Purpose |
|------|---------|
| `scripts/batch_runner.py` | Main daemon that runs Claude CLI |
| `batch-mcp-config.json` | MCP server config (only pda-translation-machine) |
| `site/src/pages/api/batch/start.ts` | API to start batch job |
| `site/src/pages/api/batch/status.ts` | API to poll job status |
| `site/src/pages/api/batch/cancel.ts` | API to cancel running job |
| `logs/batch/` | Log files for each job |

### Database Tables

```sql
-- Job tracking
batch_jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT,           -- 'preprocessing' | 'translation'
    status TEXT,             -- 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
    target_count INTEGER,
    processed_count INTEGER,
    current_article TEXT,
    pid INTEGER,             -- For cancellation
    error_message TEXT,
    log_path TEXT
)

-- Event log
batch_job_events (
    job_id TEXT,
    event_type TEXT,         -- 'started' | 'article_start' | 'article_complete' | 'article_error' | 'completed'
    article_slug TEXT,
    message TEXT,
    timestamp TEXT
)
```

### Troubleshooting

**Job stuck at "pending":**
- Check if Python path is correct in `start.ts` (line 95: `/opt/homebrew/bin/python3.11`)
- Check logs in `logs/batch/{job-id}.log`

**Job fails immediately:**
- Ensure `batch-mcp-config.json` exists and has correct paths
- Ensure MCP server can start: `/opt/homebrew/bin/python3.11 -m mcp_server.server`

**Claude not using MCP tools:**
- Verify `--tools ""` is in the command (batch_runner.py line 204)
- Check that prompt explicitly tells Claude to use MCP tools

**View full Claude output:**
```bash
tail -f logs/batch/{job-id}.log
```

### Admin Access

```bash
ENABLE_ADMIN=true npm run dev
# Then visit http://localhost:4321/admin
```

Admin pages:
- `/admin` — Dashboard (translation progress, flagged articles)
- `/admin/articles` — Article list with filters
- `/admin/articles/[id]` — Individual article review
- `/admin/preprocessing` — Failed PDF extractions
- `/admin/review` — **NEW: Review parsed articles before translation**
- `/admin/settings` — Review interval config

### Database Fields (articles table)

New extraction fields added:
- `raw_html` — Original Datalab output (backup, always preserved)
- `abstract` — Extracted abstract from PDF
- `body_html` — Cleaned main content (cruft stripped, paragraphs joined)
- `citation` — Journal, volume, pages
- `acknowledgements` — Acknowledgements section
- `references_json` — JSON array of reference strings

---

## Current State

### Completed

**Infrastructure:**
- [x] 52 resources captured from PDA Society research overviews
- [x] Glossary with ~200 terms across 18 categories
- [x] SQLite database with articles, translations, categories, keywords
- [x] Canonical taxonomy (`data/taxonomy.yaml`) with EN/FR labels
- [x] Site deployed to Vercel (pda.expert)

**Astro Site:**
- [x] Full i18n support (FR/EN)
- [x] All page templates (homepage, ICP pages, articles, categories, search)
- [x] Design system applied (Lexend + Literata fonts, deep blue/teal palette)
- [x] Pagefind search integration

**Translation Machine Plan:**
- [x] Complete MCP server specification (`docs/translation-machine-plan.md`)
- [x] Chunked delivery design (prevents summarizing/skipping)
- [x] Quality check system (SENTMIS, WORDMIS, WORDDRIFT, TERMMIS, STATMIS)
- [x] Human review interval mechanism
- [x] PDF extraction pipeline with fallbacks
- [x] Admin interface design

### Article Progress

**Translated:** 2 articles
**Remaining:** 50 articles need classification and translation

### Next Milestone: Build the Translation Machine

The MCP server needs to be built. See `docs/translation-machine-plan.md` Part 12 for implementation phases:

1. **Phase 1:** MCP Server Core — `server.py`, `database.py`, `taxonomy.py`, basic tools
2. **Phase 2:** Chunked Delivery — `get_chunk()`, PDF extraction, glossary matching
3. **Phase 3:** Quality Checks — spaCy sentence counting, word ratios, Jaccard
4. **Phase 4:** Validation + Save — tokens, transactions, review interval
5. **Phase 5:** Admin Interface — dashboard, article review, settings
6. **Phase 6:** Integration Testing — end-to-end with real articles
