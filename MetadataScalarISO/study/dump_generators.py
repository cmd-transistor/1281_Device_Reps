"""Print docstrings and top-level definitions of the generators embedded in the *_LLM.bat launchers."""

import base64
import re
import sys

BATS = {
    "MLR": r"Supporting Files for Visualization\MLR_LLM.bat",
    "PFT": r"Supporting Files for Visualization\PFT_LLM.bat",
    "ISO": r"Supporting Files for Visualization\ISO_LLM.bat",
}
wanted = sys.argv[1:] or ["claude_mlr_generator.py", "claude_pft_generator.py", "mlr_gui.py", "pft_gui.py", "iso_gui.py"]
for bat in BATS.values():
    for line in open(bat, "rb"):
        if not line.startswith(b"::DATA::"):
            continue
        name, _sep, data = line[8:].rstrip(b"\r\n").partition(b"|")
        if name.decode() not in wanted:
            continue
        src = base64.b64decode(data).decode("utf-8", "replace")
        doc = re.match(r'\s*(?:#.*\n)*\s*"""(.*?)"""', src, re.S)
        print("#####", bat.split("\\")[-1], name.decode(), len(src.splitlines()), "lines")
        print(doc[1][:3500] if doc else src[:1200])
        print("defs:", ", ".join(re.findall(r"^(?:class|def) (\w+)", src, re.M))[:1800])
        print()
