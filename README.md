# qwilight-linux

Unofficial, community-made way to run **[Qwilight](https://store.steampowered.com/app/1910130/)**
(the 2.0 beta, WinUI 3 / Windows App SDK) on Linux through a patched Wine.

This is **not** an official Qwilight project. Report issues here in the repo directly.

It is a **prototype** built and tested on exactly one machine (Arch-based, NVIDIA RTX 5090 with the
proprietary driver, Ryzen 9800X3D, KDE Plasma on Wayland/XWayland). It contains hacks and
diagnostics, it was developed with the help of an AI assistant against this one game, and none of
it can be sent to Wine upstream in this form. Expect rough edges.

## What works

* The game boots to song select, loads the library and skins, plays charts with keyboard input,
  renders BGA/skin videos, and its settings, difficulty tables, profile menu and mouse wheel
  scrolling work.
* The song downloader (F7), help pages and level votes: the embedded WebView2 pages render
  (Chromium's frames cross into the game through a custom transport, see `docs/FIXES.md`,
  "F7 downloader").
* Web traffic in general (`windows.web.http` on winhttp).

## What does not (yet)

* Steam features are off: plain Wine has no Steam client bridge, so the launcher hides the app id
  (the game would otherwise exit when `SteamClient.Init()` fails). No cloud saves, friends or
  overlay.
* Start-up is slow (a minute or more with a large library) and the UI can stall while the library
  loads. Cause and a possible remedy are in `docs/FIXES.md` ("Thread priorities"): allow negative
  niceness for your user (`/etc/security/limits.d/`, `<user> - nice -11`) so Wine can raise the
  UI threads the way Windows does.
* Full-screen toggling is unreliable; run windowed.
* Mouse-wheel scrolling moves a fifth of the Windows amount; `WINE_WHEEL_SCALE=5` compensates.
* Untested on AMD/Intel GPUs, on X11 sessions, and with any other distribution. The Vulkan
  wined3d backend is not used; Wine's OpenGL path with the GPU compositor is.

The complete list, with status, is in `docs/HANDOFF.md` ("Smaller open items").

## Layout

| Path | What |
|---|---|
| `wine/wine-qwilight.patch` | The last, uncommitted part of the Wine work as one diff against `wine/BASE_COMMIT` (a commit on the fork branch below). Kept here so the change is reviewable as a whole. |
| `compat-tool/` | A Steam compatibility tool ("Qwilight Wine") that launches the game with the patched Wine. |
| `setup/prefix-setup.sh` | Creates the Wine prefix: WebView2 runtime, DirectX shader compiler, icon fonts, animation manager registration. |
| `docs/FIXES.md` | The running writeup of every problem found and how it was fixed or diagnosed (long, technical, in the order it happened). |
| `docs/HANDOFF.md` | State of the work, tooling, house rules and open items. Written for whoever continues it. |
| `tools/` | Headless test harness (Xvfb + llvmpipe scenarios with screenshots) and small probes. |

The Wine source itself is published as a fork branch: **`qwilight`** in
[SakkiWakki/wine](https://github.com/SakkiWakki/wine). It is wine-11.16 + wine-staging + the
bring-up commits + the patch above, i.e. exactly what `wine-qwilight.patch` applies to plus that
patch.

## Setup

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
* `SegoeIcons.ttf`, `segmdl2.ttf`, `SEGUISYM.TTF` — Segoe Fluent Icons, Segoe MDL2 Assets and
  Segoe UI Symbol, from a Windows installation's `C:\Windows\Fonts`. They are Microsoft's and
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

The game's data (settings, chart database, skins) lives in `yucky/` next to `Qwilight.exe`, as on
Windows. If a launch exits at once with no window, delete a stale `yucky/Qwilight.#` left by a
killed run.

## Diagnostics

Every diagnostic in the Wine tree is gated by a `WINE_*` environment variable and listed in
`docs/HANDOFF.md` under "Remove before any commit". The ones that paid off most:
`WINE_RO_DUMP_HRESULT=all` (every WinRT error the app raises, with its thread),
`WINE_DUMP_BACKTRACE=<module>` (stack of every XAML failure capture), the compositor frame
statistics, and for WebView2 the Chromium log/NetLog/DevTools switches described in
`docs/FIXES.md` ("F7 downloader"). `tools/` has the headless harness the whole thing was developed
with; it needs Xvfb, ImageMagick and python-xlib.

## License

The Wine patches are under Wine's license, the GNU LGPL 2.1 (`LICENSE`). The scripts and
documents in this repository are under the same license. Qwilight itself is Taehui's; nothing of
the game is included here.
