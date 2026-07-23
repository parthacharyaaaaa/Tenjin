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

# Clean up output file
.venv/bin/python .pre-commit/utilities/bandit_wrapper.py "${OUTPUT_FILE}" "${FILES[@]}"
