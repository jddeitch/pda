# Featured Articles Implementation Plan

## Goal
Add ability to mark articles as "featured" so they appear in the "Getting Started" (Pour commencer) section on the homepage.

## Design Decisions

### 1. Database: `featured` column on articles table
- INTEGER (0/1) in SQLite, boolean in TypeScript
- Same pattern as `peer_reviewed` and `open_access`
- Add `featured_at` timestamp for ordering (most recently featured first)

### 2. Where featured articles appear
- **Homepage**: In the "Pour commencer / Getting started" section
- **All Articles page**: Featured filter + featured badge on cards
- ICP pages (Professionals, Families) left unchanged

### 3. Admin UI
- **Article detail page** (`/admin/articles/[id]`): Toggle button to feature/unfeature
- **Article list page** (`/admin/articles`):
  - Star icon in table for featured articles
  - Checkbox filter "Featured" (like "Has flags")

---

## Implementation Steps

### Phase 1: Database Schema

**File: `mcp_server/database.py`**

Add to `_migrate_article_columns()`:
```python
("featured", "ALTER TABLE articles ADD COLUMN featured INTEGER DEFAULT 0"),
("featured_at", "ALTER TABLE articles ADD COLUMN featured_at TEXT"),
```

**File: `site/src/lib/db.ts`**

Add to `Article` interface:
```typescript
featured: boolean;
featured_at: string | null;
```

Add query function:
```typescript
export function getFeaturedArticles(lang: Language, limit = 4): ArticleWithTranslation[]
```

Update `getAdminArticles()` to accept `featured` filter param.

### Phase 2: Admin UI

**File: `site/src/pages/api/toggle-feature.ts`** (new)
- POST endpoint: `{ articleId: string, featured: boolean }`
- Opens write-access DB connection
- Updates `featured` (0/1) and `featured_at` (timestamp or null)
- Returns JSON success/error

**File: `site/src/pages/admin/articles/[id].astro`**
- Add "Feature on Homepage" / "Remove from Homepage" button
- Same pattern as "Mark as Reviewed" button
- Only show for translated articles

**File: `site/src/pages/admin/articles/index.astro`**
- Add star icon (★) in Title column for featured articles
- Add "Featured" checkbox filter (like "Has flags")

### Phase 3: Public Site

**File: `site/src/pages/[lang]/articles/index.astro`** (new)
- New "All Articles" page for visitors
- Layout based on homepage-mockup-b.html:
  1. **Featured strip** (white band at top): horizontal scrolling compact cards
     - Compact cards: author/year, one-line description, no badges
     - "Nouveau sur le PDA ? Commencez ici →" heading
  2. **Main content**: two-column layout
     - Left: article list (full ArticleCard components, vertical stack)
     - Right sidebar: filters
       - Categories (links with counts)
       - Type checkboxes (Peer-reviewed, Open-access)
       - Method badges (clickable pills)
- URL params: `/fr/articles?category=fondements&method=empirical&peerReviewed=true`

**File: `site/src/pages/[lang]/index.astro`**
- Update "Pour commencer / Getting started" section
- Query `getFeaturedArticles(lang, 4)`
- Display as ArticleCard grid
- If no featured articles, keep existing placeholder content

**File: `site/src/components/FeaturedArticleCard.astro`** (new)
- Compact card for featured strip, strictly following design-system.html:
  - Container: `px-4 py-3 rounded-lg border border-stone-200`
  - Hover: `hover:border-primary-300 hover:bg-primary-50/30 transition-colors`
  - Title: `font-medium text-sm text-stone-900` (Lexend, authors + year)
  - Description: `text-xs text-stone-500` (one line)
  - No badges, no Literata (too small for serif)
- Must use existing color tokens (primary, stone, accent) — no new colors

**File: `site/src/components/ArticleCard.astro`**
- No changes needed (featured articles use separate compact component)

### Phase 4: Translations

**File: `site/src/i18n/translations.ts`**
- Add `admin.featureArticle`: "Feature on Homepage" / "Mettre en avant"
- Add `admin.unfeatureArticle`: "Remove from Homepage" / "Retirer de la une"

---

## Files to Modify

| File | Change |
|------|--------|
| `mcp_server/database.py` | Add migration for `featured` and `featured_at` columns |
| `site/src/lib/db.ts` | Add `featured` to interface, add `getFeaturedArticles()`, add `getFilteredArticles()` |
| `site/src/pages/api/toggle-feature.ts` | **New file** — API endpoint for toggling |
| `site/src/pages/admin/articles/[id].astro` | Add feature toggle button |
| `site/src/pages/admin/articles/index.astro` | Add star indicator + filter checkbox |
| `site/src/pages/[lang]/articles/index.astro` | **New file** — All Articles page with featured strip + filters |
| `site/src/components/FeaturedArticleCard.astro` | **New file** — Compact card for featured strip |
| `site/src/pages/[lang]/index.astro` | Display featured articles in "Getting started" section |
| `site/src/i18n/translations.ts` | Add UI strings (featured, filters, etc.) |
