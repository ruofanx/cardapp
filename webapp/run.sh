#!/bin/bash
# Launch the Pokemon Trading webapp on http://localhost:8000
#
# First run:
#   cd webapp
#   python3 -m venv .venv
#   source .venv/bin/activate
#   pip install -r requirements.txt
#   ./run.sh
#
# Subsequent runs:
#   ./run.sh

set -e
cd "$(dirname "$0")"

# Activate venv if present
if [ -d .venv ]; then
  source .venv/bin/activate
fi

# Add ANTHROPIC_API_KEY=sk-... before the command if you want photo identify
exec uvicorn app:app --reload --host 0.0.0.0 --port 8000
