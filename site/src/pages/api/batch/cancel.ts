import type { APIRoute } from "astro";
import path from "path";
import Database from "better-sqlite3";

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
    const { jobId } = body as { jobId: string };

    if (!jobId) {
      return new Response(
        JSON.stringify({
          error: "Missing jobId",
          details: "jobId is required",
        }),
        {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    const db = new Database(dbPath, { readonly: false });
    db.exec("PRAGMA foreign_keys = ON");

    // Get the job
    const job = db
      .prepare("SELECT * FROM batch_jobs WHERE id = ?")
      .get(jobId) as {
      id: string;
      status: string;
      pid: number | null;
    } | undefined;

    if (!job) {
      db.close();
      return new Response(
        JSON.stringify({
          error: "Job not found",
          details: `No job with ID ${jobId}`,
        }),
        {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Check if job is running
    if (job.status !== "running") {
      db.close();
      return new Response(
        JSON.stringify({
          error: "Job not running",
          details: `Job status is '${job.status}', cannot cancel`,
        }),
        {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Try to kill the process if we have a PID
    let processKilled = false;
    if (job.pid) {
      try {
        process.kill(job.pid, "SIGTERM");
        processKilled = true;
      } catch (err) {
        // Process may have already exited
        console.log(`Could not kill process ${job.pid}:`, err);
      }
    }

    // Update job status to cancelled
    db.prepare(
      `
      UPDATE batch_jobs
      SET status = 'cancelled',
          completed_at = datetime('now', 'localtime')
      WHERE id = ?
    `
    ).run(jobId);

    // Add cancellation event
    db.prepare(
      `
      INSERT INTO batch_job_events (job_id, event_type, message)
      VALUES (?, 'cancelled', ?)
    `
    ).run(jobId, processKilled ? "Job cancelled by user" : "Job cancelled (process may have already exited)");

    db.close();

    return new Response(
      JSON.stringify({
        success: true,
        jobId,
        processKilled,
        message: "Job cancelled",
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }
    );
  } catch (error) {
    console.error("Error cancelling batch job:", error);
    return new Response(
      JSON.stringify({
        error: "Failed to cancel batch job",
        details: error instanceof Error ? error.message : "Unknown error",
      }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
};
