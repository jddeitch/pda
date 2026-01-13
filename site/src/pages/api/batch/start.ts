import type { APIRoute } from "astro";
import { spawn } from "child_process";
import { randomUUID } from "crypto";
import path from "path";
import Database from "better-sqlite3";

export const prerender = false;

const dbPath = path.resolve(process.cwd(), "../data/pda.db");
const projectRoot = path.resolve(process.cwd(), "..");
const batchRunnerPath = path.join(projectRoot, "scripts", "batch_runner.py");
const logDir = path.join(projectRoot, "logs", "batch");

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
    const { jobType, count } = body as { jobType: string; count: number };

    // Validate inputs
    if (!jobType || !["preprocessing", "translation"].includes(jobType)) {
      return new Response(
        JSON.stringify({
          error: "Invalid job type",
          details: "jobType must be 'preprocessing' or 'translation'",
        }),
        {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    if (!count || count < 1 || count > 50) {
      return new Response(
        JSON.stringify({
          error: "Invalid count",
          details: "count must be between 1 and 50",
        }),
        {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Check for existing running job
    const db = new Database(dbPath, { readonly: true });
    const runningJob = db
      .prepare("SELECT id FROM batch_jobs WHERE status = 'running' LIMIT 1")
      .get() as { id: string } | undefined;
    db.close();

    if (runningJob) {
      return new Response(
        JSON.stringify({
          error: "Job already running",
          details: `A batch job is already running (ID: ${runningJob.id})`,
          existingJobId: runningJob.id,
        }),
        {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Generate job ID
    const jobId = randomUUID();
    const logPath = path.join(logDir, `${jobId}.log`);

    // Create job record in database
    const dbWrite = new Database(dbPath, { readonly: false });
    dbWrite.exec("PRAGMA foreign_keys = ON");
    dbWrite
      .prepare(
        `
      INSERT INTO batch_jobs (id, job_type, status, target_count, log_path, created_at)
      VALUES (?, ?, 'pending', ?, ?, datetime('now', 'localtime'))
    `
      )
      .run(jobId, jobType, count, logPath);
    dbWrite.close();

    // Spawn the batch runner as a detached process
    // The batch runner will daemonize itself, so we just need to start it
    const child = spawn(
      "/opt/homebrew/bin/python3.11",
      [
        batchRunnerPath,
        "--job-type",
        jobType,
        "--count",
        count.toString(),
        "--job-id",
        jobId,
      ],
      {
        cwd: projectRoot,
        detached: true,
        stdio: "ignore",
      }
    );

    // Don't wait for the child - let it run independently
    child.unref();

    return new Response(
      JSON.stringify({
        success: true,
        jobId,
        jobType,
        count,
        message: `Batch ${jobType} job started`,
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }
    );
  } catch (error) {
    console.error("Error starting batch job:", error);
    return new Response(
      JSON.stringify({
        error: "Failed to start batch job",
        details: error instanceof Error ? error.message : "Unknown error",
      }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
};
