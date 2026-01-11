import Database from "better-sqlite3";
import path from "path";

const dbPath = path.resolve(process.cwd(), "../data/pda.db");
const db = new Database(dbPath, { readonly: true });

// Types
export interface Category {
  id: string;
  label_fr: string;
  label_en: string;
  description: string;
  url_slug: string;
  priority: number;
}

export interface Article {
  id: string;
  source_language: string;
  source_title: string;
  source_url: string;
  authors: string;
  year: string;
  journal: string | null;
  doi: string | null;
  open_access: boolean;
  peer_reviewed: boolean;
  method: "empirical" | "synthesis" | "theoretical" | "lived_experience" | null;
  voice: "academic" | "practitioner" | "organization" | "individual" | null;
  summary_original: string;
}

export interface ArticleWithTranslation extends Article {
  translated_title: string | null;
  translated_summary: string | null;
  translated_full_text: string | null;
  translation_status: string;
  primary_category: string | null;
  keywords: string[];
}

export interface Translation {
  id: number;
  article_id: string;
  target_language: string;
  translated_title: string | null;
  translated_summary: string | null;
  translated_full_text: string | null;
  status: string;
  translator_notes: string | null;
}

// Category queries
export function getAllCategories(): Category[] {
  return db
    .prepare(
      `
    SELECT * FROM categories ORDER BY priority
  `
    )
    .all() as Category[];
}

export function getCategoryBySlug(slug: string): Category | undefined {
  return db
    .prepare(
      `
    SELECT * FROM categories WHERE url_slug = ?
  `
    )
    .get(slug) as Category | undefined;
}

// Article queries
export function getAllArticles(): Article[] {
  return db
    .prepare(
      `
    SELECT * FROM articles ORDER BY year DESC
  `
    )
    .all() as Article[];
}

export function getArticleById(id: string): Article | undefined {
  return db
    .prepare(
      `
    SELECT * FROM articles WHERE id = ?
  `
    )
    .get(id) as Article | undefined;
}

export function getArticleWithTranslation(
  id: string,
  lang: string = "fr"
): ArticleWithTranslation | undefined {
  const article = db
    .prepare(
      `
    SELECT
      a.*,
      t.translated_title,
      t.translated_summary,
      t.translated_full_text,
      t.status as translation_status,
      ac.category_id as primary_category
    FROM articles a
    LEFT JOIN translations t ON a.id = t.article_id AND t.target_language = ?
    LEFT JOIN article_categories ac ON a.id = ac.article_id AND ac.is_primary = 1
    WHERE a.id = ?
  `
    )
    .get(lang, id) as (ArticleWithTranslation & { keywords?: string }) | undefined;

  if (!article) return undefined;

  // Get keywords
  const keywords = db
    .prepare(
      `
    SELECT k.keyword FROM keywords k
    JOIN article_keywords ak ON k.id = ak.keyword_id
    WHERE ak.article_id = ?
  `
    )
    .all(id) as { keyword: string }[];

  return {
    ...article,
    keywords: keywords.map((k) => k.keyword),
  };
}

export function getArticlesByCategory(
  categoryId: string,
  lang: string = "fr"
): ArticleWithTranslation[] {
  const articles = db
    .prepare(
      `
    SELECT
      a.*,
      t.translated_title,
      t.translated_summary,
      t.status as translation_status
    FROM articles a
    JOIN article_categories ac ON a.id = ac.article_id
    LEFT JOIN translations t ON a.id = t.article_id AND t.target_language = ?
    WHERE ac.category_id = ?
    ORDER BY a.year DESC
  `
    )
    .all(lang, categoryId) as ArticleWithTranslation[];

  return articles.map((article) => ({
    ...article,
    keywords: [],
  }));
}

export function getTranslatedArticles(lang: string = "fr"): ArticleWithTranslation[] {
  const articles = db
    .prepare(
      `
    SELECT
      a.*,
      t.translated_title,
      t.translated_summary,
      t.status as translation_status,
      ac.category_id as primary_category
    FROM articles a
    JOIN translations t ON a.id = t.article_id
    LEFT JOIN article_categories ac ON a.id = ac.article_id AND ac.is_primary = 1
    WHERE t.target_language = ? AND t.status IN ('translated', 'reviewed')
    ORDER BY a.year DESC
  `
    )
    .all(lang) as ArticleWithTranslation[];

  return articles.map((article) => ({
    ...article,
    keywords: [],
  }));
}

// Stats
export function getStats() {
  const totalArticles = (
    db.prepare("SELECT COUNT(*) as count FROM articles").get() as { count: number }
  ).count;

  const translatedArticles = (
    db
      .prepare(
        "SELECT COUNT(*) as count FROM translations WHERE status IN ('translated', 'reviewed')"
      )
      .get() as { count: number }
  ).count;

  const pendingArticles = totalArticles - translatedArticles;

  return {
    total: totalArticles,
    translated: translatedArticles,
    pending: pendingArticles,
  };
}

// Search (for Pagefind, we'll generate static content, but this is useful for dev)
export function searchArticles(query: string, lang: string = "fr"): ArticleWithTranslation[] {
  const articles = db
    .prepare(
      `
    SELECT
      a.*,
      t.translated_title,
      t.translated_summary,
      t.status as translation_status
    FROM articles a
    LEFT JOIN translations t ON a.id = t.article_id AND t.target_language = ?
    WHERE
      a.source_title LIKE ? OR
      a.summary_original LIKE ? OR
      t.translated_title LIKE ? OR
      t.translated_summary LIKE ? OR
      a.authors LIKE ?
    ORDER BY a.year DESC
  `
    )
    .all(lang, `%${query}%`, `%${query}%`, `%${query}%`, `%${query}%`, `%${query}%`) as ArticleWithTranslation[];

  return articles.map((article) => ({
    ...article,
    keywords: [],
  }));
}

// =============================================================================
// Admin Interface Queries
// =============================================================================

export interface ProgressCount {
  processing_status: string;
  count: number;
}

export interface SessionState {
  id: number;
  articles_processed_count: number;
  human_review_interval: number;
  last_reset_at: string;
  last_reset_date: string;
}

export interface FlaggedArticle {
  id: string;
  source_title: string;
  processing_flags: string;
  processing_notes: string | null;
  processed_at: string | null;
}

export interface AdminArticle {
  id: string;
  source_title: string;
  source_url: string | null;
  authors: string | null;
  year: string | null;
  method: string | null;
  voice: string | null;
  peer_reviewed: number;
  open_access: number;
  processing_status: string;
  processing_flags: string;
  processing_notes: string | null;
  processed_at: string | null;
  translated_title: string | null;
  translated_summary: string | null;
  translation_status: string | null;
  primary_category: string | null;
}

export interface PreprocessingArticle {
  id: string;
  source_title: string;
  source_url: string | null;
  processing_flags: string;
  processing_notes: string | null;
}

// Get counts by processing_status
export function getProgress(): ProgressCount[] {
  return db
    .prepare(
      `
    SELECT processing_status, COUNT(*) as count
    FROM articles
    GROUP BY processing_status
  `
    )
    .all() as ProgressCount[];
}

// Get current session state
export function getSessionState(): SessionState | undefined {
  return db
    .prepare(
      `
    SELECT * FROM session_state WHERE id = 1
  `
    )
    .get() as SessionState | undefined;
}

// Get articles with flags that need review
export function getFlaggedArticles(limit: number = 20): FlaggedArticle[] {
  return db
    .prepare(
      `
    SELECT id, source_title, processing_flags, processing_notes, processed_at
    FROM articles
    WHERE processing_status = 'translated'
      AND json_array_length(processing_flags) > 0
    ORDER BY processed_at DESC
    LIMIT ?
  `
    )
    .all(limit) as FlaggedArticle[];
}

// Get recently completed articles
export function getRecentlyCompleted(limit: number = 10): FlaggedArticle[] {
  return db
    .prepare(
      `
    SELECT id, source_title, processing_flags, processing_notes, processed_at
    FROM articles
    WHERE processing_status = 'translated'
    ORDER BY processed_at DESC
    LIMIT ?
  `
    )
    .all(limit) as FlaggedArticle[];
}

// Get all articles for admin list with optional filters
export function getAdminArticles(filters?: {
  status?: string;
  hasFlags?: boolean;
  category?: string;
  method?: string;
  voice?: string;
}): AdminArticle[] {
  let query = `
    SELECT
      a.id,
      a.source_title,
      a.source_url,
      a.authors,
      a.year,
      a.method,
      a.voice,
      a.peer_reviewed,
      a.open_access,
      a.processing_status,
      a.processing_flags,
      a.processing_notes,
      a.processed_at,
      t.translated_title,
      t.translated_summary,
      t.status as translation_status,
      ac.category_id as primary_category
    FROM articles a
    LEFT JOIN translations t ON a.id = t.article_id AND t.target_language = 'fr'
    LEFT JOIN article_categories ac ON a.id = ac.article_id AND ac.is_primary = 1
  `;

  const conditions: string[] = [];
  const params: (string | number)[] = [];

  if (filters?.status) {
    conditions.push("a.processing_status = ?");
    params.push(filters.status);
  }

  if (filters?.hasFlags) {
    conditions.push("json_array_length(a.processing_flags) > 0");
  }

  if (filters?.category) {
    conditions.push("ac.category_id = ?");
    params.push(filters.category);
  }

  if (filters?.method) {
    conditions.push("a.method = ?");
    params.push(filters.method);
  }

  if (filters?.voice) {
    conditions.push("a.voice = ?");
    params.push(filters.voice);
  }

  if (conditions.length > 0) {
    query += " WHERE " + conditions.join(" AND ");
  }

  query += " ORDER BY a.processed_at DESC NULLS LAST, a.source_title";

  return db.prepare(query).all(...params) as AdminArticle[];
}

// Get single article for admin review (with full details)
export function getAdminArticleById(id: string): AdminArticle | undefined {
  return db
    .prepare(
      `
    SELECT
      a.id,
      a.source_title,
      a.source_url,
      a.authors,
      a.year,
      a.method,
      a.voice,
      a.peer_reviewed,
      a.open_access,
      a.processing_status,
      a.processing_flags,
      a.processing_notes,
      a.processed_at,
      t.translated_title,
      t.translated_summary,
      t.status as translation_status,
      ac.category_id as primary_category
    FROM articles a
    LEFT JOIN translations t ON a.id = t.article_id AND t.target_language = 'fr'
    LEFT JOIN article_categories ac ON a.id = ac.article_id AND ac.is_primary = 1
    WHERE a.id = ?
  `
    )
    .get(id) as AdminArticle | undefined;
}

// Get full translation for side-by-side review
export function getTranslationForReview(
  articleId: string
): Translation | undefined {
  return db
    .prepare(
      `
    SELECT * FROM translations
    WHERE article_id = ? AND target_language = 'fr'
  `
    )
    .get(articleId) as Translation | undefined;
}

// Get articles needing PDF preprocessing
export function getPreprocessingQueue(): PreprocessingArticle[] {
  return db
    .prepare(
      `
    SELECT id, source_title, source_url, processing_flags, processing_notes
    FROM articles
    WHERE processing_status = 'skipped'
      AND json_extract(processing_flags, '$') LIKE '%PDFEXTRACT%'
    ORDER BY source_title
  `
    )
    .all() as PreprocessingArticle[];
}

// Get summary stats for admin dashboard
export function getAdminStats(): {
  total: number;
  pending: number;
  in_progress: number;
  translated: number;
  skipped: number;
  flagged: number;
} {
  const counts = getProgress();
  const countMap: Record<string, number> = {};
  for (const row of counts) {
    countMap[row.processing_status] = row.count;
  }

  const flaggedCount = (
    db
      .prepare(
        `
    SELECT COUNT(*) as count FROM articles
    WHERE processing_status = 'translated'
      AND json_array_length(processing_flags) > 0
  `
      )
      .get() as { count: number }
  ).count;

  return {
    total: Object.values(countMap).reduce((a, b) => a + b, 0),
    pending: countMap["pending"] || 0,
    in_progress: countMap["in_progress"] || 0,
    translated: countMap["translated"] || 0,
    skipped: countMap["skipped"] || 0,
    flagged: flaggedCount,
  };
}
