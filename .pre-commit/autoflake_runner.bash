#! /usr/bin/bash

set -euo pipefail

if [ "$#" -eq 0 ]; then
    exit 0
fi

autoflake \
    --ignore-init-module-imports \
    --in-place \
    "$@"
