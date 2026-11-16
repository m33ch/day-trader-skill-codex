#!/usr/bin/env bash
# Install a complete checkout's skill folders, with backups of existing entries.
set -euo pipefail
source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
install_destination=${1:-"$HOME/.agents/skills"}
backup_destination="$(dirname "$install_destination")/skill-backups/day-trader-$(date +%Y%m%d-%H%M%S)-$$"
install_stage=$(mktemp -d "${TMPDIR:-/tmp}/day-trader-skills.XXXXXX")
trap 'rm -rf "$install_stage"' EXIT
count=0
for definition in "$source_dir"/trader-*/SKILL.md; do
  [ -f "$definition" ]
  folder=$(dirname "$definition")
  name=$(basename "$folder")
  cp -R "$folder" "$install_stage/$name"
  # Keep local interpreter artifacts out of copied installations.
  find "$install_stage/$name" -type d -name __pycache__ -prune -exec rm -rf {} +
  count=$((count + 1))
done
mkdir -p "$install_destination"
for folder in "$install_stage"/trader-*; do
  name=$(basename "$folder")
  if [ -e "$install_destination/$name" ] || [ -L "$install_destination/$name" ]; then
    mkdir -p "$backup_destination"
    mv "$install_destination/$name" "$backup_destination/$name"
  fi
  if ! mv "$folder" "$install_destination/$name"; then
    if [ -e "$backup_destination/$name" ] || [ -L "$backup_destination/$name" ]; then
      mv "$backup_destination/$name" "$install_destination/$name"
    fi
    exit 1
  fi
  printf 'Installed %s\n' "$name"
done
printf 'Installed %s skills in %s\n' "$count" "$install_destination"
if [ -d "$backup_destination" ]; then printf 'Previous versions: %s\n' "$backup_destination"; fi
printf 'In Codex use $trader-forex; restart Codex if it does not appear.\n'
