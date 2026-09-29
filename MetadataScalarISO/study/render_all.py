import sys
sys.path.insert(0, "study")
import render_crop as rc

rc.LAYERS.pop((120, 0), None)
rc.LAYERS.pop((241, 0), None)

for inst, tag in [(1, "A"), (4, "B"), (7, "C"), (10, "D")]:
    sys.argv = ["x", f"extracted/ISO-GDSNamed/x81as_ctg_test_inst{inst}.txt",
                "1800", "5300", "7300", "10900", f"study/inst{inst}_{tag}_zoom.png"]
    rc.main = rc.main  # noqa
    # patch order to skip M0
    src = open("study/render_crop.py").read()
    ns = {}
    exec(src.replace('order = [(1, 0), (8, 0), (184, 0), (2, 0), (5, 0), (120, 0), (241, 0), (32, 0), (31, 0)]',
                     'order = [(1, 0), (8, 0), (184, 0), (2, 0), (5, 0), (32, 0), (31, 0)]')
         .replace('if __name__ == "__main__":\n    main()', ''), ns)
    ns["LAYERS"].pop((120, 0), None); ns["LAYERS"].pop((241, 0), None)
    ns["main"]()
