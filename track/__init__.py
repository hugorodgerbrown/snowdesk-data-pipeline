"""
track — pure track maths, shared by an instance and the service (SNOW-1316).

Turns a line into its terrain, legs and drawing. Imports neither Django
nor any ``apps.*`` package (``tests/track/test_boundary.py``); callers pass
in the tile base URL, the terrain grid and the sampler. See
docs/decisions/pure-track-maths-is-a-package.md.
"""
