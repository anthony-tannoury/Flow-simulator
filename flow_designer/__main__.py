import sys

try:
    from .flow_designer import main
except ImportError:
    from flow_designer import main

sys.exit(main())
