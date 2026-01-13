#!/bin/bash
#
# pda.sh — Control script for PDA Translation Machine
#
# Usage:
#   ./pda.sh dev         Start Astro dev server with admin enabled
#   ./pda.sh log [N]     Show last N MCP log entries (default: 50)
#   ./pda.sh logs        Tail the MCP log file (live)
#   ./pda.sh clear       Clear the MCP log file
#   ./pda.sh test        Test if MCP server module loads correctly
#   ./pda.sh db          Open SQLite database

set -e

# --- Configuration ---
PROJECT_DIR="/Users/jd/Projects/pda"
PYTHON="/opt/homebrew/bin/python3.11"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/mcp.log"
DB_FILE="$PROJECT_DIR/data/pda.db"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# --- Commands ---

cmd_dev() {
    echo "Starting Astro dev server with admin enabled..."
    echo "Site:  http://localhost:4321"
    echo "Admin: http://localhost:4321/admin"
    echo ""
    cd "$PROJECT_DIR/site"
    ENABLE_ADMIN=true npm run dev
}

cmd_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "No log file found: $LOG_FILE"
        exit 1
    fi

    echo "Tailing $LOG_FILE (Ctrl+C to stop)..."
    echo "---"
    tail -f "$LOG_FILE"
}

cmd_log() {
    local lines=${1:-50}

    if [ ! -f "$LOG_FILE" ]; then
        echo "No log file found: $LOG_FILE"
        exit 1
    fi

    echo "Last $lines lines from $LOG_FILE:"
    echo "---"
    tail -n "$lines" "$LOG_FILE"
}

cmd_clear() {
    if [ -f "$LOG_FILE" ]; then
        > "$LOG_FILE"
        echo "Log file cleared: $LOG_FILE"
    else
        echo "No log file to clear"
    fi
}

cmd_test() {
    echo "Testing MCP server module..."
    cd "$PROJECT_DIR"

    "$PYTHON" -c "
from mcp_server.server import mcp, get_database, get_taxonomy
from mcp_server import tools, preprocessing

db = get_database()
taxonomy = get_taxonomy()

print('Database:', db._path)
print('Methods:', taxonomy.methods)
print('Voices:', taxonomy.voices)
print('Categories:', taxonomy.categories)

progress = db.get_progress()
print('Progress:', progress)

print()
print('Server module loads correctly!')
print('MCP tools registered:', len(mcp._tool_manager._tools))
"
}

cmd_db() {
    if [ ! -f "$DB_FILE" ]; then
        echo "Database not found: $DB_FILE"
        exit 1
    fi

    echo "Opening SQLite database: $DB_FILE"
    echo "Useful commands:"
    echo "  .tables                    — List all tables"
    echo "  .schema articles           — Show articles table schema"
    echo "  SELECT * FROM articles;    — List all articles"
    echo "  .quit                      — Exit"
    echo "---"
    sqlite3 "$DB_FILE"
}

cmd_help() {
    cat << 'EOF'
PDA Translation Machine Control

Usage: ./pda.sh <command> [args]

Commands:
  dev        Start Astro dev server with admin enabled
  log [N]    Show last N MCP log entries (default: 50)
  logs       Tail the MCP log file (live, Ctrl+C to stop)
  clear      Clear the MCP log file
  test       Test if MCP server module loads correctly
  db         Open SQLite database in interactive mode
  help       Show this help message

Note: The MCP server is managed by Claude Desktop, not this script.
To restart the MCP server, restart Claude Desktop.

Examples:
  ./pda.sh dev             # Start website + admin
  ./pda.sh log 100         # Show last 100 MCP log lines
  ./pda.sh logs            # Follow MCP logs in real-time

Paths:
  Log file:  /Users/jd/Projects/pda/logs/mcp.log
  Database:  /Users/jd/Projects/pda/data/pda.db
EOF
}

# --- Main ---

case "${1:-}" in
    dev)
        cmd_dev
        ;;
    logs)
        cmd_logs
        ;;
    log)
        cmd_log "${2:-50}"
        ;;
    clear)
        cmd_clear
        ;;
    test)
        cmd_test
        ;;
    db)
        cmd_db
        ;;
    help|--help|-h)
        cmd_help
        ;;
    "")
        cmd_help
        exit 1
        ;;
    *)
        echo "Unknown command: $1"
        echo "Run './pda.sh help' for usage"
        exit 1
        ;;
esac
