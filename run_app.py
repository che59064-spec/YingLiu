"""Desktop entry: keep server diagnostics in a local file without a console."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
(ROOT / 'data').mkdir(exist_ok=True)
log = (ROOT / 'data' / 'application.log').open('a', encoding='utf-8', buffering=1)
sys.stdout = log
sys.stderr = log

from server import main

if __name__ == '__main__':
    main()
