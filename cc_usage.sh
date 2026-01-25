#!/bin/zsh
# ==============================================================================
# Claude Code Usage Analyzer
# ==============================================================================

# --- 1. SETUP ---
zmodload zsh/datetime

# --- ARGUMENT PARSING ---
LOOP_MODE=0
LOOP_INTERVAL=${LOOP_INTERVAL:-300}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --loop)
            LOOP_MODE=1
            shift
            ;;
        --interval)
            LOOP_INTERVAL="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--loop] [--interval SECONDS]"
            echo "  --loop       Run continuously with countdown between refreshes"
            echo "  --interval   Seconds between refreshes (default: 300)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--loop] [--interval SECONDS]"
            exit 1
            ;;
    esac
done

DEBUG=${DEBUG:-0}
MAX_RETRIES=${MAX_RETRIES:-3}

function debug_log {
    [[ "$DEBUG" -eq 1 ]] && echo "[DEBUG] $1" >&2
}

# --- 2. CHECKS (run once) ---
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

# --- 3. MAIN FUNCTION ---
run_usage_check() {
    local START_TIME=$EPOCHREALTIME
    local LOG_FILE=$(mktemp -t claude_usage_raw.XXXXXX)
    local DRIVER=$(mktemp -t claude_driver.XXXXXX)

    # Cleanup function
    cleanup() {
        [[ -f "$DRIVER" ]] && rm "$DRIVER"
        [[ "$DEBUG" -eq 0 && -f "$LOG_FILE" ]] && rm "$LOG_FILE"
    }

    # --- DRIVER (uses expect's log_file for reliable capture) ---
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
    "Sonnet only" {
        # All sections loaded (Sonnet is last) - brief wait for render
        sleep 0.5
    }
    "% used" {
        # Fallback: first section only, wait longer for rest
        sleep 2.0
    }
    timeout {
    }
}

# Stop logging before cleanup
sleep 0.2
log_file

# Forcefully terminate - ESC alone is unreliable
catch {close}
catch {exec kill -9 \$pid}

# Wait briefly for process cleanup
catch {wait -nowait}
EOF

    # --- EXECUTE WITH RETRY ---
    local attempt=0
    local success=0

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
        cleanup
        return 1
    fi

    # --- PARSER (Python) ---
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

def parse_reset_time(time_str, window_hours=5, section_text=None):
    if not time_str: return None
    now = datetime.datetime.now()

    # PRIMARY: Try standard time pattern in captured string
    time_pattern = r'(\d{1,2})(:\d{2})?(am|pm)'
    time_match = re.search(time_pattern, time_str, re.IGNORECASE)

    # FALLBACK: Terminal does progressive rendering with cursor movement,
    # so raw capture may contain multiple versions. Find ALL valid time
    # patterns and use the LAST one (the final/complete rendering).
    if not time_match and section_text:
        # Match time with optional date prefix: "12:59am" or "Jan 29 at 6:59pm"
        full_time_pattern = r'(?:[A-Za-z]{3}\s+\d{1,2}\s+at\s+)?\d{1,2}:\d{2}(?:am|pm)'
        all_matches = re.findall(full_time_pattern, section_text, re.IGNORECASE)
        if all_matches:
            time_str = all_matches[-1]  # Use the LAST match
            time_match = re.search(time_pattern, time_str, re.IGNORECASE)

    # If we found a time match, reconstruct time_str properly
    if time_match:
        hour = time_match.group(1)
        minutes = time_match.group(2) or ''
        ampm = time_match.group(3)

        # Check for date prefix (e.g., "Jan 29 at") in original string
        date_match = re.search(r'([A-Za-z]{3})\s+(\d{1,2})\s+at', time_str, re.IGNORECASE)
        if date_match:
            time_str = f"{date_match.group(1)} {date_match.group(2)} at {hour}{minutes}{ampm}"
        else:
            time_str = f"{hour}{minutes}{ampm}"

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
                # add appropriate days: 7 for weekly (168h), 1 for session.
                if dt < now - timedelta(minutes=15):
                    days_to_add = 7 if window_hours == 168 else 1
                    dt += timedelta(days=days_to_add)

                break
            except ValueError:
                continue

    return dt

def validate_reset_time(reset_dt, window_hours, reset_str):
    """
    Validate parsed reset time makes sense.
    Returns (reset_dt, warning_msg) - dt may be None if invalid.
    """
    if not reset_dt:
        return None, f"Failed to parse: '{reset_str}'"

    now = datetime.datetime.now()
    remain = reset_dt - now
    remain_hours = remain.total_seconds() / 3600
    max_hours = window_hours + 1  # Small buffer for timing

    # Sanity check: time remaining can't exceed window
    if remain_hours > max_hours:
        return None, f"Invalid: {remain_hours:.1f}h remaining exceeds {window_hours}h window"

    # Sanity check: time remaining shouldn't be very negative
    if remain_hours < -window_hours:
        return None, f"Invalid: reset time {remain_hours:.1f}h in past"

    return reset_dt, None

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
    # Split content at "Current week" to isolate session section
    session_section = clean_text.split('Current week')[0] if 'Current week' in clean_text else clean_text

    # Session: "Current session" ... "X% used" ... "Rese(t)s <time>"
    # Handle potential character corruption (Reses vs Resets)
    session_match = re.search(
        r'Current\s+session.*?(\d+)%\s*used.*?Rese[ts]*\s*(.*?)(?:\s{2,}|\n|$)',
        session_section, re.DOTALL | re.IGNORECASE
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

    def process_and_print(title, used_str, reset_str, window_hours, section_text=None):
        used = int(used_str)
        reset_dt = parse_reset_time(reset_str, window_hours, section_text)
        reset_dt, warning = validate_reset_time(reset_dt, window_hours, reset_str)

        print(f"  {BOLD}{title}{RESET}")

        if warning:
            print(f"  Usage:  {create_bar(used)}  {used}% used")
            print(f"  {RED}Warning: {warning}{RESET}")
            if debug_mode:
                print(f"  {DIM}Raw reset_str: '{reset_str}'{RESET}")
                if reset_dt is None:
                    # Try parsing again just to show what we got
                    parsed_attempt = parse_reset_time(reset_str, window_hours, section_text)
                    if parsed_attempt:
                        print(f"  {DIM}Parsed datetime: {parsed_attempt}{RESET}")
                        print(f"  {DIM}Expected range: now to +{window_hours}h{RESET}")
            return

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

    # --- PRINT HEADER ---
    print(f"\n{BOLD}Usage Analysis - {now.strftime('%A %B %d at %H:%M')} (took {duration:.2f}s){RESET}\n")

    if debug_mode:
        print(f"  {DIM}DEBUG: Session reset_str = '{session_match.group(2)}'{RESET}")
        print(f"  {DIM}DEBUG: Week reset_str = '{week_match.group(2)}'{RESET}")
        print()

    process_and_print("Weekly Usage (168h)", week_match.group(1), week_match.group(2), 168, clean_text)
    print(f"\n  {DIM}---{RESET}\n")
    process_and_print("Session Usage (5h)", session_match.group(1), session_match.group(2), 5, session_section)
    print("")

except Exception as e:
    print(f"{RED}Script Error: {e}{RESET}")
    if debug_mode:
        import traceback
        traceback.print_exc()

END_PYTHON

    cleanup
}

# --- 4. EXECUTION ---
if [[ $LOOP_MODE -eq 1 ]]; then
    # Loop mode: run continuously with countdown
    trap "echo; exit 0" INT TERM

    while true; do
        clear
        run_usage_check

        # Save cursor position
        tput sc

        for ((i=LOOP_INTERVAL; i>0; i--)); do
            # Restore cursor, clear line, print timer
            tput rc
            tput el
            printf "Next refresh in %3d seconds... (Ctrl+C to exit)" "$i"
            sleep 1
        done
    done
else
    # Single run mode
    run_usage_check
fi
