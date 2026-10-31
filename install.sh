#!/bin/sh
# JobGambit installer for macOS and Linux. Paste this into Terminal:
#   curl -LsSf https://raw.githubusercontent.com/Joshi595/JobGambit/main/install.sh | sh
# It installs uv (a small tool that brings its own Python), installs JobGambit, adds a launcher,
# and offers to install the AI you use. No admin rights needed.
set -e
SOURCE="$JOBGAMBIT_SOURCE"
if [ -z "$SOURCE" ]; then  # a released version (a tag), never whatever is on main
  TAG=$(curl -fsSL "https://api.github.com/repos/Joshi595/JobGambit/tags?per_page=100" | grep -o '"name": *"v[0-9][0-9.]*"' |
        grep -o 'v[0-9][0-9.]*' | sort -V | tail -1)
  [ -n "$TAG" ] || { echo "Couldn't find a released version of JobGambit. Nothing was installed."; exit 1; }
  SOURCE="jobgambit @ https://github.com/Joshi595/JobGambit/archive/refs/tags/$TAG.zip"
fi
echo "\n== JobGambit setup ==\n"

# 1. uv (official installer from astral.sh)
UV="$HOME/.local/bin/uv"
[ -x "$UV" ] || curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. JobGambit itself. It was called JobQuill in 0.13–0.16 and CareerPilot up to 0.12: such a copy goes, and JobGambit
#    moves its data over at its first start.
for OLD in jobquill:JobQuill careerpilot:CareerPilot; do
  BIN="${OLD%%:*}" NAME="${OLD#*:}"
  if [ -x "$HOME/.local/bin/$BIN" ]; then
    pkill -f "$HOME/.local/bin/$BIN" || true
    "$UV" tool uninstall "$BIN"
    rm -f "$HOME/Applications/Start $NAME.command" "$HOME/.local/share/applications/$BIN.desktop"
  fi
done
echo "Installing JobGambit..."
"$UV" tool install --force "$SOURCE"
APP="$HOME/.local/bin/jobgambit"

# 3. A launcher
if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$HOME/Applications"
  L="$HOME/Applications/Start JobGambit.command"
  printf '#!/bin/sh\n"%s" >/dev/null 2>&1 &\n' "$APP" > "$L" && chmod +x "$L"
  echo "Launcher: ~/Applications/Start JobGambit.command"
else
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/jobgambit.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Start JobGambit
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

echo "\nDone. Starting JobGambit...\n"
"$APP" >/dev/null 2>&1 &
