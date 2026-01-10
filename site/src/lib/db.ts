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
