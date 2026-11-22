"""Dev tool: write morpheus/gate_labels.json (section titles + field labels/validators of the
6 gate forms' expected.json) for the wake light gate's trigger-breadth lint and smoke test.

  uv run python tools/make_gate_labels.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench_morpheus import EXAMPLES, GATE
from llm_score import truth_from_expected

OUT = Path(__file__).resolve().parents[1] / "src/coi_formularze/morpheus/gate_labels.json"

forms = {}
for slug in GATE:
    t = truth_from_expected(EXAMPLES / slug / "expected.json")
    forms[slug] = [{"title": s["title"], "fields": [{"label": f["label"], "validation": f["validation"]}
                                                     for f in s["fields"]]} for s in t["sections"]]
OUT.write_text(json.dumps(forms, ensure_ascii=False, indent=1) + "\n")
print(OUT, sum(len(s["fields"]) for v in forms.values() for s in v), "labels")
