import sys
import time
from pathlib import Path

mode = sys.argv[1]
out = Path("out")
if mode == "sleep":
    time.sleep(60)
elif mode == "ok":
    out.mkdir(exist_ok=True)
    (out / "patch.diff").write_text(
        "--- a/note.txt\n+++ b/note.txt\n@@ -1 +1,2 @@\n hello\n+world\n",
        encoding="utf-8",
    )
    (out / "pr.md").write_text("Fix note\n\nBody here.", encoding="utf-8")
# any other mode: exit doing nothing (missing patch case)
