#!/usr/bin/env python3
"""
Autonomous batch processor for PDA preprocessing and translation.

This script runs Claude CLI autonomously to process multiple articles without
requiring interactive permission approvals. It:

1. Daemonizes itself (detaches from parent process)
2. Updates batch_jobs table with status
3. Runs Claude CLI with --print --dangerously-skip-permissions
4. Monitors output for progress markers
5. Updates database as articles complete

Usage:
  python batch_runner.py --job-type preprocessing --count 5 --job-id abc123
  python batch_runner.py --job-type translation --count 10 --job-id def456

Options:
  --no-daemon    Don't daemonize (for testing/debugging)
  --verbose      Print output to console as well as log file
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.database import Database

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pda.db"
LOG_DIR = PROJECT_ROOT / "logs" / "batch"
MCP_CONFIG = PROJECT_ROOT / "batch-mcp-config.json"


def daemonize():
    """
    Double-fork to detach from parent process.

    This allows the batch job to continue running even after the API
    endpoint that spawned it has returned.
    """
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent exits
        sys.exit(0)

    # Create new session
    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        # First child exits
        sys.exit(0)

    # Now we're the grandchild, fully detached
    # Redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()

    # Close inherited file descriptors
    os.close(0)
    os.close(1)
    os.close(2)

    # Reopen to /dev/null
    os.open('/dev/null', os.O_RDWR)
    os.dup2(0, 1)
    os.dup2(0, 2)


def build_preprocessing_prompt(count: int) -> str:
    """Build the Claude prompt for preprocessing batch."""
    return f"""You are running in autonomous batch mode. Process up to {count} articles through preprocessing.

CRITICAL: Output progress markers EXACTLY as shown below. The batch runner parses these to track progress.

WORKFLOW:
1. Call start_preprocessing() to see available files
2. If there are datalab-output-*.json files, call parse_datalab_file() for one
3. After parsing, follow step4_check_* and step4_confirm_* tools in sequence:
   - step4_check_fields() then step4_confirm_fields()
   - step4_check_warnings() then step4_confirm_warnings()
   - step4_check_references() then step4_confirm_references()
   - step4_check_formulas() then step4_confirm_formulas()
4. Call step4_complete() when all checks pass
5. Output: ARTICLE_COMPLETE: {{slug}}
6. Repeat for next article until {count} done or no more work

OUTPUT MARKERS (output these EXACTLY):
- Starting an article: ARTICLE_START: {{slug}}
- Finished an article: ARTICLE_COMPLETE: {{slug}}
- Error on an article: ARTICLE_ERROR: {{slug}} - {{reason}}
- All done: BATCH_COMPLETE

RULES:
- Do NOT ask questions. Make reasonable decisions.
- If a field is missing and cannot be determined, leave it empty.
- If an article has blocking issues, skip it with ARTICLE_ERROR.
- When no more files to process, output BATCH_COMPLETE and stop."""


def build_translation_prompt(count: int) -> str:
    """Build the Claude prompt for translation batch."""
    return f"""You are running in autonomous batch mode. Translate up to {count} articles to French.

CRITICAL: Output progress markers EXACTLY as shown below. The batch runner parses these to track progress.

WORKFLOW:
1. Call get_next_article()
2. If response status is SESSION_PAUSE or COMPLETE, output BATCH_COMPLETE and stop
3. Output: ARTICLE_START: {{article_id}}
4. Translate title and summary (always)
5. If open_access is true:
   - Loop: call get_chunk(article_id, chunk_number) starting at 1
   - Translate each chunk, accumulate translations
   - Continue until response.complete is true
6. Call validate_classification() with your classification
7. Call save_article() with the validation token and translations
8. Output: ARTICLE_COMPLETE: {{article_id}}
9. Repeat until {count} done or SESSION_PAUSE

OUTPUT MARKERS (output these EXACTLY):
- Starting an article: ARTICLE_START: {{article_id}}
- Finished an article: ARTICLE_COMPLETE: {{article_id}}
- Error on an article: ARTICLE_ERROR: {{article_id}} - {{reason}}
- All done: BATCH_COMPLETE

RULES:
- Do NOT ask questions. Make reasonable decisions.
- Use glossary terms provided in each chunk response.
- If quality checks block save, try to fix once, then skip with ARTICLE_ERROR.
- Respect the SESSION_PAUSE signal - don't try to continue past it."""


class BatchRunner:
    """Runs Claude CLI and monitors output for progress."""

    def __init__(self, job_id: str, job_type: str, count: int, verbose: bool = False):
        self.job_id = job_id
        self.job_type = job_type
        self.count = count
        self.verbose = verbose
        self.db = Database(DB_PATH)
        self.process: subprocess.Popen | None = None
        self._stop_requested = False

    def setup_signal_handlers(self):
        """Set up handlers for graceful shutdown."""
        def handle_signal(signum, frame):
            self._stop_requested = True
            if self.process:
                self.process.terminate()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    def run(self) -> int:
        """Run the batch job. Returns exit code."""
        self.setup_signal_handlers()

        # Ensure log directory exists
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{self.job_id}.log"

        # Update status to running
        self.db.update_batch_job_status(
            self.job_id,
            "running",
            pid=os.getpid()
        )
        self.db.add_batch_job_event(
            self.job_id,
            "started",
            message=f"Starting {self.job_type} batch for {self.count} articles"
        )

        # Build prompt
        if self.job_type == "preprocessing":
            prompt = build_preprocessing_prompt(self.count)
        else:
            prompt = build_translation_prompt(self.count)

        # Build command
        # SECURITY: We use --dangerously-skip-permissions to avoid interactive prompts,
        # but we RESTRICT tools to only MCP calls. Claude cannot use Bash, Write, Edit etc.
        # The only tools available are the MCP server tools defined in batch-mcp-config.json.
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--mcp-config", str(MCP_CONFIG),
            "--model", "sonnet",
            "--tools", "",  # Disable ALL built-in tools (Bash, Write, Edit, etc.)
            prompt
        ]

        try:
            with open(log_path, "w") as log_file:
                # Start Claude process
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT),
                    text=True,
                    bufsize=1  # Line buffered
                )

                # Monitor output
                for line in self.process.stdout:
                    # Write to log
                    log_file.write(line)
                    log_file.flush()

                    if self.verbose:
                        print(line, end="")

                    # Parse for markers
                    self._parse_output_line(line)

                    if self._stop_requested:
                        break

                # Wait for process to complete
                exit_code = self.process.wait()

            # Update final status
            if self._stop_requested:
                self.db.update_batch_job_status(self.job_id, "cancelled")
                self.db.add_batch_job_event(
                    self.job_id, "cancelled",
                    message="Job cancelled by user"
                )
            elif exit_code == 0:
                self.db.update_batch_job_status(self.job_id, "completed")
                self.db.add_batch_job_event(
                    self.job_id, "completed",
                    message="Batch completed successfully"
                )
            else:
                self.db.update_batch_job_status(
                    self.job_id, "failed",
                    error_message=f"Claude exited with code {exit_code}"
                )
                self.db.add_batch_job_event(
                    self.job_id, "failed",
                    message=f"Claude process failed with exit code {exit_code}"
                )

            return exit_code

        except Exception as e:
            self.db.update_batch_job_status(
                self.job_id, "failed",
                error_message=str(e)
            )
            self.db.add_batch_job_event(
                self.job_id, "error",
                message=f"Exception: {e}"
            )
            return 1
        finally:
            self.db.close()

    def _parse_output_line(self, line: str):
        """Parse output line for progress markers."""
        line = line.strip()

        # ARTICLE_START: slug
        if match := re.match(r'ARTICLE_START:\s*(.+)', line):
            slug = match.group(1).strip()
            self.db.update_batch_job_status(
                self.job_id, "running",
                current_article=slug
            )
            self.db.add_batch_job_event(
                self.job_id, "article_start",
                article_slug=slug,
                message=f"Started processing {slug}"
            )

        # ARTICLE_COMPLETE: slug
        elif match := re.match(r'ARTICLE_COMPLETE:\s*(.+)', line):
            slug = match.group(1).strip()
            self.db.increment_batch_job_progress(self.job_id)
            self.db.add_batch_job_event(
                self.job_id, "article_complete",
                article_slug=slug,
                message=f"Completed {slug}"
            )

        # ARTICLE_ERROR: slug - reason
        elif match := re.match(r'ARTICLE_ERROR:\s*([^\-]+)\s*-\s*(.+)', line):
            slug = match.group(1).strip()
            reason = match.group(2).strip()
            self.db.add_batch_job_event(
                self.job_id, "article_error",
                article_slug=slug,
                message=f"Error: {reason}"
            )

        # BATCH_COMPLETE
        elif "BATCH_COMPLETE" in line:
            self.db.add_batch_job_event(
                self.job_id, "batch_complete",
                message="All articles processed"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous batch processor for PDA project"
    )
    parser.add_argument(
        "--job-type",
        required=True,
        choices=["preprocessing", "translation"],
        help="Type of batch job to run"
    )
    parser.add_argument(
        "--count",
        type=int,
        required=True,
        help="Number of articles to process"
    )
    parser.add_argument(
        "--job-id",
        required=True,
        help="Unique job ID for tracking"
    )
    parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="Don't daemonize (run in foreground for testing)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print output to console as well as log file"
    )
    args = parser.parse_args()

    # Validate MCP config exists
    if not MCP_CONFIG.exists():
        print(f"ERROR: MCP config not found at {MCP_CONFIG}", file=sys.stderr)
        print("Create batch-mcp-config.json with your MCP server settings.", file=sys.stderr)
        sys.exit(1)

    # Daemonize unless --no-daemon
    if not args.no_daemon:
        daemonize()

    # Run the batch job
    runner = BatchRunner(
        job_id=args.job_id,
        job_type=args.job_type,
        count=args.count,
        verbose=args.verbose or args.no_daemon
    )
    exit_code = runner.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
