"""
apps/public/guidance.py — Load the field guidance notes from YAML.

``apps/public/field_guidance.yaml`` holds one plain-text note per EAWS
problem type, attached to each problem card on the bulletin page.

``load_field_guidance()`` is ``functools.cache``d, as
``apps.public.glossary.load_glossary()`` is: the file never changes at
runtime, and it was re-parsed on every bulletin render (SNOW-1344). Because
every caller now shares the one result, it is returned as a read-only
mapping — a caller that mutated it would change every later render.
"""

import functools
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import yaml

# The YAML lives beside this module.
_GUIDANCE_PATH = Path(__file__).parent / "field_guidance.yaml"


@functools.cache
def load_field_guidance() -> Mapping[str, str]:
    """
    Load field guidance texts from YAML, once per process.

    Returns:
        A read-only mapping keyed by problem_type with stripped plain-text
        values. The values are strings, so nothing nested can be mutated.

    """
    with _GUIDANCE_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return MappingProxyType({k: v["text"].strip() for k, v in data.items()})
