# Article Schema Reference

This document defines what data we capture for each article and how it's classified.

**CANONICAL SOURCE: `data/taxonomy.yaml`**

All classification terms (method, voice, categories) and their multilingual labels are defined in `data/taxonomy.yaml`. That file is the single source of truth. This document explains the schema; the YAML file contains the exact values to use.

---

## Article Fields

### Identification

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Stable unique identifier |
| `title` | Yes | Original title (usually English) |
| `authors` | Yes | Author names as string |
| `year` | Yes | Publication year |
| `url` | No | Link to original source |
| `doi` | No | Digital Object Identifier if available |
| `journal` | No | Publication venue (journal, publisher, institution) |

### Content

| Field | Required | Description |
|-------|----------|-------------|
| `summary` | Yes | Original abstract/summary in source language |

### Flags

| Field | Type | Description |
|-------|------|-------------|
| `open_access` | boolean | Can we access and translate the full text? |
| `peer_reviewed` | boolean | Published in peer-reviewed venue? |

---

## Classification

Articles are classified on two orthogonal dimensions. Both are required.

### Method (how was the knowledge produced?)

Every article has exactly ONE method tag:

| Tag | Definition | Examples |
|-----|------------|----------|
| `empirical` | Original data collection — surveys, interviews, clinical observations, experiments, case series | Cerebra SGT Report (1,200 surveys), EDA-Q validation study, Eaton & Weaver clinic data |
| `synthesis` | Reviewing/analyzing existing work — systematic reviews, meta-analyses, literature reviews, introductory overviews | Philippe & Contejean 2018, Kildahl systematic review |
| `theoretical` | Conceptual argument or critique — no new data, working with ideas | Gillberg commentary, Moore critical perspective |
| `lived_experience` | First-hand personal or family experience | Parent essays, autistic adult perspectives |

### Voice (who produced it?)

Every article has exactly ONE voice tag:

| Tag | Definition | Examples |
|-----|------------|----------|
| `academic` | Researchers, scholars, university-affiliated | O'Nions, Gillberg, Happé |
| `practitioner` | Clinicians, educators, healthcare professionals | Eaton, clinical case studies |
| `organization` | Charities, societies, institutions | PDA Society, Cerebra |
| `individual` | Person or family speaking for themselves | Parent blogs, autistic adult accounts |

---

## Topic Categories

Articles are tagged with clinical topics (not hierarchical — an article can have multiple):

| ID | French Label | What it covers |
|----|--------------|----------------|
| `fondements` | Fondements | What is PDA? Core definitions, history, conceptualization |
| `evaluation` | Évaluation | Screening tools, diagnostic instruments, assessment approaches |
| `presentation_clinique` | Présentation clinique | Behavioral profiles, how PDA presents, case descriptions |
| `etiologie` | Étiologie et mécanismes | Neurobiological underpinnings, anxiety mechanisms, cognitive factors |
| `prise_en_charge` | Prise en charge | Treatment approaches, educational strategies, intervention |
| `comorbidites` | Comorbidités | Anxiety, ADHD, other overlapping conditions |
| `trajectoire` | Trajectoire développementale | Lifespan perspective — children, adolescents, adults |

---

## Keywords

Controlled vocabulary for search. Each keyword has:
- `id` — slug (e.g., `anxiety`, `school-exclusion`)
- `name_en` — English label
- `name_fr` — French label

Keywords are more granular than categories. An article about school exclusion would have:
- Category: `prise_en_charge` (it's about intervention/support)
- Keywords: `school-exclusion`, `education`, `inclusion`

---

## Translations

Each article can have translations in multiple languages:

| Field | Description |
|-------|-------------|
| `language` | Target language code (`fr`, `es`, etc.) |
| `title` | Translated title |
| `summary` | Translated abstract/summary |
| `full_text` | Full translation (only if `open_access` = true) |
| `status` | `pending` → `in_progress` → `review` → `published` |

---

## Examples

### Philippe & Contejean 2018
```
title: "Le syndrome d'évitement pathologique des demandes..."
authors: "Philippe A, Contejean Y"
year: 2018
journal: "Neuropsychiatrie de l'enfance et de l'adolescence"
method: synthesis
voice: practitioner
peer_reviewed: true
open_access: false
categories: [fondements, presentation_clinique]
```

### Cerebra Systems Generated Trauma Report
```
title: "Systems Generated Trauma"
authors: "Cerebra, Luke Clements"
year: 2025
method: empirical
voice: organization
peer_reviewed: false
open_access: true
categories: [prise_en_charge]
```

### EDA-Q Development (O'Nions 2013)
```
title: "Development of the Extreme Demand Avoidance Questionnaire"
authors: "O'Nions E, Christie P, Gould J, Viding E, Happé F"
year: 2013
method: empirical
voice: academic
peer_reviewed: true
open_access: true
categories: [evaluation]
```

### Gillberg Commentary 2014
```
title: "Reflections on the 2014 Paper by O'Nions et al"
authors: "Gillberg C"
year: 2014
method: theoretical
voice: academic
peer_reviewed: true
open_access: true
categories: [fondements]
```

### Parent Essay (hypothetical)
```
title: "Living with PDA: Our Family's Journey"
authors: "Anonymous parent"
year: 2024
method: lived_experience
voice: individual
peer_reviewed: false
open_access: true
categories: [prise_en_charge, presentation_clinique]
```
