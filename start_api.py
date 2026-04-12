"""Entry point to start the FastAPI sidecar independently."""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from autocapcut.api.server import main

if __name__ == "__main__":
    main()
