#!/bin/sh
# Stop and remove the four launchd jobs. Forecast files, logs and the forecast log are left alone.
set -eu
DOMAIN="gui/$(id -u)"
for job in forecast outlook evening morning; do
  label="com.vailsnow.$job"
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
  echo "removed $label"
done
