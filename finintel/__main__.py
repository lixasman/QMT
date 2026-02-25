import sys

sys.dont_write_bytecode = True

from .main import main

raise SystemExit(main())
