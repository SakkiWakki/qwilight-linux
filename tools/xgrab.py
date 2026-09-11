#!/usr/bin/env python3
"""xgrab.py :display name count interval outprefix : grab a window's pixels by WM name, repeatedly."""
import sys, time
from Xlib import display, X
disp, name, count, interval, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
d = display.Display(disp); root = d.screen().root
def find(w, depth=0):
    try:
        n = w.get_wm_name()
    except Exception:
        n = None
    if n and name in n: return w
    for c in w.query_tree().children:
        r = find(c, depth+1)
        if r: return r
    return None
win = find(root)
if not win: print("window not found"); sys.exit(1)
g = win.get_geometry(); print("window", hex(win.id), g.width, g.height)
try:
    from PIL import Image
except ImportError:
    Image = None
for i in range(count):
    t = time.time()
    img = win.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xffffffff)
    data = img.data
    if Image:
        im = Image.frombytes("RGBX", (g.width, g.height), data, "raw", "BGRX").convert("RGB")
        im.save(f"{out}-{i:02d}.png")
    else:
        open(f"{out}-{i:02d}.raw", "wb").write(data)
    print(f"{i:02d} {t:.3f} {len(data)} bytes")
    time.sleep(interval)
