#!/usr/bin/env python3
"""xhover.py :display name seconds : move the pointer around the named window (XTest) for N seconds."""
import sys, time, math, os
from Xlib import display, X
from Xlib.ext import xtest
disp, name, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
d = display.Display(disp); root = d.screen().root
def find(w):
    try: n = w.get_wm_name()
    except Exception: n = None
    if n and name in n: return w
    for c in w.query_tree().children:
        r = find(c)
        if r: return r
win = None; t0 = time.time()
while not win and time.time() - t0 < 60:
    win = find(root); time.sleep(1)
if not win: print("window not found"); sys.exit(1)
g = win.get_geometry(); tr = win.translate_coords(root, 0, 0); ox, oy = -tr.x, -tr.y
print("window", hex(win.id), g.width, g.height, "at", ox, oy, flush=True)
# hover targets in window fractions: mode buttons row, search box, list, hint bar, comment tabs
targets = [(0.15, 0.33), (0.55, 0.20), (0.35, 0.50), (0.60, 0.92), (0.75, 0.42), (0.05, 0.24), (0.36, 0.60)]
t0 = time.time(); i = 0
while time.time() - t0 < secs:
    fx, fy = targets[i % len(targets)]
    x, y = ox + int(g.width * fx), oy + int(g.height * fy)
    for k in range(6):
        xtest.fake_input(d, X.MotionNotify, x=x + k, y=y + (k % 2)); d.sync(); time.sleep(0.05)
    time.sleep(0.6)
    if i % 3 == 1 and not os.environ.get('XHOVER_NOCLICK'):
        xtest.fake_input(d, X.ButtonPress, 1); d.sync(); time.sleep(0.1)
        xtest.fake_input(d, X.ButtonRelease, 1); d.sync()
        print("click at", x, y, round(time.time(), 3), flush=True)
    if os.environ.get('XHOVER_STAY') and i % 3 == 1: time.sleep(float(os.environ['XHOVER_STAY']))
    time.sleep(0.3); i += 1
print("done", flush=True)
