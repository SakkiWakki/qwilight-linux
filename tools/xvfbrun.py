#!/usr/bin/env python3
"""Run Qwilight on a private Xvfb display with Mesa software GL, start a song, spam the note keys,
then report d2d error lines. Never touches the user's display."""
import subprocess as _sp
if _sp.run(["pgrep","-x","Qwilight.exe"],capture_output=True).returncode==0: raise SystemExit("REFUSED: Qwilight.exe is already running")
import os, sys, time, subprocess, re, collections
HOME = os.path.expanduser('~'); QW = f'{HOME}/dev/qwilight'; PUB = f'{QW}/qwpub-dev'; S = os.path.dirname(os.path.abspath(__file__))
run = int(sys.argv[1]); DISP = ':5'; LOG = f'{QW}/run{run}.log'
env = dict(os.environ, DISPLAY=DISP, WINEPREFIX=f'{QW}/qwpfx-dev', WINE_DISABLE_INPROC_SYNC='1', PULSE_LATENCY_MSEC='60',
           WINEDEBUG=os.environ.get('QWDEBUG','warn+module,+timestamp,+seh,warn+d2d,warn+dxgi,warn+d3d11'), LIBGL_ALWAYS_SOFTWARE='1', __GLX_VENDOR_LIBRARY_NAME='mesa',
           __EGL_VENDOR_LIBRARY_FILENAMES='/usr/share/glvnd/egl_vendor.d/50_mesa.json', GALLIUM_DRIVER='llvmpipe')
WINE = f'{QW}/wine-build/wine'
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)
xvfb = subprocess.Popen(['Xvfb', DISP, '-screen', '0', '2560x1440x24', '+extension', 'GLX', '-nolisten', 'tcp'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
lock = f'{PUB}/yucky/Qwilight.#'
if os.path.exists(lock): os.remove(lock)
logf = open(LOG, 'wb')
p = subprocess.Popen([WINE, 'Qwilight.exe'], env=env, cwd=PUB, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
log('game pid', p.pid, 'on', DISP)
pat = re.compile(r'^(\d+\.\d+):([0-9a-f]+):\w+:(\w+):(\w+)')
class Tail:
    def __init__(s): s.off = 0; s.buf = b''; s.main = None; s.ev = []; s.presents = 0; s.errs = []; s.lastp = None
    def poll(s):
        with open(LOG, 'rb') as f:
            f.seek(s.off); d = f.read(); s.off += len(d)
        parts = (s.buf + d).split(b'\n'); s.buf = parts.pop()
        for raw in parts:
            line = raw.decode(errors='replace'); m = pat.match(line)
            if not m: continue
            t = float(m.group(1)); tid = m.group(2); fn = m.group(4)
            if s.main is None and fn == 'GetProcessGroupAffinity': s.main = tid
            if fn == 'd3d11_swapchain_Present1': s.presents += 1; s.lastp = t
            elif tid == s.main and fn == 'd3d11_swapchain_GetDesc1':
                if not s.ev or t - s.ev[-1][0] > 0.05: s.ev.append([t, 1])
                else: s.ev[-1][1] += 1
            if 'set_error_' in line or ':err:d2d:' in line or ('trace:seh:dispatch_exception' in line and 'code=e0434352' in line): s.errs.append(line[:200])
tail = Tail()
def key(name, hold=0.05):
    subprocess.run(['python3', f'{S}/xkey.py', 'key', name, str(int(hold * 1000))], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
t0 = time.time()
while time.time() - t0 < 240:
    tail.poll()
    if p.poll() is not None: log('game exited'); break
    if len(tail.ev) >= 2 and time.time() - t0 > 25: break
    time.sleep(2)
log('boot events', tail.ev, 'presents', tail.presents)
time.sleep(10)
def click(x, y, b=1):
    subprocess.run(['python3', f'{S}/xkey.py', 'click', str(x), str(y), str(b)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
def shot(name):
    subprocess.run(['python3', f'{S}/shot.py', f'{S}/../{name}.png'], env=env, stdout=subprocess.DEVNULL, timeout=30)
if os.environ.get('QW_PRE'):
    # QW_PRE=<python file>: run before Enter is pressed, on the song-select screen (key/click/shot/log/tail available).
    exec(open(os.environ['QW_PRE']).read())
key('Return'); log('Enter sent')
t0 = time.time(); base = tail.presents; retry = 45
while time.time() - t0 < float(os.environ.get('QW_START_WAIT', '60')):
    tail.poll(); time.sleep(1)
    if tail.presents > base + 60: break
    if time.time() - t0 > retry:
        key('Return'); log('Enter re-sent'); retry += 45
log('presents', tail.presents, 'events', tail.ev)
if os.environ.get('QW_SCRIPT'):
    # QW_SCRIPT=<python file>: run once the song has started.
    exec(open(os.environ['QW_SCRIPT']).read())
elif os.environ.get('QW_AUTO'):
    # QW_AUTO=<seconds>: press F1 (auto mode) so the song keeps playing, take shots every 2 s.
    delay = float(os.environ.get('QW_AUTO_DELAY', '5')); pressed = False
    t0 = time.time(); n = 0
    while time.time() - t0 < float(os.environ['QW_AUTO']):
        if not pressed and time.time() - t0 >= delay:
            key('F1'); pressed = True; log('F1 sent at +%.1f s' % (time.time() - t0))
        time.sleep(0.5 if time.time() - t0 < 12 else 2); n += 1
        subprocess.run(['python3', f'{S}/shot.py', f'{S}/../auto-run{run}-{n}.png'], env=env, stdout=subprocess.DEVNULL, timeout=30)
        log('shot', n, 'at +%.1f s' % (time.time() - t0))
else:
    log('spamming note keys for 14 s')
    t0 = time.time(); i = 0; keys = ['s', 'd', 'f', 'space', 'j', 'k', 'l']
    while time.time() - t0 < 14:
        key(keys[i % 7]); i += 1
subprocess.run(['python3', f'{S}/shot.py', f'{S}/../shot-run{run}.png'], env=env, timeout=30)
tail.poll(); time.sleep(3); tail.poll()
log('presents', tail.presents, 'errors', len(tail.errs))
for e in tail.errs[:40]: print(e)
import collections as C
print('error kinds:', C.Counter(re.sub(r'^[0-9.]+:[0-9a-f]+:', '', e).split(' on context')[0] for e in tail.errs).most_common(10))
p.terminate()
try: p.wait(20)
except Exception: p.kill()
# never end the prefix session while a Qwilight.exe not started by this script is running (the
# user's Steam instance shares the prefix): that killed the user's game once
import shutil
others = subprocess.run(['pgrep', '-x', 'Qwilight.exe'], capture_output=True, text=True).stdout.split()
if others: log('leaving wineserver alone, Qwilight.exe still running:', others)
else: subprocess.run([f'{QW}/wine-build/server/wineserver', '-k'], env=env)
xvfb.terminate()
if os.path.exists(lock): os.remove(lock)
log('done')
