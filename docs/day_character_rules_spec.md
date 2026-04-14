# Day Character Rules — Implementation Spec

## Overview

Implemented as `compute_day_character(render_model) -> str` in
`pipeline/services/render_model.py`. Takes a render model dict (as produced
by `build_render_model`) and returns one of five string labels. This was
task 13 in the SnowDesk design doc: compute labels for all historical
bulletins and verify the distribution looks sensible before surfacing them
in the UI.

## Output labels (exact strings)

- `"Stable day"`
- `"Manageable day"`
- `"Hard-to-read day"`
- `"Widespread danger"`
- `"Dangerous conditions"`

## Rules cascade

Evaluate in order. Return the label for the first rule that matches.

### Rule 1 — Dangerous conditions
```
danger_rating >= 4
```

### Rule 2 — Hard-to-read day
```
danger_rating >= 2
AND any problem across all render_model.traits[*].problems has problem_type in:
    {"persistent_weak_layers", "gliding_snow"}
```

### Rule 3 — Widespread danger
```
danger_rating == 3
AND any of:
    - total unique aspects across all flattened problems >= 6
    - any problem has elevation.lower <= 2000
    - total flattened problem count >= 2
```

### Rule 3b — Widespread danger (subdivision)
```
danger_rating == 3 AND danger_rating is upper subdivision (3+)
```
SLF publishes danger level 3 with an optional subdivision indicating the
upper half of the level. Check the CAAML source for the relevant field —
likely `dangerRating.mainValue` with a modifier, or a separate
`dangerRating.tendency` or `dangerRating.highlight` field. If the
subdivision field exists and indicates upper 3, apply this rule.

### Rule 4 — Manageable day
```
danger_rating in {2, 3}
AND no earlier rule matched
```

### Rule 5 — Stable day
```
danger_rating == 1
OR (danger_rating == 2 AND all problems have problem_type == "no_distinct_avalanche_problem")
```

## Implementation notes

- All five rules are mutually exclusive by construction — evaluate top to
  bottom and return on first match.
- If no rule matches (should not happen with valid data), return `"Stable day"`
  as the safe default.
- The function is pure — no side effects, no database calls. Derive
  everything from the render model dict passed in.
- Use type annotations throughout.

## Calibration task

Run against all stored bulletins and print a distribution:

```python
from collections import Counter
from pipeline.models import Bulletin
from pipeline.services.render_model import compute_day_character

labels = [compute_day_character(b.render_model) for b in Bulletin.objects.all()]
for label, count in Counter(labels).most_common():
    print(f"{label}: {count}")
```

Expected healthy distribution for a full Swiss winter season:
- `Stable day` and `Manageable day` — majority of days
- `Hard-to-read day` — clustered around persistent weak layer events,
  typically mid-winter
- `Widespread danger` — less frequent
- `Dangerous conditions` — rare (Level 4/5 days are uncommon in most regions)

If `Dangerous conditions` or `Widespread danger` dominate, the rules need
recalibration before the labels are surfaced in the UI.

## What to do with the results

Do NOT render the labels in the UI yet. Just run the distribution check and
report back. Task 14 (adding the label to the card) depends on the
distribution looking correct.
