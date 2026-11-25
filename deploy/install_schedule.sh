#!/bin/sh
# Install the four launchd jobs for this user: the daily forecast, the 10-day refresh (four a day),
# and the evening and 5 am alerts. Safe to run again: each job is replaced.
# Remove them with deploy/uninstall_schedule.sh. Logs go to data/logs/.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv)"
DEST="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
mkdir -p "$DEST" "$ROOT/data/logs"
for job in forecast outlook evening morning; do
  label="com.vailsnow.$job"
  sed -e "s|/PATH/TO/Vail Pinpoint Snow Forecaster|$ROOT|g" -e "s|/opt/homebrew/bin/uv|$UV|g" "$ROOT/deploy/$label.plist" > "$DEST/$label.plist"
  plutil -lint "$DEST/$label.plist" > /dev/null
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$DEST/$label.plist"
  echo "installed $label"
done
