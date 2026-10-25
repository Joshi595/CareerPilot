#!/bin/sh
# JobQuill installer for macOS and Linux. Paste this into Terminal:
#   curl -LsSf https://raw.githubusercontent.com/Joshi595/JobQuill/main/install.sh | sh
# It installs uv (a small tool that brings its own Python), installs JobQuill, adds a launcher,
# and offers to install the AI you use. No admin rights needed.
set -e
SOURCE="$JOBQUILL_SOURCE"
if [ -z "$SOURCE" ]; then  # a released version (a tag), never whatever is on main
  TAG=$(curl -fsSL "https://api.github.com/repos/Joshi595/JobQuill/tags?per_page=100" | grep -o '"name": *"v[0-9][0-9.]*"' |
        grep -o 'v[0-9][0-9.]*' | sort -V | tail -1)
  [ -n "$TAG" ] || { echo "Couldn't find a released version of JobQuill. Nothing was installed."; exit 1; }
  SOURCE="jobquill @ https://github.com/Joshi595/JobQuill/archive/refs/tags/$TAG.zip"
fi
echo "\n== JobQuill setup ==\n"

# 1. uv (official installer from astral.sh)
UV="$HOME/.local/bin/uv"
[ -x "$UV" ] || curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. JobQuill itself. Up to 0.12 it was called CareerPilot: that copy goes, and JobQuill moves its data over at its
#    first start.
if [ -x "$HOME/.local/bin/careerpilot" ]; then
  pkill -f "$HOME/.local/bin/careerpilot" || true
  "$UV" tool uninstall careerpilot
  rm -f "$HOME/Applications/Start CareerPilot.command" "$HOME/.local/share/applications/careerpilot.desktop"
fi
echo "Installing JobQuill..."
"$UV" tool install --force "$SOURCE"
APP="$HOME/.local/bin/jobquill"

# 3. A launcher
if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$HOME/Applications"
  L="$HOME/Applications/Start JobQuill.command"
  printf '#!/bin/sh\n"%s" >/dev/null 2>&1 &\n' "$APP" > "$L" && chmod +x "$L"
  echo "Launcher: ~/Applications/Start JobQuill.command"
else
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/jobquill.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Start JobQuill
Exec=$APP
Terminal=false
EOF
fi

# 4. The AI the person pays for (each vendor's own installer, only with their OK)
echo "\nWhich AI plan do you have?\n  1) Claude (Pro or Max)\n  2) ChatGPT (Plus or Pro) - Codex\n  3) Google AI Pro / Ultra - Antigravity\n  4) Already installed / skip"
printf "Type 1, 2, 3 or 4: "
read choice < /dev/tty
case "$choice" in
  1) command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash ;;
  2) if command -v brew >/dev/null; then brew install codex; elif command -v npm >/dev/null; then npm install -g @openai/codex;
     else echo "Install Node.js (nodejs.org), then run: npm install -g @openai/codex"; fi ;;
  3) echo "Download Antigravity from https://antigravity.google" ;;
esac

echo "\nDone. Starting JobQuill...\n"
"$APP" >/dev/null 2>&1 &
