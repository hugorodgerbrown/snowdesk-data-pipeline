#!/usr/bin/env bash
#
# Re-download the competitor store screenshots listed in sources.txt.
#
# The images are deliberately not committed (see .gitignore). This script
# is the committed half: the URLs plus the one command that turns them
# back into files, so the research is reproducible without the repository
# carrying anyone else's assets.
#
# Usage:
#   ./fetch.sh            # 1200px wide, the App Store's full-size render
#   ./fetch.sh 600x1300   # smaller, if you just want to skim them
#
# Apple serves any size from the same base URL by appending a size
# segment, so the width is a parameter rather than something baked into
# the stored URLs.
set -euo pipefail

cd "$(dirname "$0")"
size="${1:-1200x2600}"

while IFS='  ' read -r name url; do
    case "$name" in ''|'#'*) continue ;; esac
    [ -z "${url:-}" ] && continue
    printf 'fetching %s ... ' "$name"
    if curl -fsSL --max-time 30 -o "$name" "${url}/${size}bb.png"; then
        printf 'ok (%s)\n' "$(du -h "$name" | cut -f1)"
    else
        printf 'FAILED\n'
    fi
done < <(awk 'NF==2 && $1 !~ /^#/ {print $1 "  " $2}' sources.txt)

echo
echo "Done. These files are gitignored — do not add them to a commit."
