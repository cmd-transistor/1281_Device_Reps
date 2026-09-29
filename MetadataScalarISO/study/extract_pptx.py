import os
import re
import zipfile

z = zipfile.ZipFile("x1281z - ISO E-test Scalar calculations suggestion.pptx")
os.makedirs("study/pptx_media", exist_ok=True)
for n in z.namelist():
    m = re.match(r"ppt/slides/_rels/slide(\d+)\.xml\.rels", n)
    if m:
        rels = z.read(n).decode("utf8")
        imgs = re.findall(r'Target="\.\./media/([^"]+)"', rels)
        print("slide", m.group(1), "->", imgs)
for n in z.namelist():
    if n.startswith("ppt/media/"):
        with open("study/pptx_media/" + os.path.basename(n), "wb") as f:
            f.write(z.read(n))
