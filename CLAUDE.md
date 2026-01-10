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
│   └── decisions.md                   # Architecture and workflow decisions
├── training/
│   ├── philippe_contejean_2018.md     # Reference French PDA paper — GOLD STANDARD
│   ├── has_terminology_2017.md        # HAS guidelines terminology
│   └── style_notes.md                 # Human feedback on translations
├── external/
│   └── pda/                           # Collection of PDA research PDFs
├── scripts/
│   ├── init_db.py                     # Database schema initialization
│   ├── migrate_yaml_to_db.py          # YAML→SQLite migration
│   └── parse_pda_research.py          # HTML parser for PDA Society
└── site/                              # Astro static site
    └── src/
        └── lib/
            └── db.ts                  # Database query layer
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

## Translation Workflow

Process one article completely before moving to the next:

1. **Select** next untranslated article from database
2. **Assign** primary + secondary categories
3. **Assign** keywords for searchability
4. **Translate** summary (always)
5. **Translate** full paper (if open access)
6. **Save** to database
7. **Move** to next article

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
- [x] Astro site scaffolding with DB query layer
- [x] Architecture decisions documented
- [x] Vercel deployment (pda.expert, Paris region)
- [x] Article schema defined (see `docs/schema.md`)
  - Two-dimension classification: method (empirical/synthesis/theoretical/lived_experience) + voice (academic/practitioner/organization/individual)
  - Categories as tags, not hierarchy
  - Controlled keyword vocabulary
- [x] Canonical taxonomy file created (`data/taxonomy.yaml`) with EN/FR labels

### Next Session

**Sequence:**
1. Update SQLite schema (add `method`, `voice`, `peer_reviewed` fields)
2. Classify 3-5 test articles
3. Translate ONE article fully (test the workflow)
4. Build minimal site (article page template)
5. View translation rendered on site
6. Refine workflow based on learnings

**Not yet:**
- Mass classification of all 52 articles
- Mass translation
- Full site styling
