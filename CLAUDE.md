# PDA France — French-Language PDA Resource

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
│   └── design-system.html             # Complete visual reference (fonts, colors, components)
├── training/
│   ├── philippe_contejean_2018.md     # Reference French PDA paper — GOLD STANDARD
│   ├── has_terminology_2017.md        # HAS guidelines terminology
│   └── style_notes.md                 # Human feedback on translations
├── external/
│   └── pda/                           # Collection of PDA research PDFs
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

## Article Workflow

**CRITICAL: One article at a time. Complete it fully. No batching.**

Do NOT:
- Classify all articles, then translate all articles
- Do summaries first, then full papers
- Any form of batch processing

DO:
- Pick one article
- Do ALL the work for that article (classify + translate + save)
- Only then move to the next

### Steps for Each Article

1. **Select** — Pick the next unprocessed article
2. **Read summary** — Read the English summary (already in database)
3. **Read article** — Fetch the source URL, read the full article
4. **Translate** — Translate summary to French (while content is fresh)
5. **Classify** — Assign method, voice, peer_reviewed, categories, keywords
6. **Save** — Write classification to `articles` table, translation to `translations` table
7. **Done** — Move to next article

### What "Complete" Means

An article is complete when it has:
- [ ] method assigned (empirical/synthesis/theoretical/lived_experience)
- [ ] voice assigned (academic/practitioner/organization/individual)
- [ ] peer_reviewed flag set (true/false)
- [ ] categories assigned (1 or more from taxonomy.yaml)
- [ ] keywords assigned (for searchability)
- [ ] French title translated
- [ ] French summary translated
- [ ] Saved to database

### Classification Signals

These are initial heuristics. Refine as we learn from each article.

**Method** — What type of work is this?
| Value | Signals |
|-------|---------|
| `empirical` | "N participants", "sample", "survey", "interviews conducted", "data collected", "findings" |
| `synthesis` | "review", "meta-analysis", "literature search", "N studies examined" |
| `theoretical` | "argues", "proposes", "framework", "critique", "conceptual model" |
| `lived_experience` | First-person narrative, "my child", "as a parent", "my experience" |

**Voice** — What perspective is this written from? (Not just author's job title)
| Value | Signals |
|-------|---------|
| `academic` | University affiliation, research framing, "this study", scholarly apparatus |
| `practitioner` | Clinical framing, "in my practice", guidance for professionals, case studies |
| `organization` | Published by charity/society, "commissioned by", institutional voice |
| `individual` | Personal narrative, no institutional framing, speaking for self/family |

**⚠️ Academic vs Practitioner ambiguity:** Many authors are both (e.g., clinical psychologist doing PhD research). Ask: *What perspective does the piece take?* A clinician writing up research → academic. A researcher offering clinical guidance → practitioner.

**Peer-reviewed** — Check for:
- Journal name in header/footer
- DOI
- "Published in [Journal]"
- Volume/issue numbers

**When uncertain:** Flag for human review rather than guessing.

### Translation Quality Requirements

**Register:**
- Academic/clinical French, formal but accessible
- Match French psychiatric literature register
- Use HAS (Haute Autorité de Santé) conventions

**Terminology:** See `data/glossary.yaml` for consistent translations.

**Anti-Patterns — What NOT to Do:**
- Add translator notes or editorial commentary
- "Improve" or "clarify" the original
- Use informal register
- Simplify complex sentences
- Omit anything, even redundancy
- Use machine-translation artifacts

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

1. **Link to originals, don't host** — copyright clean, original gets credit
2. **Article-by-article workflow** — keeps context tight, nothing half-done
3. **Multi-language support** — French now, structure supports Spanish/German later
4. **SQLite over YAML at scale** — queryable, handles relationships
5. **Static site** — no runtime, fast, cheap hosting
6. **Single source of truth for taxonomy** — `data/taxonomy.yaml` is canonical for all classification terms and their translations

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

- **"Let's translate"** — Start or continue translation work
- **"Check progress"** — Show translation status
- **"Add source [URL]"** — Add a new paper to the database

---

## Key Files to Read

When starting ANY work on this project:
1. `data/taxonomy.yaml` — CANONICAL classification terms and French labels
2. `data/glossary.yaml` — terminology for article content
3. `training/style_notes.md` — human feedback and corrections

When starting translation work, also:
4. Query database for untranslated articles

---

## Current State

### Completed
- [x] Captured 52 resources from PDA Society research overviews
- [x] Built glossary with ~200 terms across 18 categories
- [x] SQLite database with articles, categories, keywords
- [x] Architecture decisions documented
- [x] Vercel deployment configured (pda.expert, Paris region)
- [x] Article schema defined (see `docs/schema.md`)
  - Two-dimension classification: method (empirical/synthesis/theoretical/lived_experience) + voice (academic/practitioner/organization/individual)
  - Categories as tags, not hierarchy
  - Controlled keyword vocabulary
- [x] Canonical taxonomy file created (`data/taxonomy.yaml`) with EN/FR labels
- [x] SQLite schema migrated (added `method`, `voice`, `peer_reviewed` columns)
- [x] Complete Astro site with full i18n support
  - Tailwind CSS v4 styling
  - Language detection middleware (cookie → Accept-Language → French default)
  - BaseLayout with hreflang tags for SEO
  - All UI strings translated (FR/EN)
- [x] All page templates implemented
  - Homepage with ICP audience cards
  - ICP landing pages (/fr/professionnels, /fr/familles)
  - Category index and category detail pages
  - Article pages with answer capsules (AI citation optimized)
  - Classification badges (method, voice, peer-reviewed, open access)
  - About page
- [x] Pagefind search integration
  - Build script runs indexer after Astro build
  - Search UI at /fr/recherche and /en/search
- [x] Design system finalized and applied to components
  - Fonts: Lexend (UI) + Literata (content)
  - Color palette: Deep blue primary, teal accent, cream background
  - Wordmark: text-only with raised teal dot (no icon)
  - Card typography hierarchy locked in
  - Complete visual reference at `docs/design-system.html`
- [x] Site deployed to Vercel (pda.expert)

### Article Progress

**Classified and translated (2):**
1. "Practising Psychologists' Accounts of Demand Avoidance..." (post-id-16136) — empirical, practitioner, peer-reviewed
2. "What are the experiences and support needs of families..." (post-id-16049) — translated only, needs classification

**Remaining:** 50 articles need classification and translation

### Next Steps

Continue article-by-article workflow:
1. Select next unclassified article
2. Assign method, voice, peer_reviewed, categories, keywords
3. Translate summary (and full text if open access)
4. Save to database
5. Move to next article
