#!/bin/zsh
# ==============================================================================
# Claude Code Usage Analyzer
# ==============================================================================

# --- 1. SETUP & TIMING ---
zmodload zsh/datetime
START_TIME=$EPOCHREALTIME

LOG_FILE=$(mktemp -t claude_usage_raw.XXXXXX)
DRIVER=$(mktemp -t claude_driver.XXXXXX)
DEBUG=${DEBUG:-0}
MAX_RETRIES=${MAX_RETRIES:-3}

function cleanup {
    [[ -f "$DRIVER" ]] && rm "$DRIVER"
    [[ "$DEBUG" -eq 0 && -f "$LOG_FILE" ]] && rm "$LOG_FILE"
}
trap cleanup EXIT INT TERM

function debug_log {
    [[ "$DEBUG" -eq 1 ]] && echo "[DEBUG] $1" >&2
}

# --- 2. CHECKS ---
if ! command -v expect &> /dev/null; then
    echo "Error: 'expect' is not installed."
    exit 1
fi

if command -v claude &> /dev/null; then
    TARGET_CMD="claude"
elif command -v cc &> /dev/null; then
    TARGET_CMD="cc"
else
    echo "Error: 'claude' or 'cc' binary not found."
    exit 1
fi

# --- 3. DRIVER (uses expect's log_file for reliable capture) ---
cat <<EOF > "$DRIVER"
set timeout 20
log_file -noappend "$LOG_FILE"

# Set terminal size
set env(LINES) 50
set env(COLUMNS) 160

spawn $TARGET_CMD /usage

# Capture the spawned PID for cleanup
set pid [exp_pid]

# Handle trust dialog if it appears, then wait for usage content
expect {
    "Yes, proceed" {
        # Trust dialog - accept it and continue waiting
        sleep 0.3
        send "\r"
        exp_continue
    }
    "% used" {
        # Actual data loaded - wait for full render
        sleep 1.5
    }
    timeout {
        # Continue anyway - might have partial content
    }
}

# Stop logging before cleanup
log_file

# Forcefully terminate - ESC alone is unreliable
catch {close}
catch {exec kill -9 \$pid}

# Wait briefly for process cleanup
catch {wait -nowait}
EOF

# --- 4. EXECUTE WITH RETRY ---
attempt=0
success=0

# Safety: kill any pre-existing /usage processes from this terminal
pkill -f "claude /usage" 2>/dev/null

while [[ $attempt -lt $MAX_RETRIES && $success -eq 0 ]]; do
    ((attempt++))
    debug_log "Attempt $attempt of $MAX_RETRIES"

    rm -f "$LOG_FILE"
    expect "$DRIVER" > /dev/null 2>&1

    # Extra safety: ensure no orphaned process from this attempt
    sleep 0.2
    pkill -f "claude /usage" 2>/dev/null

    if [[ -s "$LOG_FILE" ]]; then
        # Validate we got the key patterns AND actual percentage data
        if grep -q "Current session" "$LOG_FILE" && \
           grep -q "Current week" "$LOG_FILE" && \
           grep -qE "[0-9]+%.*used" "$LOG_FILE"; then
            success=1
            debug_log "Got valid output on attempt $attempt"
        else
            debug_log "Output missing required patterns, retrying..."
            [[ $attempt -lt $MAX_RETRIES ]] && sleep 1
        fi
    else
        debug_log "No output captured, retrying..."
        [[ $attempt -lt $MAX_RETRIES ]] && sleep 1
    fi
done

if [[ $success -eq 0 ]]; then
    echo "Error: Usage data unavailable after $MAX_RETRIES attempts."
    echo "Try running '/usage' directly inside Claude Code."
    exit 1
fi

# --- 5. PARSER (Python) ---
python3 - "$LOG_FILE" "$START_TIME" "$DEBUG" <<'END_PYTHON'
import sys
import re
import time
import datetime
from datetime import timedelta

log_path = sys.argv[1]
start_ts = float(sys.argv[2])
debug_mode = len(sys.argv) > 3 and sys.argv[3] == '1'

# --- UI CONSTANTS ---
WIDTH = 40
BLOCK_FULL = '█'
BLOCK_EMPTY = '░'
GREEN = '\033[92m'
RED = '\033[91m'
BOLD = '\033[1m'
RESET = '\033[0m'
DIM = '\033[2m'

def strip_ansi(text):
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)

def clean_date_string(text):
    text = re.sub(r'\s*\(.*?\)', '', text) # Remove (Europe/Stockholm)
    text = text.replace(',', '')           # Remove commas
    text = re.sub(r'\s+', ' ', text)       # Collapse spaces
    text = ''.join(c for c in text if c.isprintable())
    # Normalize am/pm to uppercase for consistent %p parsing
    text = re.sub(r'(?i)(am|pm)$', lambda m: m.group(1).upper(), text)
    return text.strip()

def parse_reset_time(time_str):
    if not time_str: return None
    now = datetime.datetime.now()
    clean = clean_date_string(time_str)
    dt = None

    # Formats containing a specific Date
    formats_date_with_year = [
        '%b %d %Y at %I:%M%p', # Jan 2 2026 at 9:59pm
        '%b %d %Y at %I%p'     # Jan 2 2026 at 9pm
    ]
    formats_date_no_year = [
        '%b %d at %I:%M%p',    # Dec 8 at 4:30pm
        '%b %d at %I%p'        # Dec 8 at 4pm
    ]

    # Formats containing only Time (The Problem Makers)
    formats_time = [
        '%I:%M%p',             # 4:30pm
        '%I%p'                 # 4pm
    ]

    # 1. Try formats with explicit Dates first
    if 'at' in clean:
        # First try formats that already have year embedded
        for fmt in formats_date_with_year:
            try:
                dt = datetime.datetime.strptime(clean, fmt)
                break
            except ValueError:
                continue

        # Then try formats without year (append current year)
        if dt is None:
            for fmt in formats_date_no_year:
                try:
                    dt = datetime.datetime.strptime(f"{clean} {now.year}", f"{fmt} %Y")
                    # Year Wrap Logic: If date is way in past, it's next year
                    if dt < now - timedelta(days=300):
                        dt = dt.replace(year=now.year + 1)
                    elif dt > now + timedelta(days=300):
                        dt = dt.replace(year=now.year - 1)
                    break
                except ValueError:
                    continue

    # 2. Try Time-Only formats
    else:
        for fmt in formats_time:
            try:
                t = datetime.datetime.strptime(clean, fmt).time()
                dt = datetime.datetime.combine(now.date(), t)

                # --- FIX: THE TOMORROW LOGIC ---
                # If we parsed "1:59am" but it is currently "11:58pm",
                # the parsed date (Today 1:59am) is in the past.
                # "Resets" always implies the future.
                # If the parsed time is earlier than NOW (minus a small buffer),
                # assume it refers to Tomorrow.
                if dt < now - timedelta(minutes=15):
                    dt += timedelta(days=1)

                break
            except ValueError:
                continue

    return dt

def create_bar(percent):
    p = max(0, min(100, percent))
    fill = int((p / 100) * WIDTH)
    return (BLOCK_FULL * fill) + (BLOCK_EMPTY * (WIDTH - fill))

try:
    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    content = content.replace('\r\n', '\n').replace('\r', '\n')
    clean_text = strip_ansi(content)

    # --- REGEX PATTERNS ---
    # Session: "Current session" ... "X% used" ... "Resets <time>"
    session_match = re.search(
        r'Current\s+session.*?(\d+)%\s*used.*?Resets\s*(.*?)(?:\s{2,}|\n|$)',
        clean_text, re.DOTALL | re.IGNORECASE
    )

    # Week: "Current week (all models)" - must explicitly match "(all models)"
    # to avoid matching "Sonnet only" section
    week_match = re.search(
        r'Current\s+week\s+\(all\s+models\).*?(\d+)%\s*used.*?Resets\s*(.*?)(?:\s{2,}|\n|$)',
        clean_text, re.DOTALL | re.IGNORECASE
    )

    if not session_match or not week_match:
        print(f"{RED}Error: Data incomplete.{RESET}")
        print(f"  Session data: {'found' if session_match else 'MISSING'}")
        print(f"  Week data: {'found' if week_match else 'MISSING'}")

        # Show captured content preview for debugging
        lines = [l.strip() for l in clean_text.split('\n') if l.strip()]
        preview = lines[:15] if len(lines) > 15 else lines
        print(f"\n{DIM}Captured content preview:{RESET}")
        for line in preview:
            print(f"  {DIM}{line[:70]}{RESET}")
        if len(lines) > 15:
            print(f"  {DIM}... ({len(lines) - 15} more lines){RESET}")

        if debug_mode:
            print(f"\n{DIM}Log file preserved at: {log_path}{RESET}")
        sys.exit(1)

    now = datetime.datetime.now()
    duration = time.time() - start_ts

    def process_and_print(title, used_str, reset_str, window_hours):
        used = int(used_str)
        reset_dt = parse_reset_time(reset_str)

        print(f"  {BOLD}{title}{RESET}")

        if reset_dt:
            if window_hours == 168:
                start_dt = reset_dt - timedelta(days=7)
            else:
                start_dt = reset_dt - timedelta(hours=window_hours)

            elapsed_pct = ((now - start_dt).total_seconds() / (window_hours * 3600)) * 100
            pace = used - elapsed_pct
            remain = reset_dt - now

            # Clamp visualization to 100% so bars don't break lines
            bar_pct = min(100, elapsed_pct)

            print(f"  Time:   {create_bar(bar_pct)}  {int(elapsed_pct)}% time")
            c = RED if used > elapsed_pct else GREEN
            print(f"  Usage:  {c}{create_bar(used)}{RESET}  {used}% used")

            pace_int = round(pace)
            if pace_int == 0:
                msg = "On pace"
            else:
                p_str = f"{abs(pace_int)}pp"
                msg = f"Above pace ({p_str})" if pace_int > 0 else f"Below pace ({p_str})"

            days = remain.days
            hours = remain.seconds // 3600
            mins = (remain.seconds // 60) % 60

            if days > 0:
                remain_str = f"{days}d {hours}h"
            else:
                remain_str = f"{hours}h {mins}m"

            print(f"  Status: {c}{msg}{RESET} | Resets in {remain_str}")
        else:
            print(f"  Usage:  {create_bar(used)}  {used}% used")
            print(f"  {RED}Date error: '{reset_str}'{RESET}")

    # --- PRINT HEADER ---
    print(f"\n{BOLD}Usage Analysis - {now.strftime('%A %B %d at %H:%M')} (took {duration:.2f}s){RESET}\n")

    process_and_print("Weekly Usage (168h)", week_match.group(1), week_match.group(2), 168)
    print(f"\n  {DIM}---{RESET}\n")
    process_and_print("Session Usage (5h)", session_match.group(1), session_match.group(2), 5)
    print("")

except Exception as e:
    print(f"{RED}Script Error: {e}{RESET}")
    if debug_mode:
        import traceback
        traceback.print_exc()

END_PYTHON
