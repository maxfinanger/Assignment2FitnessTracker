"""Smart Fitness Session Analyzer -- program entry point.

Run from the repository root:

    python3 main.py --profiles data/participants.csv \
        --sessions data/fitness_sessions.csv data/fitness_sessions_invalid.csv \
        --output output

All three options have defaults, so ``python3 main.py`` alone does the same.
"""

import sys

from fitness_analyzer.cli import main

if __name__ == "__main__":
    sys.exit(main())
