import sys
from Xlib import display, X
from PIL import Image
d = display.Display(); root = d.screen().root
def find(w, depth=0):
    try: n = w.get_wm_name()
    except Exception: n = None
    if n and 'Qwilight AMD64' in str(n): return w
    if depth < 3:
        for c in w.query_tree().children:
            r = find(c, depth + 1)
            if r: return r
w = find(root); g = w.get_geometry()
img = w.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xffffffff)
im = Image.frombytes('RGB', (g.width, g.height), img.data, 'raw', 'BGRX')
im = im.resize((g.width // 2, g.height // 2)); im.save(sys.argv[1]); print('saved', sys.argv[1], g.width, g.height)
