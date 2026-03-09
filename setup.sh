#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  IDX Briefing — One-shot setup
#  Run once from Terminal:  bash setup.sh
#  Does everything: installs packages, creates Gist, patches JSX, sets up
#  launchd so your briefing auto-updates every day at 8:50 AM.
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
JSX="$SCRIPT_DIR/idx-briefing-v11.jsx"
BRIEFING_PY="$SCRIPT_DIR/generate_briefing.py"
PLIST_LABEL="com.douglas.idx-briefing"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

export ANTHROPIC_API_KEY="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"
export GITHUB_TOKEN="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"
export IDX_GIST_ID=""

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  IDX Briefing — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


# ── 1. Install Python packages ────────────────────────────────────────────────
echo ""
echo "→ [1/4] Installing Python packages (yfinance, anthropic, requests)…"
echo "  Python: $(python3 --version 2>&1)"
python3 -m pip install yfinance anthropic requests --quiet --upgrade
echo "  ✓ Done"


# ── 2. Generate today's briefing + create Gist ───────────────────────────────
echo ""
echo "→ [2/4] Generating today's briefing and creating your private Gist…"
echo "  (This takes ~30s while fetching live prices and calling Claude)"
echo ""

TMPOUT="$SCRIPT_DIR/.setup_output.tmp"
python3 "$BRIEFING_PY" 2>&1 | tee "$TMPOUT"
PYTHON_EXIT=${PIPESTATUS[0]}

if [ "$PYTHON_EXIT" -ne 0 ]; then
  echo ""
  echo "✗ generate_briefing.py exited with error (code $PYTHON_EXIT)."
  echo "  See the output above for details."
  rm -f "$TMPOUT"
  exit 1
fi

GIST_RAW_URL=$(grep -o 'https://gist\.githubusercontent\.com[^ ]*' "$TMPOUT" | head -1)
rm -f "$TMPOUT"

if [ -z "$GIST_RAW_URL" ]; then
  echo ""
  echo "✗ Script finished but no Gist URL was found in its output."
  exit 1
fi

echo ""
echo "  ✓ Gist raw URL: $GIST_RAW_URL"


# ── 3. Patch the JSX with the live Gist URL ──────────────────────────────────
echo ""
echo "→ [3/4] Wiring Gist URL into idx-briefing-v11.jsx…"
sed -i '' "s|const GIST_URL = \"[^\"]*\";|const GIST_URL = \"$GIST_RAW_URL\";|" "$JSX"
echo "  ✓ Done"


# ── 4. Create and load the launchd agent ─────────────────────────────────────
echo ""
echo "→ [4/4] Installing launchd agent (runs every day at 8:50 AM)…"

PYTHON3_PATH=$(which python3)
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Library/Logs"

cat > "$PLIST_PATH" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON3_PATH}</string>
    <string>${BRIEFING_PY}</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key>
    <string>${ANTHROPIC_API_KEY}</string>
    <key>GITHUB_TOKEN</key>
    <string>${GITHUB_TOKEN}</string>
    <key>IDX_GIST_ID</key>
    <string></string>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>   <integer>8</integer>
    <key>Minute</key> <integer>0</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/idx-briefing.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/idx-briefing-error.log</string>

  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST_EOF

# Unload silently if already loaded, then load fresh
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "  ✓ Agent loaded — will run every morning at 8:50 AM"


# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Setup complete!"
echo ""
echo "  Your IDX briefing artifact now updates automatically every morning."
echo "  Open idx-briefing-v11.jsx in Claude to see today's live briefing."
echo ""
echo "  Logs (if anything ever goes wrong):"
echo "    $HOME/Library/Logs/idx-briefing.log"
echo "    $HOME/Library/Logs/idx-briefing-error.log"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
