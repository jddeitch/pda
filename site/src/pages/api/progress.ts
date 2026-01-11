import type { APIRoute } from "astro";
import {
  getAdminStats,
  getSessionState,
  getFlaggedArticles,
  getRecentlyCompleted,
} from "../../lib/db";

export const prerender = false;

export const GET: APIRoute = async () => {
  // Check admin access
  if (!import.meta.env.ENABLE_ADMIN) {
    return new Response(JSON.stringify({ error: "Admin access disabled" }), {
      status: 403,
      headers: {
        "Content-Type": "application/json",
      },
    });
  }

  try {
    const stats = getAdminStats();
    const session = getSessionState();
    const flagged = getFlaggedArticles(10);
    const recent = getRecentlyCompleted(5);

    // Calculate progress percentage
    const progressPercent =
      stats.total > 0 ? Math.round((stats.translated / stats.total) * 100) : 0;

    return new Response(
      JSON.stringify({
        stats,
        session,
        flagged,
        recent,
        progressPercent,
        timestamp: new Date().toISOString(),
      }),
      {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-cache, no-store, must-revalidate",
        },
      }
    );
  } catch (error) {
    console.error("Error fetching progress:", error);
    return new Response(
      JSON.stringify({
        error: "Failed to fetch progress data",
        details: error instanceof Error ? error.message : "Unknown error",
      }),
      {
        status: 500,
        headers: {
          "Content-Type": "application/json",
        },
      }
    );
  }
};
