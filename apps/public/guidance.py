"""Load field guidance notes from YAML."""

from pathlib import Path

import yaml


def load_field_guidance() -> dict[str, str]:
    """
    Load field guidance texts from YAML.

    Returns a dict keyed by problem_type with plain text values.

    """
    path = Path(__file__).parent / "field_guidance.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {k: v["text"].strip() for k, v in data.items()}
