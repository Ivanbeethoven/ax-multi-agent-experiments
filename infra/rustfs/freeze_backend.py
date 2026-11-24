#!/usr/bin/env python3
"""Compatibility entry point; verifies the latest branch, with no commit pin."""
import sys
from resolve_backend import main

if __name__ == "__main__": raise SystemExit(main(["image", *sys.argv[1:]]))
