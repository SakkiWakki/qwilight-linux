#!/bin/bash
# Regenerate wine/wine-qwilight.patch from a Wine working tree: the diff against its last commit
# plus every untracked file, as one git-apply-able patch.  Also records the base commit and
# checks that the patch applies to a clean export of it.
#
#   setup/make-patch.sh [wine tree]        (default: ~/dev/qwilight/wine)
set -e
TREE="${1:-$HOME/dev/qwilight/wine}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/wine/wine-qwilight.patch"
[ -d "$TREE/.git" ] || { echo "not a git tree: $TREE" >&2; exit 1; }

cd "$TREE"
{
  git diff HEAD --binary
  for f in $(git ls-files --others --exclude-standard | grep -v '^\.make\.lock$\|^vb$'); do
    git diff --no-index --binary /dev/null "$f" || true   # exit 1 just means "differs"
  done
} > "$OUT"
git rev-parse HEAD > "$REPO/wine/BASE_COMMIT"

echo "patch: $OUT ($(wc -c < "$OUT") bytes, $(grep -c '^diff --git' "$OUT") files), base $(cat "$REPO/wine/BASE_COMMIT")"

# verify against a clean export of the base commit
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git archive HEAD | tar -x -C "$TMP"
( cd "$TMP" && git init -q && git apply --check "$OUT" ) && echo "applies cleanly to the base commit"
