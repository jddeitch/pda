import type { APIRoute } from "astro";
import Database from "better-sqlite3";
import path from "path";

export const prerender = false;

const dbPath = path.resolve(process.cwd(), "../data/pda.db");

export const POST: APIRoute = async ({ request }) => {
  // Check admin access
  if (!import.meta.env.ENABLE_ADMIN) {
    return new Response(JSON.stringify({ error: "Admin access disabled" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const body = await request.json();
    const { articleId, action } = body;

    if (!articleId) {
      return new Response(
        JSON.stringify({ error: "articleId is required" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    // Open database with write access
    const db = new Database(dbPath, { readonly: false });

    if (action === "mark_reviewed") {
      // Clear processing_flags and add a note that it was reviewed
      const result = db.prepare(`
        UPDATE articles
        SET processing_flags = '[]',
            processing_notes = CASE
              WHEN processing_notes IS NULL OR processing_notes = '' THEN '[REVIEWED] Manually reviewed and cleared'
              ELSE processing_notes || '; [REVIEWED] Manually reviewed and cleared'
            END
        WHERE id = ?
      `).run(articleId);

      db.close();

      if (result.changes === 0) {
        return new Response(
          JSON.stringify({ error: "Article not found" }),
          { status: 404, headers: { "Content-Type": "application/json" } }
        );
      }

      return new Response(
        JSON.stringify({ success: true, articleId, action: "marked_reviewed" }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }

    if (action === "reset_to_pending") {
      // Reset article to pending status for re-translation
      const result = db.prepare(`
        UPDATE articles
        SET processing_status = 'pending',
            processing_flags = '[]',
            processing_notes = CASE
              WHEN processing_notes IS NULL OR processing_notes = '' THEN '[RESET] Reset to pending for re-translation'
              ELSE processing_notes || '; [RESET] Reset to pending for re-translation'
            END,
            processed_at = NULL
        WHERE id = ?
      `).run(articleId);

      db.close();

      if (result.changes === 0) {
        return new Response(
          JSON.stringify({ error: "Article not found" }),
          { status: 404, headers: { "Content-Type": "application/json" } }
        );
      }

      return new Response(
        JSON.stringify({ success: true, articleId, action: "reset_to_pending" }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }

    db.close();

    return new Response(
      JSON.stringify({ error: "Invalid action. Use 'mark_reviewed' or 'reset_to_pending'" }),
      { status: 400, headers: { "Content-Type": "application/json" } }
    );

  } catch (error) {
    console.error("Error updating article:", error);
    return new Response(
      JSON.stringify({
        error: "Failed to update article",
        details: error instanceof Error ? error.message : "Unknown error",
      }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
};
