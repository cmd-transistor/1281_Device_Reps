"""Render pptx slides to PNG via PowerPoint COM (study aid, Windows only)."""
import sys
from pathlib import Path

import win32com.client  # type: ignore

src = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
app = win32com.client.Dispatch("PowerPoint.Application")
pres = app.Presentations.Open(str(src), WithWindow=False)
for i in [int(x) for x in sys.argv[3:]] or range(1, pres.Slides.Count + 1):
    pres.Slides(i).Export(str(out / f"slide{i}.png"), "PNG", 1600, 900)
pres.Close()
app.Quit()
print("ok")
