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

.venv/bin/python .pre-commit/utilities/deptry_wrapper.py
