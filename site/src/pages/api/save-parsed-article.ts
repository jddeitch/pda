import type { APIRoute } from "astro";
import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";

export const prerender = false;

const PROJECT_ROOT = "/Users/jd/Projects/pda";
const dbPath = path.join(PROJECT_ROOT, "data/pda.db");
const READY_DIR = path.join(PROJECT_ROOT, "cache/articles/ready");
const ARCHIVED_DIR = path.join(PROJECT_ROOT, "cache/articles/archived");
const CACHE_DIR = path.join(PROJECT_ROOT, "cache/articles");

interface ApproveBody {
  action: "approve";
  slug: string;
  title: string;
  authors: string;
  year: string;
  citation: string;
  abstract: string;
  body_html: string;
  references_json: string[];
  doi: string;
  method: string;
  voice: string;
  peer_reviewed: boolean;
  open_access: boolean;
  source_language: string;
  source_url: string;
}

interface RejectBody {
  action: "reject";
  slug: string;
}

type RequestBody = ApproveBody | RejectBody;

export const POST: APIRoute = async ({ request }) => {
  // Check admin access
  if (!import.meta.env.ENABLE_ADMIN) {
    return new Response(JSON.stringify({ error: "Admin access disabled" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const body: RequestBody = await request.json();

    if (!body.slug) {
      return new Response(
        JSON.stringify({ error: "slug is required" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    const readyPath = path.join(READY_DIR, `${body.slug}_parsed.json`);

    // Check file exists in ready dir
    if (!fs.existsSync(readyPath)) {
      return new Response(
        JSON.stringify({ error: "Article not found in ready queue" }),
        { status: 404, headers: { "Content-Type": "application/json" } }
      );
    }

    // Handle REJECT action
    if (body.action === "reject") {
      // Ensure cache dir exists
      if (!fs.existsSync(CACHE_DIR)) {
        fs.mkdirSync(CACHE_DIR, { recursive: true });
      }

      // Move file back to main cache for rework
      const reworkPath = path.join(CACHE_DIR, `${body.slug}_parsed.json`);
      fs.renameSync(readyPath, reworkPath);

      return new Response(
        JSON.stringify({
          success: true,
          action: "rejected",
          slug: body.slug,
          message: "Article moved back for rework",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }

    // Handle APPROVE action
    if (body.action === "approve") {
      const {
        slug,
        title,
        authors,
        year,
        citation,
        abstract,
        body_html,
        references_json,
        doi,
        method,
        voice,
        peer_reviewed,
        open_access,
        source_language,
        source_url,
      } = body as ApproveBody;

      if (!title) {
        return new Response(
          JSON.stringify({ error: "title is required" }),
          { status: 400, headers: { "Content-Type": "application/json" } }
        );
      }

      // Open database with write access
      const db = new Database(dbPath, { readonly: false });

      // Enable foreign keys
      db.exec("PRAGMA foreign_keys = ON");

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
            doi = ?,
            source_url = ?,
            source_language = ?,
            open_access = ?,
            peer_reviewed = ?,
            method = ?,
            voice = ?,
            source = 'datalab',
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
          references_json ? JSON.stringify(references_json) : null,
          doi || null,
          source_url || null,
          source_language || 'en',
          open_access ? 1 : 0,
          peer_reviewed ? 1 : 0,
          method || null,
          voice || null,
          slug
        );

        db.close();
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
            doi,
            source_url,
            source_language,
            open_access,
            peer_reviewed,
            method,
            voice,
            source,
            processing_status,
            created_at,
            updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datalab', 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        `).run(
          slug,
          title,
          authors || null,
          year || null,
          citation || null,
          abstract || null,
          body_html || null,
          references_json ? JSON.stringify(references_json) : null,
          doi || null,
          source_url || null,
          source_language || 'en',
          open_access ? 1 : 0,
          peer_reviewed ? 1 : 0,
          method || null,
          voice || null
        );

        db.close();
      }

      // Ensure archived dir exists
      if (!fs.existsSync(ARCHIVED_DIR)) {
        fs.mkdirSync(ARCHIVED_DIR, { recursive: true });
      }

      // Move file to archived
      const archivedPath = path.join(ARCHIVED_DIR, `${slug}_parsed.json`);
      fs.renameSync(readyPath, archivedPath);

      return new Response(
        JSON.stringify({
          success: true,
          action: existing ? "updated" : "created",
          articleId: slug,
        }),
        { status: existing ? 200 : 201, headers: { "Content-Type": "application/json" } }
      );
    }

    // Unknown action
    return new Response(
      JSON.stringify({ error: "Invalid action. Use 'approve' or 'reject'" }),
      { status: 400, headers: { "Content-Type": "application/json" } }
    );

  } catch (error) {
    console.error("Error processing article:", error);
    return new Response(
      JSON.stringify({
        error: "Failed to process article",
        details: error instanceof Error ? error.message : "Unknown error",
      }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
};
