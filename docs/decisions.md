# Project Decisions Log

Architectural and workflow decisions made during project development.

---

## 2025-01-10: Content Display Strategy

**Decision:** Link to original source rather than extracting/hosting original text.

**Rationale:**
- Copyright cleaner — we're translating (transformative), not republishing
- Keeps us out of the business of hosting/maintaining original-language content
- Original source gets the traffic/credit
- Less work for us

**Implementation:** Each resource page displays:
- French translation (our work)
- Link to original source in its original language
- Full citation

**Note:** Original language may not always be English. If we find a German paper on PDA, we'd translate it to French and link to the German original. The site is French-language; sources can be any language.

---

## 2025-01-10: Translation Workflow — Article by Article

**Decision:** Process one article completely before moving to the next.

**Rejected alternative:** Batch categorization of all 52 resources, then batch translation.

**Rationale:**
- Keeps context tight — categorization decisions made with content fresh in mind
- Nothing sits in a half-done state
- Easier to maintain quality when focused on one piece

**Workflow per article:**
1. Read the original summary/paper
2. Assign primary + secondary categories
3. Translate summary
4. If open access: translate full paper
5. Save translation file
6. Update progress.yaml
7. Move to next

---

## 2025-01-10: Source Language Handling

**Decision:** The site is French-language. Source materials can be in any language.

**Implementation:**
- Track `source_language` field for each resource (default: "en" for English)
- Always link to original in its original language
- Translation target is always French

**Example:** A German PDA paper would have:
- `source_language: de`
- Link to German original
- French translation

---

## 2025-01-10: Architecture — SQLite + Astro + Vercel

**Decision:** Use SQLite for data, Astro for static site generation, Vercel for hosting.

**Rationale:**
- SQLite: queryable, handles relationships, single file, version-controllable
- Astro: ships zero JS by default, built for content sites, fast page loads
- Vercel: push-to-deploy, free tier generous, static hosting

**Stack:**
```
SQLite (data/pda.db) → Astro (build) → Pagefind (search) → Vercel (host)
```

**Domain:** pda.expert

---

## 2025-01-10: Data Model — Multi-Language Support

**Decision:** Structure translations table to support multiple target languages.

**Schema:**
- `translations(article_id, target_language, translated_title, translated_summary, ...)`
- Unique constraint on (article_id, target_language)

**Rationale:**
- French is primary target, but structure should support Spanish, German, etc.
- No code changes needed to add new languages later

---
