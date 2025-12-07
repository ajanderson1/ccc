# Claude Code Usage Analyzer (ccc)

A command-line tool that provides a visual, pace-aware interpretation of your Claude Code usage limits. This script extracts and analyzes the data from Claude Code's `/usage` command, presenting it in a more actionable format with time-based pacing indicators.

## Why This Tool?

Claude Code (the CLI tool) provides a `/usage` command that shows your current usage percentages, but it lacks context:
- **How much time has elapsed** in your current window?
- **Are you ahead or behind pace** for sustainable usage?
- **When exactly does the limit reset?**

This tool answers those questions by calculating your usage *pace* - comparing your consumption percentage against elapsed time percentage.

### Example Output

```
Usage Analysis - Monday December 08 at 14:32 (took 0.87s)

  Weekly Usage (168h)
  Time:   ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  21% time
  Usage:  ████████████████░░░░░░░░░░░░░░░░░░░░░░░░  40% used
  Status: Above pace (19pp) | Resets in 5d 11h

  ---

  Session Usage (5h)
  Time:   ████████████████████████░░░░░░░░░░░░░░░░  60% time
  Usage:  ██████████████████████████████░░░░░░░░░░  75% used
  Status: Above pace (15pp) | Resets in 2h 0m
```

**Green** = You're using less than the elapsed time (sustainable pace)
**Red** = You're consuming faster than time is passing (may hit limits early)

## Background: Claude Code Usage Limits

Claude Code has a dual-window rate limiting system:

### Session Window (5 hours)
A rolling 5-hour window that starts when you begin using Claude Code. This governs short-term burst usage.

### Weekly Window (7 days)
A separate 168-hour window that tracks longer-term consumption. Introduced in August 2025, this prevents sustained heavy usage from exhausting resources.

### Why Pacing Matters

If you're at 50% usage but only 25% through your time window, you're consuming at **2x sustainable pace** and will likely hit your limit before reset. This tool makes that immediately visible.

For more details on Claude Code limits, see the [official documentation](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan).

## Installation

### Prerequisites

| Dependency | Purpose | Installation |
|------------|---------|--------------|
| **zsh** | Shell interpreter (script uses zsh-specific features) | Pre-installed on macOS; `apt install zsh` on Linux |
| **expect** | Automates interaction with Claude Code's interactive `/usage` command | `brew install expect` (macOS) or `apt install expect` (Linux) |
| **python3** | Parses output and calculates pacing metrics | Pre-installed on most systems |
| **claude** or **cc** | The Claude Code CLI itself | See [Claude Code installation](https://docs.claude.com/en/code/) |

### Setup

1. Clone this repository:
   ```bash
   git clone https://github.com/ajanderson/ccc.git
   cd ccc
   ```

2. Make the script executable:
   ```bash
   chmod +x cc_usage.sh
   ```

3. (Optional) Add to your PATH or create an alias:
   ```bash
   # Add to ~/.zshrc or ~/.bashrc
   alias ccc="/path/to/ccc/cc_usage.sh"
   ```

## Usage

Simply run the script:

```bash
./cc_usage.sh
```

Or if you set up the alias:

```bash
ccc
```

The script will:
1. Launch Claude Code with the `/usage` command
2. Capture the output using `expect` and `script`
3. Parse and analyze the usage data
4. Display a visual pace-aware breakdown

## How It Works

This tool is admittedly a "hack" - it works around the lack of a programmatic API for usage data:

### The Problem
Claude Code's `/usage` command is interactive and designed for human consumption. There's no API endpoint or machine-readable output available for Pro/Max subscribers (unlike API-based accounts which have proper rate limit headers).

### The Solution
1. **`expect`** - A Unix tool for automating interactive applications. The script spawns Claude Code, waits for the usage display to render, then sends an escape key to exit.

2. **`script`** - Captures all terminal output (including ANSI escape codes) to a log file. This is necessary because expect alone doesn't reliably capture the full output.

3. **Python parser** - Strips ANSI codes, extracts percentages and reset times using regex, calculates elapsed time percentages, and renders the visual comparison.

### Key Technical Details

- Uses `zmodload zsh/datetime` for high-precision timing
- Creates temporary files for the expect driver and captured output
- Cleans up temporary files on exit (including interrupt signals)
- Handles various date/time formats from Claude's output
- Includes "tomorrow logic" for time-only reset values that appear to be in the past

## Limitations

- **macOS/Linux only** - Relies on Unix tools (`script`, `expect`)
- **zsh required** - Uses zsh-specific features (`zmodload zsh/datetime`, `$EPOCHREALTIME`)
- **Timing sensitive** - The expect script has hardcoded timeouts; slow connections may fail
- **Fragile parsing** - If Anthropic changes the `/usage` output format, the regex patterns may break
- **No caching** - Each run spawns a new Claude Code instance (~1 second overhead)

## Troubleshooting

### "Error: 'expect' is not installed"
Install expect: `brew install expect` (macOS) or `apt install expect` (Linux)

### "Error: 'claude' or 'cc' binary not found"
Ensure Claude Code is installed and in your PATH. See [installation docs](https://docs.claude.com/en/code/).

### "Error: No output captured"
- Check that Claude Code is working: run `claude /usage` manually
- Increase the timeout in the expect script if you have a slow connection
- Ensure you're authenticated with Claude Code

### "Error: Data incomplete" or "Date error"
The regex patterns may not match the current output format. This could happen if:
- Your locale uses different date formatting
- Anthropic has updated the `/usage` output format
- The timezone display format has changed

## Contributing

This is a quick utility script, but improvements are welcome:
- Better cross-platform support
- Bash compatibility (remove zsh dependencies)
- Caching to reduce overhead
- Configuration options for display width, colors, etc.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Related Resources

- [Claude Code Documentation](https://docs.claude.com/en/code/)
- [Claude Code Limits Explained](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- [expect command tutorial](https://likegeeks.com/expect-command/)
- [Using script to capture terminal output](https://superuser.com/questions/1084287/save-terminal-output-command-on-osx)

## Acknowledgments

Built as a workaround for the lack of programmatic access to Claude Code usage metrics for consumer (Pro/Max) subscribers.
