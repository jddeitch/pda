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
    const { articleId, field, value } = body;

    if (!articleId) {
      return new Response(
        JSON.stringify({ error: "articleId is required" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    if (!field) {
      return new Response(
        JSON.stringify({ error: "field is required" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    // Whitelist of editable fields
    const editableFields = ["source_url"];
    if (!editableFields.includes(field)) {
      return new Response(
        JSON.stringify({ error: `Field '${field}' is not editable` }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    // Validate URL if updating source_url
    if (field === "source_url" && value) {
      try {
        new URL(value);
      } catch {
        return new Response(
          JSON.stringify({ error: "Invalid URL format" }),
          { status: 400, headers: { "Content-Type": "application/json" } }
        );
      }
    }

    // Open database with write access
    const db = new Database(dbPath, { readonly: false });

    // Update the field
    const result = db.prepare(`
      UPDATE articles
      SET ${field} = ?
      WHERE id = ?
    `).run(value || null, articleId);

    db.close();

    if (result.changes === 0) {
      return new Response(
        JSON.stringify({ error: "Article not found" }),
        { status: 404, headers: { "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({ success: true, articleId, field, value }),
      { status: 200, headers: { "Content-Type": "application/json" } }
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
