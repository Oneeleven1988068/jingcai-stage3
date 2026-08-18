#!/usr/bin/env bash
set -euo pipefail

echo "=== Jingcai Stage3 Skill Installer ==="

ZIP="$(find . -maxdepth 1 -type f -name 'Jingcai_Stage3_Canonical_Skill*.zip' | head -n 1)"
if [ -z "${ZIP}" ]; then
  echo "ERROR: Jingcai Stage3 Canonical Skill ZIP not found in repo root."
  exit 1
fi

echo "Using ZIP: ${ZIP}"

TMP="/tmp/jingcai-stage3-skill-install"
TARGET="skills/jingcai-stage3"

rm -rf "${TMP}"
mkdir -p "${TMP}" "${TARGET}"

unzip -q "${ZIP}" -d "${TMP}"

SRC="$(find "${TMP}" -mindepth 1 -maxdepth 1 -type d -name 'Jingcai_Stage3_Canonical_Skill*' | head -n 1)"
if [ -z "${SRC}" ]; then
  echo "ERROR: Canonical Skill directory not found inside ZIP."
  exit 1
fi

rm -rf "${TARGET:?}/"*
cp -R "${SRC}/." "${TARGET}/"

echo "=== Installed files ==="
find "${TARGET}" -maxdepth 2 -type f | sort | head -n 120

echo "=== Self test ==="
python3 "${TARGET}/runtime/validate_skill.py"

echo "=== Git commit ==="
git add "${TARGET}"

if git diff --cached --quiet; then
  echo "No Skill changes to commit."
else
  git commit -m "Add Jingcai Stage3 Canonical Skill v1.0.0"
  git push origin main
fi

echo "=== DONE ==="
git status --short
