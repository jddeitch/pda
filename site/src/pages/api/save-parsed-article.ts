import type { APIRoute } from "astro";
import Database from "better-sqlite3";
import path from "path";

export const prerender = false;

const dbPath = path.resolve(process.cwd(), "../data/pda.db");

interface SaveParsedArticleBody {
  slug: string;
  title: string;
  authors: string;
  year: string;
  citation: string;
  abstract: string;
  body_html: string;
  references_json: string[];
  acknowledgements: string;
  raw_html: string;
  method: string;
  voice: string;
  peer_reviewed: boolean;
}

export const POST: APIRoute = async ({ request }) => {
  // Check admin access
  if (!import.meta.env.ENABLE_ADMIN) {
    return new Response(JSON.stringify({ error: "Admin access disabled" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const body: SaveParsedArticleBody = await request.json();
    const {
      slug,
      title,
      authors,
      year,
      citation,
      abstract,
      body_html,
      references_json,
      acknowledgements,
      raw_html,
      method,
      voice,
      peer_reviewed,
    } = body;

    if (!slug) {
      return new Response(
        JSON.stringify({ error: "slug is required" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    if (!title) {
      return new Response(
        JSON.stringify({ error: "title is required" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    // Open database with write access
    const db = new Database(dbPath, { readonly: false });

    // Check if article already exists
    const existing = db.prepare("SELECT id FROM articles WHERE id = ?").get(slug);

    if (existing) {
      // Update existing article
      db.prepare(`
        UPDATE articles
        SET
          source_title = ?,
          authors = ?,
          year = ?,
          citation = ?,
          abstract = ?,
          body_html = ?,
          references_json = ?,
          acknowledgements = ?,
          raw_html = ?,
          method = ?,
          voice = ?,
          peer_reviewed = ?,
          processing_status = 'pending',
          updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
      `).run(
        title,
        authors || null,
        year || null,
        citation || null,
        abstract || null,
        body_html || null,
        JSON.stringify(references_json || []),
        acknowledgements || null,
        raw_html || null,
        method || null,
        voice || null,
        peer_reviewed ? 1 : 0,
        slug
      );

      db.close();

      return new Response(
        JSON.stringify({ success: true, action: "updated", articleId: slug }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    } else {
      // Insert new article
      db.prepare(`
        INSERT INTO articles (
          id,
          source_title,
          authors,
          year,
          citation,
          abstract,
          body_html,
          references_json,
          acknowledgements,
          raw_html,
          method,
          voice,
          peer_reviewed,
          source,
          processing_status,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datalab', 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
      `).run(
        slug,
        title,
        authors || null,
        year || null,
        citation || null,
        abstract || null,
        body_html || null,
        JSON.stringify(references_json || []),
        acknowledgements || null,
        raw_html || null,
        method || null,
        voice || null,
        peer_reviewed ? 1 : 0
      );

      db.close();

      return new Response(
        JSON.stringify({ success: true, action: "created", articleId: slug }),
        { status: 201, headers: { "Content-Type": "application/json" } }
      );
    }

  } catch (error) {
    console.error("Error saving parsed article:", error);
    return new Response(
      JSON.stringify({
        error: "Failed to save article",
        details: error instanceof Error ? error.message : "Unknown error",
      }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
};
