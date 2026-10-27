#!/bin/bash
PYTHONPATH=$(dirname $(dirname $(readlink -f $0)))/.. python3 $(dirname $(dirname $(readlink -f $0)))/scripts/render_telegraph_digest.py "$@"
