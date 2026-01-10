# PDA France — French-Language PDA Resource

## Project Overview

This project creates an authoritative French-language resource on Pathological Demand Avoidance (PDA), a behavioral profile within autism that is virtually unknown in France. The goal is to make the English-language research literature accessible to French clinicians (psychiatrists, pediatricians, psychologists) who would otherwise never encounter it.

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

## Target Audience

**Primary:** French-speaking clinicians who have never heard of PDA

- Psychiatrists
- Pediatricians
- Clinical psychologists
- School psychologists

**Secondary:** French-speaking parents who want to share professional literature with their child's care team

---

## Content Strategy

### Source Material

1. **PDA Society Research Overviews** — 52 papers with summaries (captured in `data/pda_research.yaml`)
2. **Full papers** — PDFs from open access sources, translated in full
3. **New research** — monitored and added as published

### Translation Approach

- Translate ONE piece fully (summary + full paper if available) before moving to the next
- Quality bar: Must read as authentic French academic/clinical writing
- A French clinician should not suspect it was translated

### Translation Workflow

1. Select next untranslated item from `data/pda_research.yaml`
2. Read training materials (glossary, style guide, reference texts)
3. Translate summary first
4. If open access, fetch and translate full paper
5. Save translations to YAML
6. Update progress tracking
7. Human spot-checks periodically

---

## Project Structure

```
/Users/jd/Projects/pda/
├── CLAUDE.md                          # This file — project instructions
├── data/
│   ├── pda_research.yaml              # Source of truth: 52 resources with EN summaries
│   ├── glossary.yaml                  # EN→FR terminology reference
│   └── progress.yaml                  # Translation progress (TODO)
├── training/
│   ├── philippe_contejean_2018.md     # Reference French PDA paper — GOLD STANDARD
│   ├── french_autism_terminology.md   # HAS guidelines terminology (TODO)
│   └── style_notes.md                 # Human feedback on translations (TODO)
├── external/
│   ├── pda/                           # Collection of PDA research PDFs
│   ├── Research overviews - PDA Society.html
│   ├── Research overviews - PDA Society.webarchive
│   └── research_overviews_extracted.html
├── scripts/
│   └── parse_pda_research.py          # HTML parser for PDA Society
└── site/                              # Static site output (TODO)
```

---

## Translation Quality Requirements

### Register

- Academic/clinical French, formal but accessible
- Match the register of French psychiatric literature (e.g., Neuropsychiatrie de l'enfance et de l'adolescence)
- Use conventions from HAS (Haute Autorité de Santé) autism guidelines

### Terminology Consistency

All translations must use consistent terminology. Key terms (see `data/glossary.yaml` when created):

| English | French |
|---------|--------|
| Pathological Demand Avoidance (PDA) | Syndrome d'évitement pathologique des demandes (PDA) |
| Autism Spectrum Disorder (ASD) | Trouble du spectre de l'autisme (TSA) |
| Extreme Demand Avoidance (EDA) | Évitement extrême des demandes (EED) |
| demand avoidance | évitement des demandes |
| anxiety-driven | lié à l'anxiété / motivé par l'anxiété |
| meltdown | crise / effondrement |
| masking | camouflage |

### Style Rules

- Longer sentences are acceptable in French academic writing
- Use appropriate hedging ("il semblerait que", "les données suggèrent")
- Preserve nuance — never oversimplify for translation convenience
- Author names stay as-is; do not translate
- Preserve all citations and references
- Rigid adherence to the text
- Never summarize: every translation must have 100% fidelity to the articles' language, structure and tone

### Anti-Patterns — What NOT to Do

**NEVER:**
- Add explanatory notes or editorial commentary ("Note du traducteur: ...")
- "Improve" or "clarify" the original — translate exactly what's there, even if awkward
- Use informal register even if the English is slightly casual
- Simplify complex sentences — if the original is dense, the translation should be dense
- Add transitions or connectors not present in the original
- Omit anything, including repetition or redundancy in the source
- Use machine-translation artifacts ("en termes de", overuse of "cela")
- Translate idioms literally when a French equivalent exists
- Use anglicisms when proper French terms exist (see glossary)

**IF a term has no established French equivalent:**
- Keep the English term
- On first use, provide French explanation in parentheses
- Example: "l'approche dite « low-demand » (à faible niveau d'exigence)"

**IF the original contains an error:**
- Translate the error faithfully
- Do NOT correct it silently

---

## MCP Server (TODO)

An MCP server will be built to support translation workflow:

### Planned Tools

- `get_translation_context()` — returns glossary, style rules, current progress
- `get_next_untranslated()` — returns the next piece to translate
- `save_translation(id, summary_fr, full_text_fr)` — writes translation to YAML
- `fetch_paper(url)` — retrieves full paper content for translation

### Discovery Agent (TODO)

An agent that monitors:
- PDA Society for new research additions
- PubMed for new PDA papers
- Flags new material for translation queue

---

## Current State

### Completed
- [x] Captured 52 resources from PDA Society research overviews
- [x] Parsed into structured YAML format (`data/pda_research.yaml`)
- [x] Identified open access status (41 open, 9 paywall, 2 unknown)
- [x] Extracted Philippe & Contejean 2018 as training reference (`training/philippe_contejean_2018.md`)
- [x] Built glossary from French reference materials (`data/glossary.yaml`)
- [x] Documented anti-patterns and style rules
- [x] Extracted HAS 2017 autism guidelines terminology (`training/has_terminology_2017.md`)
- [x] Expanded glossary with ~200 terms across 18 categories
- [x] First translation completed: Nawaz & Speer 2025

### In Progress
- [ ] Define scope/inclusion criteria for sources
- [ ] Static site generator (French-only clearing house)

### Not Started
- [ ] MCP server for translation workflow
- [ ] Deployment
- [ ] Discovery agent for new research

---

## Site Vision: French PDA Clearing House

**Concept:** A curated French-language library of translated PDA research summaries.

**Structure:**
```
/
├── index.html           # French landing page
├── ressources/
│   ├── nawaz-speer-2025.html
│   └── ...
├── glossaire.html       # Terminology reference (from glossary.yaml)
└── a-propos.html        # About, methodology, sources
```

**Open question:** What sources to include?
- Option A: PDA Society research overviews only (52 resources, curated)
- Option B: Expand to peer-reviewed PDA literature more broadly
- Option C: Include HAS/French governmental autism resources
- Decision needed on inclusion criteria before scaling translation effort

---

## Commands

When working on this project:

- **"Let's translate"** — Start or continue translation work. Read training materials, check progress, translate next item.
- **"Check progress"** — Show translation status across all resources
- **"Add source [URL]"** — Add a new paper/resource to the queue

---

## Key Files to Read

When starting translation work, always read:
1. `data/glossary.yaml` — terminology consistency
2. `training/style_notes.md` — human feedback and corrections
3. `data/progress.yaml` — what's been done, what's next
