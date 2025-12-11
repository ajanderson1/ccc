#!/bin/zsh
# ==============================================================================
# Claude Code Usage Analyzer (Fixed Date Logic)
# ==============================================================================

# --- 1. SETUP & TIMING ---
zmodload zsh/datetime
START_TIME=$EPOCHREALTIME

LOG_FILE=$(mktemp -t claude_usage_raw.XXXXXX)
DRIVER=$(mktemp -t claude_driver.XXXXXX)

function cleanup {
    [[ -f "$DRIVER" ]] && rm "$DRIVER"
    [[ -f "$LOG_FILE" ]] && rm "$LOG_FILE"
}
trap cleanup EXIT INT TERM

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

# --- 3. DRIVER ---
cat <<EOF > "$DRIVER"
set timeout 10
spawn $TARGET_CMD /usage
stty rows 50 cols 160

# Wait for the specific sections to appear
expect "Current week"
expect "Resets"
sleep 0.2

send "\033"
expect eof
EOF

# --- 4. EXECUTE ---
script -qF "$LOG_FILE" expect "$DRIVER" > /dev/null 2>&1

if [[ ! -s "$LOG_FILE" ]]; then
    echo "Error: No output captured."
    exit 1
fi

# --- 5. PARSER (Python) ---
python3 - "$LOG_FILE" "$START_TIME" <<'END_PYTHON'
import sys
import re
import time
import datetime
from datetime import timedelta

log_path = sys.argv[1]
start_ts = float(sys.argv[2])

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
    text = re.sub(r'\s+', ' ', text)       # Collapse spaces
    text = ''.join(c for c in text if c.isprintable())
    return text.strip()

def parse_reset_time(time_str):
    if not time_str: return None
    now = datetime.datetime.now()
    clean = clean_date_string(time_str)
    dt = None

    # Formats containing a specific Date
    formats_date = [
        '%b %d at %I:%M%p %Y', # Dec 8 at 4:30pm 2025
        '%b %d at %I%p %Y'     # Dec 8 at 4pm 2025
    ]
    
    # Formats containing only Time (The Problem Makers)
    formats_time = [
        '%I:%M%p',             # 4:30pm
        '%I%p'                 # 4pm
    ]

    # 1. Try formats with explicit Dates first
    if 'at' in clean:
        clean_with_year = f"{clean} {now.year}"
        for fmt in formats_date:
            try:
                dt = datetime.datetime.strptime(clean_with_year, fmt)
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

    # --- UPDATED REGEX ---
    # We specifically anchor to "Current session" and "Current week (all models)"
    # This ensures we don't accidentally grab "Extra usage" or "Sonnet only".
    
    session_match = re.search(r'Current session.*?(\d+)% used.*?Resets (.*?)(?:\s{2,}|\n|$)', clean_text, re.DOTALL)
    week_match = re.search(r'Current week \(all models\).*?(\d+)% used.*?Resets (.*?)(?:\s{2,}|\n|$)', clean_text, re.DOTALL)

    if not session_match or not week_match:
        print(f"{RED}Error: Data incomplete.{RESET}")
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
            
            pace_int = int(pace)
            p_str = f"{abs(pace_int)}pp"
            msg = f"Above pace ({p_str})" if pace > 0 else f"Below pace ({p_str})"
            
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

END_PYTHON