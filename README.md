# qwilight-linux

Unofficial way to run **[Qwilight](https://store.steampowered.com/app/1910130/)**
(the 2.0 beta, WinUI 3 / Windows App SDK) on Linux through a patched Wine.

This is **not** an official Qwilight project. Report issues here in the repo directly!

This was built and tested on only my machine (Arch, 5090 with the proprietary driver, 9800X3D, KDE Plasma on Wayland). Since it was developed with the help of an LLM, so changes made here can only really serve as a **prototype** for any future work on Wine's actual upstream (and of course for playing Qwilight in the meantime). For example, some of the code also contains weird shortcuts that could potentially cause race conditions and deadlocks in e.g. the rendering loop. My goals were to just learn about aspects of Windows internals for my other GUI projects while being able to play Qwilight comfortably on Linux.

Fun fact:
LLMs are still not good enough to debug parts of this extension by clankselves!!!

## Stuff that needs work

* Steam features are off: plain Wine has no Steam client bridge, I want to investigate porting over to Proton later after implementing 
* Storage contention esp. on an HDD can cause massive slowdowns to the point where startup just displays nothing for an entire minute until the Window finally appears (only for the Beta though). If you don't see a window surface appear within at least 2-5 minutes you should open a bug report along with any information you can gather.
* Full-screen toggling may be unreliable. It runs on my machine but I haven't tested it on vms or containers
* Untested on AMD/Intel GPUs, on X11 sessions, and with any other distribution.
* You need to source the Windows fonts for icons directly or make one yourself.

## Layout

| Path | What |
|---|---|
| `wine/wine-qwilight.patch` | Serves as a diff to compare againist base wine |
| `compat-tool/` | A Steam compatibility tool that launches the game with the patched Wine. |
| `setup/prefix-setup.sh` | Creates the Wine prefix: WebView2 runtime, DirectX shader compiler, icon fonts, animation manager registration. |
| `docs/FIXES.md` | The running writeup of every problem found and how it was fixed or diagnosed (long, technical, in the order it happened). Written by an LLM and kept because I wanted a reference to see what potential work could be contributed to Wine upstream for Qwilight. Though some items may not be accurate and someone needs to yell at Yucky to clean it up himself when he has time. |
| `tools/` | Headless test harness (Xvfb + llvmpipe scenarios with screenshots) and small probes. |

The Wine source itself is published as a fork branch: **`qwilight`** in
[SakkiWakki/wine](https://github.com/SakkiWakki/wine). It is wine-11.16 + wine-staging + the
bring-up commits + the patch above, i.e. exactly what `wine-qwilight.patch` applies to plus that
patch.

## Setup (written by a clanker)

### 1. Build the Wine fork

Install the usual Wine build dependencies for your distribution (your distro's `wine` package
build recipe lists them; the new WoW64 mode is used, so **no 32-bit libraries are needed**).

```sh
git clone --branch qwilight --depth 1 https://github.com/SakkiWakki/wine.git ~/qwilight-linux/wine
mkdir ~/qwilight-linux/wine-build && cd ~/qwilight-linux/wine-build
../wine/configure --enable-archs=x86_64,i386 --disable-tests --without-opencl
make -j$(nproc)
```

Nothing is installed; the build directory is used in place. The paths above are the launcher's
defaults; anything else goes into `compat-tool/config.sh` (see the top of `compat-tool/run`).

### 2. Create the prefix

```sh
setup/prefix-setup.sh ~/qwilight-linux/wine-build ~/qwilight-linux/prefix
```

The script needs, in its directory or given on the command line:

* `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` — the **Evergreen Standalone Installer (x64)** from
  <https://developer.microsoft.com/en-us/microsoft-edge/webview2/>. The game's downloader and help
  pages are WebView2. Version 152 was used here. The runtime's own `d3dcompiler_47.dll` is then
  copied into the prefix and used instead of Wine's (the WinUI compositor links shaders with an
  API Wine's compiler lacks).
* `SegoeIcons.ttf`, `segmdl2.ttf`, `SEGUISYM.TTF` from a Windows installation's `C:\Windows\Fonts`. They are Microsoft's and
  cannot be redistributed here; without them the UI shows boxes instead of icons. Do **not**
  install the Segoe UI text family, it breaks text layout (`docs/FIXES.md`, "Glyph findings").

### 3. Install the Steam compatibility tool

```sh
mkdir -p ~/.steam/root/compatibilitytools.d
cp -r compat-tool ~/.steam/root/compatibilitytools.d/qwilight-wine
```

Restart Steam, switch Qwilight to the **beta** branch (the 2.0 beta; the 1.x build's D3DImage
canvas does not work on Wine), then in the game's properties choose compatibility tool
**"Qwilight Wine"**. Launch options are not required. Useful ones:

| Launch option | Effect |
|---|---|
| `QWILIGHT_WINEDEBUG=warn+module,+timestamp %command%` | A Wine log in `compat-tool/qwilight-wine.log` (default is silent; the log is overwritten per launch). |
| `WINE_WHEEL_SCALE=5 %command%` | Windows-like wheel scrolling amount. |
| `QWILIGHT_KEEP_STEAMAPPID=1 %command%` | Keep the Steam app id (the game will exit unless a Steam client bridge exists). |
| `QWILIGHT_INPROC_SYNC=1 %command%` | Re-enable in-process synchronisation (ntsync); hangs at boot here. |

The game's data (settings, chart database, skins) lives in `[user]/` next to `Qwilight.exe`, as on
Windows. If a launch exits at once with no window, delete a stale `[user]/Qwilight.#` left by a
killed run.

## Diagnostics

Every diagnostic in the Wine tree is gated by a `WINE_*` environment variable and listed in
`docs/HANDOFF.md` under "Remove before any commit". You probably do not need to run any of those unless you want to help Yucky (the guy publishing this repo) debug.