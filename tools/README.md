Headless harness used during the bring-up (Xvfb + Mesa llvmpipe, python-xlib, ImageMagick).
The scripts carry the developer's paths (`~/dev/qwilight`, the Steam library on `/mnt/Yucky`)
at their top; adjust before use. The FIXES.md writeup explains them where they were used.

* `betahl10.sh <log> <secs>` / `betahl10-hover.sh` / `betahl10-scenario.sh`: launch the game on
  Xvfb :6, screenshot every 10 s; the scenario variant drives `xscenario.py`
  (`SCENARIO=graphics|hover|profile|table|f7|...`).
* `xvfbrun.py <n>`: regression run that must end with 0 errors.
* `startup-sampler.sh`: CPU/thread sampler for the first minute.
* `glyphscan.py`: which glyphs the game's skins and UI need and which fonts cover them.
* `listwin.c`: dump every window with style/parent/pid (mingw; run inside the prefix).
* `cdpnav.py <url>`: navigate a WebView2 page through Chromium's DevTools port
  (`--remote-debugging-port=9222` in the browser arguments).
* `addvisual.c`: DirectComposition AddVisual re-parenting test.
