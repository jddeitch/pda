import type { APIRoute } from "astro";
import { readFileSync, existsSync } from "fs";
import path from "path";
import Database from "better-sqlite3";

export const prerender = false;

const dbPath = path.resolve(process.cwd(), "../data/pda.db");

interface BatchJob {
  id: string;
  job_type: string;
  status: string;
  target_count: number;
  processed_count: number;
  current_article: string | null;
  started_at: string | null;
  completed_at: string | null;
  pid: number | null;
  error_message: string | null;
  log_path: string | null;
  created_at: string;
}

interface BatchJobEvent {
  id: number;
  job_id: string;
  event_type: string;
  article_slug: string | null;
  message: string | null;
  timestamp: string;
}

export const GET: APIRoute = async ({ url }) => {
  // Check admin access
  if (!import.meta.env.ENABLE_ADMIN) {
    return new Response(JSON.stringify({ error: "Admin access disabled" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }

  const jobId = url.searchParams.get("jobId");

  try {
    const db = new Database(dbPath, { readonly: true });

    // If no jobId provided, return the running job (if any) or most recent
    let job: BatchJob | undefined;

    if (jobId) {
      job = db
        .prepare("SELECT * FROM batch_jobs WHERE id = ?")
        .get(jobId) as BatchJob | undefined;
    } else {
      // Try to get running job first, otherwise most recent
      job = db
        .prepare(
          "SELECT * FROM batch_jobs WHERE status = 'running' LIMIT 1"
        )
        .get() as BatchJob | undefined;

      if (!job) {
        job = db
          .prepare(
            "SELECT * FROM batch_jobs ORDER BY created_at DESC LIMIT 1"
          )
          .get() as BatchJob | undefined;
      }
    }

    if (!job) {
      db.close();
      return new Response(
        JSON.stringify({
          error: "Job not found",
          hasJob: false,
        }),
        {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Get recent events for this job
    const events = db
      .prepare(
        `
      SELECT * FROM batch_job_events
      WHERE job_id = ?
      ORDER BY timestamp DESC
      LIMIT 50
    `
      )
      .all(job.id) as BatchJobEvent[];

    // Get recent batch jobs for history
    const recentJobs = db
      .prepare(
        `
      SELECT id, job_type, status, target_count, processed_count, created_at, completed_at
      FROM batch_jobs
      ORDER BY created_at DESC
      LIMIT 10
    `
      )
      .all() as Partial<BatchJob>[];

    db.close();

    // Read last 50 lines of log file if it exists
    let logTail: string[] = [];
    if (job.log_path && existsSync(job.log_path)) {
      try {
        const logContent = readFileSync(job.log_path, "utf-8");
        const lines = logContent.split("\n").filter((line) => line.trim());
        logTail = lines.slice(-50);
      } catch {
        // Ignore log read errors
      }
    }

    return new Response(
      JSON.stringify({
        job: {
          id: job.id,
          jobType: job.job_type,
          status: job.status,
          targetCount: job.target_count,
          processedCount: job.processed_count,
          currentArticle: job.current_article,
          startedAt: job.started_at,
          completedAt: job.completed_at,
          errorMessage: job.error_message,
          createdAt: job.created_at,
        },
        events: events.map((e) => ({
          id: e.id,
          eventType: e.event_type,
          articleSlug: e.article_slug,
          message: e.message,
          timestamp: e.timestamp,
        })),
        recentJobs: recentJobs.map((j) => ({
          id: j.id,
          jobType: j.job_type,
          status: j.status,
          targetCount: j.target_count,
          processedCount: j.processed_count,
          createdAt: j.created_at,
          completedAt: j.completed_at,
        })),
        logTail,
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }
    );
  } catch (error) {
    console.error("Error getting batch status:", error);
    return new Response(
      JSON.stringify({
        error: "Failed to get batch status",
        details: error instanceof Error ? error.message : "Unknown error",
      }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
};
