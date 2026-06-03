#!/usr/bin/env python3
"""CLI entrypoint for HIP‑3 Builder Deployment Event Discovery probe.

Executes :func:`hip3_builder_deployment_event_discovery_v0.main` with command-line arguments.
"""
import sys

import importlib.util, pathlib, sys
script_path = pathlib.Path(__file__).resolve().parent.parent.parent / 'hip3_builder_deployment_event_discovery_v0.py'
spec = importlib.util.spec_from_file_location('hip3_probe', script_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
main = module.main


if __name__ == "__main__":
    sys.exit(main())
