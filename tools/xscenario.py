#!/usr/bin/env python3
"""xscenario.py :display name outprefix : focus click, hover the mode button, move away, hover hint bar; screenshot after each step."""
import sys, time
from Xlib import display, X
from Xlib.ext import xtest
from PIL import Image
disp, name, out = sys.argv[1], sys.argv[2], sys.argv[3]
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
def shot(tag):
    gg = win.get_geometry()
    img = win.get_image(0, 0, gg.width, gg.height, X.ZPixmap, 0xffffffff)
    Image.frombytes("RGBX", (gg.width, gg.height), img.data, "raw", "BGRX").convert("RGB").save(f"{out}-{tag}.png")
    print("shot", tag, gg.width, gg.height, round(time.time(), 3), flush=True)
def move(x, y):
    for k in range(6):
        xtest.fake_input(d, X.MotionNotify, x=ox + x + k, y=oy + y + (k % 2)); d.sync(); time.sleep(0.05)
def click():
    xtest.fake_input(d, X.ButtonPress, 1); d.sync(); time.sleep(0.1)
    xtest.fake_input(d, X.ButtonRelease, 1); d.sync(); print("click", round(time.time(), 3), flush=True)

time.sleep(8); shot("A-start")
import os
if os.environ.get("SCENARIO") == "table":
    move(int(g.width*0.605), int(g.height*0.035)); time.sleep(0.5); click(); time.sleep(1.0); click(); time.sleep(3); shot("T1-table")
    tx, ty = os.environ.get("TX"), os.environ.get("TY")
    if tx and ty:
        move(int(g.width*float(tx)), int(g.height*float(ty))); time.sleep(0.5); click(); time.sleep(3); shot("T2-click")
    print("done", flush=True); sys.exit(0)
import os
if os.environ.get("SCENARIO") == "graphics":
    try: os.remove("/tmp/guard-trigger")
    except OSError: pass
    def dialog_open():
        # the dialog's red close button sits at about (56.3%, 3%) of the screen
        gg = win.get_geometry(); x0, y0 = int(gg.width*0.553), int(gg.height*0.018)
        w0, h0 = int(gg.width*0.02), int(gg.height*0.025)
        img = win.get_image(x0, y0, w0, h0, X.ZPixmap, 0xffffffff)
        px = Image.frombytes("RGBX", (w0, h0), img.data, "raw", "BGRX").convert("RGB")
        red = sum(1 for r, g_, b in px.getdata() if r > 150 and g_ < 90 and b < 90); return red > 20, red
    for attempt in range(4):
        move(int(g.width*float(os.environ.get("AX","0.058"))), int(g.height*float(os.environ.get("AY","0.038")))); time.sleep(0.5); click(); time.sleep(3)
        ok, mean = dialog_open(); print("dialog attempt", attempt, "open" if ok else "not open", round(mean,1), flush=True)
        if ok: break
        time.sleep(3)
    shot("G0-dialog")
    move(int(g.width*0.5), int(g.height*0.085)); time.sleep(0.5)
    open("/tmp/guard-trigger", "w").close(); print("trigger", round(time.time(), 3), flush=True)
    click(); time.sleep(3); shot("G1-graphics")
    try: os.remove("/tmp/guard-trigger")
    except OSError: pass
    def drag(x0, y0, x1, y1):
        move(x0, y0); time.sleep(0.3)
        xtest.fake_input(d, X.ButtonPress, 1); d.sync(); time.sleep(0.2)
        steps = 20
        for k in range(1, steps + 1):
            xtest.fake_input(d, X.MotionNotify, x=ox + x0 + (x1 - x0) * k // steps, y=oy + y0 + (y1 - y0) * k // steps); d.sync(); time.sleep(0.03)
        time.sleep(0.2); xtest.fake_input(d, X.ButtonRelease, 1); d.sync()
    sx = int(g.width*0.906)
    drag(sx, int(g.height*0.30), sx, int(g.height*0.55)); time.sleep(2); shot("G2-scrolled")
    drag(sx, int(g.height*0.60), sx, int(g.height*0.95)); time.sleep(2); shot("G3-bottom")
    if os.environ.get("WHEEL"):
        move(int(g.width*0.5), int(g.height*0.55)); time.sleep(0.5)
        n = int(os.environ["WHEEL"]); btn = 4 if n < 0 else 5   # negative = wheel up
        for k in range(abs(n)):
            print("wheel", btn, round(time.time(), 3), flush=True)
            xtest.fake_input(d, X.ButtonPress, btn); d.sync(); time.sleep(0.05); xtest.fake_input(d, X.ButtonRelease, btn); d.sync(); time.sleep(0.3)
        time.sleep(0.15); shot("G5-wheel-now")
        time.sleep(2); shot("G6-wheel")
        time.sleep(8); shot("G7-wheel10s")
        if os.environ.get("CPUSAMPLE"):
            import subprocess, glob
            pid = subprocess.run(["pgrep","-x","Qwilight.exe"],capture_output=True,text=True).stdout.split()[0]
            def snap():
                d = {}
                for t in glob.glob(f"/proc/{pid}/task/*"):
                    try:
                        f = open(t+"/stat").read().rsplit(")",1)[1].split(); d[t.rsplit("/",1)[1]] = (int(f[11])+int(f[12]), int(f[19]), f[0])
                    except Exception: pass
                return d
            a = snap(); time.sleep(3); b = snap()
            pstart = int(open(f"/proc/{pid}/stat").read().rsplit(")",1)[1].split()[19])
            for tid,(cpu,st,state) in sorted(b.items(), key=lambda kv: -(kv[1][0]-a.get(kv[0],(0,0,""))[0]))[:4]:
                print("CPUSAMPLE tid", tid, "ticks/3s", cpu-a.get(tid,(0,0,""))[0], "state", state, "started +%.1fs" % ((st-pstart)/100.0), flush=True)
    cx, cy = os.environ.get("CX"), os.environ.get("CY")
    if cx and cy:
        move(int(g.width*float(cx)), int(g.height*float(cy))); time.sleep(0.5); click(); time.sleep(1); shot("G4a-combo"); time.sleep(2); shot("G4-combo"); time.sleep(5); shot("G4c-combo"); time.sleep(7); shot("G4d-combo")
        px, py = int(g.width*float(cx)), int(g.height*float(cy))
        raw = win.get_image(px-4, py-30, 8, 1, X.ZPixmap, 0xffffffff).data
        print("raw words near combo:", " ".join("%08x" % int.from_bytes(raw[i:i+4], "little") for i in range(0, len(raw), 4)), flush=True)
        def walk(w, depth=0):
            try:
                for c in w.query_tree().children:
                    ge = c.get_geometry(); at = c.get_attributes()
                    if ge.width in (300, 600) or ge.height in (176, 352):
                        print("xwin 0x%x %dx%d+%d+%d depth %d map_state %d" % (c.id, ge.width, ge.height, ge.x, ge.y, ge.depth, at.map_state), flush=True)
                        try:
                            wi = c.get_image(0, 0, ge.width, ge.height, X.ZPixmap, 0xffffffff)
                            Image.frombytes("RGBA", (ge.width, ge.height), wi.data, "raw", "BGRA").save(f"{out}-xwin-{c.id:x}.png")
                            ri = win.get_image(ge.x, ge.y, ge.width, ge.height, X.ZPixmap, 0xffffffff)
                            Image.frombytes("RGBA", (ge.width, ge.height), ri.data, "raw", "BGRA").save(f"{out}-root-{c.id:x}.png")
                            print("saved xwin/root images", flush=True)
                        except Exception as e: print("img err", e, flush=True)
                    if depth < 3: walk(c, depth+1)
            except Exception as e: print("walk err", e, flush=True)
        walk(d.screen().root)
        cx2, cy2 = os.environ.get("CX2"), os.environ.get("CY2")
        if cx2 and cy2:
            move(int(g.width*float(cx2)), int(g.height*float(cy2))); time.sleep(0.5); click(); time.sleep(3); shot("G5-picked")
            walk(d.screen().root)
        if os.environ.get("XWININFO"):
            import subprocess
            with open(os.environ["XWININFO"], "w") as f:
                subprocess.run(["xwininfo", "-root", "-tree", "-display", os.environ.get("DISPLAY", ":6")], stdout=f, stderr=subprocess.STDOUT)
                for line in open(os.environ["XWININFO"]).read().splitlines():
                    if "PopupWindowSiteBridge" in line or "0x" in line and ("300x176" in line or "600x352" in line):
                        wid = line.split()[0]; f.write("\n== xwininfo " + wid + "\n"); f.flush()
                        subprocess.run(["xwininfo", "-id", wid, "-all", "-display", os.environ.get("DISPLAY", ":6")], stdout=f, stderr=subprocess.STDOUT)
    print("done", flush=True); sys.exit(0)
import os
if os.environ.get("SCENARIO") == "fullscreen":
    move(int(g.width*0.117), int(g.height*0.093)); time.sleep(0.5); click(); time.sleep(3); shot("F0-dialog")
    move(int(g.width*0.573), int(g.height*0.131)); time.sleep(0.5); click(); time.sleep(2.5); shot("F1-visual")
    tx, ty = os.environ.get("TX"), os.environ.get("TY")
    if tx and ty:
        move(int(g.width*float(tx)), int(g.height*float(ty))); time.sleep(0.5); click(); print("toggle", flush=True)
        for i in range(4): time.sleep(2); shot("F%d-after" % (2+i))
    print("done", flush=True); sys.exit(0)
import os
if os.environ.get("SCENARIO") == "name":
    for i, fy in enumerate((0.03, 0.045, 0.06)):
        move(int(g.width*0.125), int(g.height*fy)); time.sleep(0.5); click(); time.sleep(3); shot("N%d-name" % i)
        print("nameclick", i, flush=True)
    print("done", flush=True); sys.exit(0)
import os
if os.environ.get("SCENARIO") == "f7":
    time.sleep(float(os.environ.get("F7WAIT", "25"))); shot("W0-before")
    kc = d.keysym_to_keycode(0xffc4)  # XK_F7
    xtest.fake_input(d, X.KeyPress, kc); d.sync(); time.sleep(0.1); xtest.fake_input(d, X.KeyRelease, kc); d.sync()
    print("F7", round(time.time(), 3), flush=True)
    time.sleep(5); shot("W1-f7-5s"); time.sleep(15); shot("W2-f7-20s")
    def xtree(w, depth):
        for c in w.query_tree().children:
            try:
                g = c.get_geometry(); a = c.get_attributes()
                print("  " * depth + f"xwin 0x{c.id:x} {g.width}x{g.height}+{g.x}+{g.y} map={a.map_state} name={c.get_wm_name()!r} class={c.get_wm_class()!r}", flush=True)
            except Exception as e:
                print("  " * depth + f"xwin 0x{c.id:x} ? {e}", flush=True)
            if depth < 4: xtree(c, depth + 1)
    xtree(d.screen().root, 0)
    print("done", flush=True); sys.exit(0)

if os.environ.get("SCENARIO") == "hover":
    # song list row hover/select: HX/HY are window fractions of a list row (default: the first row)
    hx, hy = float(os.environ.get("HX", "0.45")), float(os.environ.get("HY", "0.275"))
    time.sleep(3); shot("H0-idle")
    move(int(g.width*hx), int(g.height*hy)); time.sleep(1.5); shot("H1-hover")
    click(); time.sleep(1.5); shot("H2-selected")
    move(int(g.width*0.2), int(g.height*0.9)); time.sleep(1.5); shot("H3-away")
    print("done", flush=True); sys.exit(0)

if os.environ.get("SCENARIO") == "settings-wheel":
    ax, ay = int(g.width*0.117), int(g.height*0.093)
    move(ax, ay); time.sleep(0.5); click(); time.sleep(3); shot("S1-dialog")
    def wheel(down):
        b = 5 if down else 4
        xtest.fake_input(d, X.ButtonPress, b); d.sync(); time.sleep(0.03); xtest.fake_input(d, X.ButtonRelease, b); d.sync()
    move(int(g.width*0.5), int(g.height*0.6)); time.sleep(0.5)
    for i in range(12): wheel(True); time.sleep(0.12)
    time.sleep(1.5); shot("S2-wheeldown")
    for i in range(6): wheel(False); time.sleep(0.12)
    time.sleep(1.5); shot("S3-wheelup")
    move(int(g.width*0.35), int(g.height*0.2)); time.sleep(0.3)
    for i in range(8): wheel(True); time.sleep(0.1)
    time.sleep(1.5); shot("S4-wheel-slider")
    print("done", flush=True); sys.exit(0)
import os
if os.environ.get("SCENARIO") == "profile":
    def windows(tag):
        try:
            f = d.get_input_focus().focus
            print(tag, "focus", "0x%x" % f.id if hasattr(f, "id") else f, flush=True)
            for c in d.screen().root.query_tree().children:
                at = c.get_attributes(); ge = c.get_geometry()
                if at.map_state != 2 or ge.width < 2: continue
                name = c.get_wm_name()
                print(tag, "xwin 0x%x %dx%d+%d+%d [%s]" % (c.id, ge.width, ge.height, ge.x, ge.y, name), flush=True)
                if ge.width < g.width and ge.height < g.height:
                    try:
                        wi = c.get_image(0, 0, ge.width, ge.height, X.ZPixmap, 0xffffffff)
                        Image.frombytes("RGBA", (ge.width, ge.height), wi.data, "raw", "BGRA").save(f"{out}-{tag}-xwin-{c.id:x}.png")
                    except Exception as e: print(tag, "xwin image failed", e, flush=True)
        except Exception as e: print(tag, "windows failed", e, flush=True)
    ax, ay = int(g.width*float(os.environ.get("AX","0.117"))), int(g.height*float(os.environ.get("AY","0.093")))
    time.sleep(2); shot("P0-before"); windows("P0")
    if not os.environ.get("CONTROL"):
        move(ax, ay); time.sleep(float(os.environ.get("HOLD", "0.5"))); shot("P0h-hover"); windows("P0h"); click(); print("avatar", ax, ay, flush=True)
        time.sleep(3); shot("P1-profile"); windows("P1"); time.sleep(2); shot("P2-profile")
    # definite XAML changes: switch a tab, hover a list row
    move(int(g.width*0.203), int(g.height*0.085)); time.sleep(0.5); click(); time.sleep(2.5); shot("P5-tab"); windows("P5")
    move(int(g.width*0.5), int(g.height*0.275)); time.sleep(2); shot("P6-hover")
    print("done", flush=True); sys.exit(0)
# keyboard experiment: click the search box (window fractions), type, screenshot
sx, sy = int(g.width*0.76), int(g.height*0.10)
move(sx, sy); time.sleep(0.5); click(); time.sleep(1.0); shot("K0-focus")
def key(code):
    xtest.fake_input(d, X.KeyPress, code); d.sync(); time.sleep(0.05); xtest.fake_input(d, X.KeyRelease, code); d.sync(); time.sleep(0.08)
for code in (38, 39, 40, 65, 53, 29, 30):  # a s d space x u t
    key(code)
time.sleep(1.5); shot("K1-typed")
key(22); time.sleep(1); shot("K2-backspace")
print("searchbox", sx, sy, flush=True); print("done", flush=True)
