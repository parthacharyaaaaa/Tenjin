#! /usr/bin/bash

set -eou pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

mapfile -t FILES < <(
    git diff --cached --name-only --diff-filter=ACMR |
    grep -E '\.(py|pyi)$' || true
)

if [ ${#FILES[@]} -eq 0 ]; then
    exit 0
fi

OUTPUT_FILE="bandit_output.json"


bandit \
    -c pyproject.toml \
    -r \
    -f json \
    -o "${OUTPUT_FILE}" \
    --exit-zero \
    "${FILES[@]}"


# Clean up output file
.venv/bin/python .pre-commit/utilities/bandit_output_cleaner.py "${OUTPUT_FILE}"

git add "${FILES[@]}"
