# Qwilight on Wine — fix log

Every change made to get Qwilight (Steam 1910130) running on Linux/Wine, newest
first. Wine tree: `~/dev/qwilight/wine`, branch `qwilight-bringup`.
Build with `cd ~/dev/qwilight/wine-build && make -j8` (always a full make).

---

## How to run

```bash
cd ~/dev/qwilight/qwpub-dev
rm -f yucky/Qwilight.#          # stale single-instance lock if a run was killed
setsid nohup env \
  WINEPREFIX=~/dev/qwilight/qwpfx-dev \
  WINE_DISABLE_INPROC_SYNC=1 \
  PULSE_LATENCY_MSEC=60 \
  ~/dev/qwilight/wine-build/wine Qwilight.exe > ~/dev/qwilight/run.log 2>&1 < /dev/null & disown
```

`WINE_DISABLE_INPROC_SYNC=1` is currently **required** — see "Boot hang" below.

---

## 2026-09-03 — Keyboard input in-song and in key binding

**Commit** `6f980874` win32u: Include QS_RAWINPUT in the GetMessage wait mask.

**Symptom.** Keys worked in the menus but did nothing in-song, and the key
binding screen never registered a keypress ("Last input received" stayed empty).

**Why the two differ.** Qwilight has two independent keyboard paths:

| Path | Mechanism | Used by |
|---|---|---|
| Menus | WPF `KeyDown` on the main window, ordinary `WM_KEYDOWN` | song select, dialogs |
| Gameplay + key binding | Raw Input only | `System/DefaultControllerSystem/DefaultSystem.cs` |

`DefaultSystem` creates a hidden window on a dedicated thread, registers a
keyboard sink with `RIDEV_INPUTSINK`, and pumps it with
`GetMessage(&msg, _handle, WM_INPUT, WM_INPUT)`. Everything gameplay-related
funnels through `DefaultControllerSystem.HandleInput`, so when that loop is
starved, both in-song input and binding capture die while menus stay fine.

**Root cause (a genuine Wine bug).** `NtUserGetMessage` in
`dlls/win32u/message.c` derives its peek/wait mask from the requested message
range and had no case for the raw input messages:

```c
if ((first <= WM_KEYLAST) && (last >= WM_KEYFIRST)) mask |= QS_KEY;
if (mouse ranges ...)                               mask |= QS_MOUSE;
if (WM_TIMER / WM_SYSTIMER / WM_PAINT ...)          mask |= QS_TIMER / QS_PAINT;
```

`WM_INPUT` is `0x00ff`, immediately below `WM_KEYFIRST` (`0x0100`), so it fell
through every case. A filtered `GetMessage(hwnd, WM_INPUT, WM_INPUT)` waited on
`QS_POSTMESSAGE | QS_SENDMESSAGE` only: the thread was never woken for
`QS_RAWINPUT`, and `peek_message()` never returned the already-queued hardware
message. The loop blocked forever. `PeekMessage` is unaffected because it
passes no mask, which is why an unfiltered pump sees the same messages fine.

**Fix.** One line in `NtUserGetMessage`:

```c
if ((first <= WM_INPUT) && (last >= WM_INPUT_DEVICE_CHANGE)) mask |= QS_RAWINPUT;
```

**How it was isolated.** Three ~60-line test programs, no game involved
(sources kept in the session scratchpad):

| Test | Shape | Result |
|---|---|---|
| `rawtest` | hidden sink, no Wine window focused | 0 messages (Wine only dispatches raw input when a Wine window is foreground) |
| `rawtest2` | visible focused window + hidden sink, unfiltered `PeekMessage` | works |
| `rawtest3` | visible focused window + sink on its own thread, filtered `GetMessage` — the game's exact shape | 0 before the fix, 194 after |

**Upstreamable.** Yes, this is not Qwilight-specific. Any app using a filtered
`GetMessage` for `WM_INPUT` is affected.

---

## 2026-09-11 — Boot hang with ntsync: FIXED (server-side waiters for wait completion packets)

`server/inproc_sync.c`: `create_inproc_waiter()` / `cancel_inproc_waiter()` /
`inproc_sync_try_wait()`; `server/completion.c`: `associate_completion_packet`
takes the inproc branch, `cancel_completion_packet` cancels the waiter, the
completion step is factored into `complete_packet()`. Design: a wait completion
packet on an inproc object becomes a real ntsync waiter (which also gives the
NT consuming semantics for auto-reset events and semaphores). A detached
helper thread in wineserver blocks in `NTSYNC_IOC_WAIT_ANY` on a dup of the
object fd with a per-waiter manual-reset event as the alert; it touches
nothing but its fds and reports back through a pipe polled by the main loop.
If a cancelled waiter loses the race and consumes a signal, the main thread
puts it back (`EVENT_SET` / `SEM_RELEASE`). Verified 3/3 headless boots with
ntsync, and `scratchpad/wcptest.c` (deferred auto-reset event, already
signalled manual event, cancel, semaphore, thread exit, close-while-pending,
16 packets on one event) passes in both sync modes. Run scripts no longer set
`WINE_DISABLE_INPROC_SYNC`; `QWILIGHT_DISABLE_INPROC_SYNC=1` is the opt-out.

## 2026-09-03 — Boot hang (diagnosed, worked around; fixed 2026-09-11, see above)

**Symptom.** From run40 onward, launches hung on the splash at ~650 log lines.
Intermittent-looking, but it is not a race.

**Cause.** In-process synchronisation (`/dev/ntsync`). A 10-run A/B was
decisive:

| Sync mode | Runs | Booted |
|---|---|---|
| in-process sync (default) | 5 | 0 |
| `WINE_DISABLE_INPROC_SYNC=1` | 5 | 5 |

**Workaround (superseded).** Set `WINE_DISABLE_INPROC_SYNC=1`. The run
scripts did this until the 2026-09-11 fix.

**Where the real fix belongs.** The hung main thread sits in a two-handle
`WaitForMultipleObjects` inside `dwmcorei` (`CConnection::Initialize`, reached
from `dcompi` and `Microsoft.ui.xaml`), waiting for the CoreMessaging
DispatcherQueue thread that has already exited. CoreMessagingXP drives its wait
loop through I/O completion ports and wait completion packets
(`NtCreateWaitCompletionPacket` / `NtAssociateWaitCompletionPacket` /
`NtRemoveIoCompletionEx`, via `WaitController::TransferWaitsToIocp`). With
in-process sync, `NtSetEvent` is satisfied entirely client-side through an
ioctl on `/dev/ntsync`, so the server never observes the event becoming
signalled and never runs `wake_up_completion_packets()` for it. The associated
packet is never queued to the completion port and the waiter sleeps forever.
The local wait-completion-packet support came from commit `c234adbd`
(ported from MR6911); its interaction with in-process sync is unhandled.

---

## Earlier fixes (all committed on `qwilight-bringup`)

Listed newest first; see `git log` for the full messages.

| Commit | Area | What it does |
|---|---|---|
| `9db92805` | d2d1 | Border/DpiCompensation/Premultiply/UnPremultiply effects, effect-chain DrawImage (the note freeze; see end) |
| `5cfbaf12` | winex11 | Never-presented client surfaces no longer steal a window's client window |
| `b9cf0862`, `6ae2c66c` | windows.media | MediaPlayer object model and MediaSource (BGA groundwork) |
| `80119fed` | ws2_32 | `WSALookupServiceBegin` stubs return `WSASERVICE_NOT_FOUND` |
| `a2ae8d3a` | combase | Real WinRT originated-error APIs |
| `e8c29286` | dxgi | Host composition swapchains next to the XAML island content bridge (see caveat below) |
| `23c81a86` | d2d1 | Share shaders, input layouts, state objects and quad buffers between device contexts (GLSL compiles 27.7k -> 279 per run) |
| `2586d009` | coremessaging | Reject NULL arguments in `IMessageRegistrar::QueryInterface` |
| `0df34189` | shcore | Read through the stream's own `IInputStream`, keep async handlers alive |
| `bc9c1437` | dxgi | Implement `IDXGIOutput::WaitForVBlank` as a timed wait (was `E_NOTIMPL` -> 46k caught exceptions per run) |
| `02766928` | shcore | Implement `CreateStreamOverRandomAccessStream` (Win2D bitmap loading) |
| `7cb85b70` | windows.devices.midi | New DLL with `MidiInPort`/`MidiOutPort` statics; boot previously died on `CLASSNOTREG` |
| `b2194753` | windows.devices.enumeration | `DeviceClass` overloads of `FindAllAsync` and `CreateWatcher` |
| `2bf216e9` | dxgi | Swapchain matrix transforms + private DWM partner swapchain interface |
| `9e41f204` | d2d1 | Port the private DWM device and device-context interfaces from the React Native branch |
| `745561f9` | d3d11 | Private DWM partner device interface `{26c5dc23-...}` (dcompi dereferenced it unchecked) |
| `13d16531` | user32 | Private window-band exports used by WinAppSDK; ordinal 2574 = `IsShellManagedWindow` must return FALSE |
| `886d8c41` | d2d1 | Private DWM factory interface `{6f72c0a2-...}` |
| `c08733ac` | kernelbase | Return success from `WerRegisterMemoryBlock`/`WerUnregisterMemoryBlock` (dwmcorei `DllMain` used WIL `RETURN_IF_FAILED`) |
| `45ca8b11` | ntdll | WNF query/subscribe stubs, `RtlGetDeviceFamilyInfoEnum`, `RtlIsMultiSessionSku` |
| `604ef4b8` | twinapi.appcore | Implement `ICoreApplication` on the CoreApplication factory |
| `fa9f07f2` | windows.ui | `IUISettings6` |
| `8693ae89` | windows.storage.applicationdata | ApplicationData Local/Roaming/Temporary folders |
| `092e29d9` | d3d11 | `CreateDirect3D11DeviceFromDXGIDevice` / `...SurfaceFromDXGISurface` |
| `75e80ebe` | bcp47mrm | New DLL with `IsWellFormedTag` |
| `bec96709` | coremessaging | Native CoreUI message session |
| `7c7e56c0` | twinapi.appcore | Undocumented CoreApplication `{17b0e613}` interface |
| `c234adbd` | ntdll/server | WaitCompletionPacket support (ported from MR6911) |
| `aecc722a`, `ae08f4ab` | ntdll | `NtAlpcDisconnectPort`, `NtAlpcSendWaitReceivePort` |

Also fixed but worth calling out: `twinapi.appcore`
`IStaticLifetime::UnregisterUnhandledErrorHandler` takes its token **by value**;
the pointer prototype wrote to address 1 and caused an access violation on exit.

---

## Known-open issues

**Missing glyphs (emoji render as tofu boxes).** Fixed in session 5 (dwrite
commit after `72c1a4b7`: fallback rows for 2300-23FF and 1F300-1F9FF; `tools/dwtest.cpp`
verifies the four icons map to Noto Sans Symbols 2 / Unifont Upper). Original notes:
Not a font-installation
problem. `Configure.json` sets all four font slots to `Century Gothic`, which
does not exist on Linux, so text falls back. The characters that fail are
emoji, used by the game as icons: `U+2328` keyboard, `U+1F579` joystick,
`U+1F3B9` musical keyboard, `U+1F503`. Wine's DirectWrite fallback table in
`dlls/dwrite/analyzer.c` has entries for `2600-26FF`, `2700-27BF` and
`2B00-2BFF`, but **nothing for `2300-23FF` or `1F300-1F5FF`**, so no fallback
font is ever consulted for them. Fix is to add those ranges to the table;
on this host `Noto Sans Symbols 2` covers the joystick, `DejaVu Sans` /
`Noto Sans Symbols` cover the keyboard, and only `Unifont Upper` and
`Noto Color Emoji` cover the musical keyboard.

**Freeze when a note is hit.** Root cause found and fixed in session 5,
commit `9db92805` (see the entry at the end); awaiting the user's confirmation. The
glibc robust-mutex abort mentioned earlier is the separate exit crash.

**Video BGA.** Uses `Windows.Media.Playback.MediaPlayer`; Wine's
`windows.media` is a stub. Will not work without implementing it.

**Composition swapchain hosting is a hack.** `dxgi_create_composition_window()`
creates the swapchain's window as a `WS_CHILD` sibling of the thread's
`DesktopChildSiteBridge` and mirrors its rect and visibility on every
`Present1`. Parenting *into* the bridge fails (WinUI rejects it with
"WindowParentChain invalid state", then `MoveAndResize` returns `E_NOTIMPL`
and boot dies). The proper fix is to port CodeWeavers' dcomp presentation
(`dcomp/{device,surface,visual,target}.c` from `zhiyi/bug-23698-react-native-alpc`
at `5505b7de`).

**Shipped Steam build's game canvas cannot work.** It shares a D3D11 texture to
WPF's D3D9 via `D3DImage`. wined3d has no cross-device sharing, and an isolated
test showed DXVK's d3d9 also rejects it (`E_INVALIDARG`). The dev build replaces
this with a software `WriteableBitmap` copy, which is why testing uses the dev
build.

---

## 2026-09-03 — Stale frame covering the UI after a song ("stuck on the loading screen")

**Commit** `fdd78cec` dxgi: Follow the island bridge from its own messages, not only from Present1.

**Symptom.** After a song finished, the screen stayed on the Qwilight loading
art, but "all the background buttons work".

**Diagnosis.** Not a hang. The Wine-hosted window for the composition swapchain
(the `dxgi_create_composition_window` hack) mirrored the XAML island bridge's
rectangle and visibility **only from inside `Present1`**. When the app stops
presenting, the window keeps its last presented frame on screen, on top of the
WPF content. Its window class returns `HTTRANSPARENT`, so the real UI
underneath still receives every click while being completely invisible — hence
working buttons you cannot see. A window-tree probe confirmed a single
`wine_dxgi_composition` child of the main window, visible and full-screen
(2560x1440), for the whole session.

**Fix.** Subclass the bridge window and re-sync every hosted composition window
under its parent on `WM_SHOWWINDOW`, `WM_WINDOWPOSCHANGED`, `WM_SIZE`,
`WM_MOVE` and `WM_DESTROY`, so visibility follows the island even with no
presents in flight.

**Note.** This is still the hack, not the real solution. The proper fix remains
porting CodeWeavers' dcomp presentation.

---

## 2026-09-03 — WinRT originated-error APIs were stubs

**Commit `a2ae8d3a`, `dlls/combase`.** Not a freeze fix; a performance and
legibility fix that came out of investigating the freeze.

Every in-song log is dominated by two messages in a 1:1 interleave:
`fixme:combase:GetRestrictedErrorInfo` and
`fixme:d2d:d2d_device_create_device_context`. In run54 there are 4485 of the
former in a single song.

**Why it happens.** When a WinRT call fails, the callee records an
`IRestrictedErrorInfo` for the calling thread via `RoOriginateError()`, and the
language projection retrieves it with `GetRestrictedErrorInfo()` to build a
precise exception. Wine implemented `RoOriginateError()` as a stub that recorded
nothing and `GetRestrictedErrorInfo()` as a bare `return E_NOTIMPL`. C#/WinRT
asks after **every** failed call, so an application failing calls inside its
render loop pays the cost per frame.

The failures being asked about are real: Win2D's
`Microsoft.Graphics.Canvas.Text.CanvasTextLayout`,
`...Geometry.CanvasPathBuilder` and `...Geometry.CanvasGeometry` are app-local
and `RoGetActivationFactory` cannot find them, so CsWinRT falls back to
`LoadLibrary` — benign in outcome, expensive in path. Note the ratio: 4485
`GetRestrictedErrorInfo` calls against only 4 `RoOriginateLanguageException`
calls, i.e. the projection asks constantly and almost nothing ever originates.

**The fix.** A real `IRestrictedErrorInfo` implementation holding the HRESULT,
the originated message and any language exception, stored with the thread's COM
data (`struct tlsdata`) exactly the way `SetErrorInfo`/`GetErrorInfo` store an
`IErrorInfo`, and released in `com_cleanup_tlsdata`. `GetRestrictedErrorInfo`
hands the error to the caller and clears it from the thread, returning `S_FALSE`
with a NULL pointer when there is none; `RoOriginateError` rejects success
codes. `RoClearError` is now exported too. Both match Windows.

One build wrinkle worth remembering: including `restrictederrorinfo.h` from
`combase_private.h` breaks the link, because every translation unit that also
includes `initguid.h` then defines `IID_IRestrictedErrorInfo`. Use the widl
forward-declaration guard (`__IRestrictedErrorInfo_FWD_DEFINED__`) in the
private header instead, and cast to `IUnknown *` at the one release site in
`combase.c`.

**Verification.** `scratchpad/retest.c`, a standalone mingw program, checks ten
cases against the documented Windows behaviour: get with nothing stored,
originate, get, `GetErrorDetails` returns the right HRESULT and message,
`GetReference`, hand-off leaves the thread empty, `SetRestrictedErrorInfo`
round-trip returns the same pointer, `RoClearError` empties it, and
`RoOriginateError(S_OK, ...)` returns FALSE. All ten pass.

## 2026-09-07 — Performance under Steam (session 5, later)

Three separate problems, in the order they were found:

1. **Software rendering under Steam.** Steam's `LD_PRELOAD` of
   `gameoverlayrenderer.so` made glvnd fall back to Mesa llvmpipe in this
   Wine (`GL_RENDERER` "llvmpipe" with it, "NVIDIA GeForce RTX 5090" without;
   the perf profile was sixteen llvmpipe threads). The Steam compatibility
   tool (`~/.steam/root/compatibilitytools.d/qwilight-wine/run`) now unsets
   `LD_PRELOAD`, and also `WINEDLLOVERRIDES` and friends: the game's old
   launch options carried `WINEDLLOVERRIDES=dcomp=d`, which made
   `Microsoft.UI.Xaml.dll` unloadable and the game exit at startup.
2. **Vsync on a redirected window.** `cd2ccf8a` winex11: no swap interval
   for offscreen client surfaces; they are blitted, never scanned out.
3. **Per-primitive churn in d2d1 and wined3d.** With the real driver the
   game rendered a fixed 117 fps: 6 ms to build each frame, the render
   thread spinning 38% of its time in `wined3d_cs_queue_require_space`
   waiting for the command stream thread. `+d2d` showed ~150 FillRectangle,
   ~70 DrawBitmap and ~10 glyph runs per frame; `+opengl` showed ~107 GL
   calls per draw, mostly a full pipeline re-application because d2d1
   swapped the device context state around every draw. Fixed in two
   commits after `9db92805` (see `git log`): streaming vertex/index buffers
   plus a shared-quad FillRectangle in d2d1, and BeginDraw/EndDraw-scoped
   state installation in d2d1, a no-op same-state swap in d3d11, a
   uniform-buffer bind cache and viewport dedup in wined3d. Headless
   measurement: 25 GL calls per draw, draws per second doubled.

4. **Presentation path.** `72c1a4b7` winex11: `WINE_X11_ONSCREEN_CHILD_GL=1`
   (set by the Steam launcher; `QWILIGHT_ONSCREEN_CHILD_GL=0` reverts) makes
   the composition child's GL client window a real subwindow of the
   top-level X window. No glFinish, no blit, and the swap interval is
   honoured. Verified headless (run74: zero blits, picture correct) and on the real display:
   a steady ~545 fps at 2 ms intervals on the 500 Hz panel (was a fixed 117 fps).
   Note for later: the native EGL test `tools/egltest.c` showed XWayland +
   NVIDIA swapping 800-1300 times a second on both top-level and child
   windows, vsync not honoured by the driver, so the display pipeline itself
   was never the cap; and WPF's own D3D9 device presents the hidden UI at
   ~480 fps in the menus and ~70 fps behind the game (thread 020c, 94k
   swaps in one session), a game-side waste worth stopping.

Still open on this front: DWrite re-rasterises every glyph run each frame
(`libfreetype` 11% of the render thread) and d2d1 creates a texture per
glyph run per frame; WPF repaints behind the game at ~20 fps; the
composition swapchain is still presented through a composite-redirected
child window with a glFinish and a blit per frame.

Tools: `tools/perfrun.py` (headless perf), the launcher's `QWILIGHT_PERF=1`
(perf on the real display), `tools/xvfbrun.py` with `QWDEBUG=+timestamp,+opengl`
or `+d2d` for call histograms.

## 2026-09-07 — Freeze after every judged note: Win2D effect chain (session 5)

**Commit** `9db92805` d2d1: Add Border, DpiCompensation, Premultiply and
UnPremultiply effects, and draw the source bitmap of an effect chain.
Verified headless in run65 (zero lost frames while spamming note keys; run63
and run64 before it lost 25-45 frames per hit). User confirmation pending.

**Symptom.** After every non-miss judgement the picture stops for 0.3-0.8 s
while audio, input and game logic continue.

**Cause.** The hit-note effect is drawn with `CanvasComposite.Add`. Win2D's
`DrawImage` uses `DrawBitmap` only when the composite matches the session's
primitive blend; otherwise it builds `DpiCompensation -> Border ->
ColorMatrix(opacity)` and calls `ID2D1DeviceContext::DrawImage`. Wine's d2d1
lacked Border and DpiCompensation, `CreateEffect` failed, Win2D threw inside
the drawing session, and the game's render loop caught it, reported to
Sentry and skipped the present, once per frame while the effect was visible.

**Evidence.** Per-frame accounting on the render thread: 87-208 drawing
sessions between two presents (`run61-start-and-fail.log` 32412-32416),
one `GetRestrictedErrorInfo` per session in run54. Headless runs with
`warn+d2d` printed `d2d_effect_create Effect id {2a2d49c0-...} not found`
exactly as often as frames were lost. The instrumented
`d2d_device_context_set_error` (now logs its caller) never fired, ruling out
a d2d error state.

**Fix.** Four stub effects in `dlls/d2d1/effect.c` in the style of the
existing ones; `d2d_effect_get_source_bitmap()` walks an effect chain's
input 0 to the bitmap, multiplying in a ColorMatrix's `_44`;
`d2d_device_context_DrawImage()` draws that bitmap. Additive blending and
real effect rendering are still not implemented, so the hit effect draws
source-over, which is fine.

**Not the cause.** The BGA path, the WinRT activation storm, lock order, the
audio stack, and the result-to-select client-window steal (`5cfbaf12`, a
real but separate bug).

**Tools added** (`~/dev/qwilight/tools/`): `xvfbrun.py` (headless game run
on Xvfb + llvmpipe with key spam and d2d error capture), `wmsize.py`,
`xtrace.py`, `xkey.py`, `activate.js`, `shot.py`, `xtree.py`, `wtree.exe`,
`fg.exe`, `drive.py`, `sendkey.exe`.

## 2026-09-07 — Video BGA renders (session 5, later)

**Symptom.** BGA video never appeared; after the object model existed, the
game opened every source, enabled the frame server and stopped.

**Three separate causes, all fixed.**

1. **`ffprobe.exe` in the dev build was a 134-byte git-lfs pointer**, as are
   the ffmpeg DLLs next to it (`fix-devbuild.sh` reports them as MISSING when
   the Steam copy is absent). The game gets each media's length by running
   `Assets/AMD64/ffprobe.exe` and only calls `MediaPlayer.Play()` while the
   position is below that length, so length 0 meant no BGA ever started.
   Fixed by copying the older working `yucky/Software/ffprobe.exe` and its
   `av*-60`/`sw*` DLLs into `Assets/AMD64/`. Git-lfs needs credentials for
   `vcs.taehui.net`, so the exact 2025 binaries could not be fetched.
2. **No frames** — `MediaPlayer` had no engine behind it. Commits `3ac9254e`
   (windows.graphics.imaging: SoftwareBitmap), `2ba01924` (MediaPlayer on
   `IMFMediaEngineEx` with a frame-server thread; frames go through a WIC
   bitmap and `UpdateSubresource` because the engine has no DXGI device
   manager for the app's device) and `6a850e11` (d2d1 `CopyFromBitmap`
   across D3D devices, which the game uses because it decodes into a bitmap
   on a private `new CanvasDevice()`).
3. **A black 1920x1080 "ActiveMovie Window" covered the screen** whenever the
   song-select preview played: the game also opens the same file through
   WPF's `System.Windows.Media.MediaPlayer`, which on Wine is wmp + quartz,
   and quartz's video renderer pops up its own top-level window. Commit
   `382d7892` (wmp hides the video window when the control has no host).

**Verification.** `tools/xvfbrun.py` with `QW_AUTO=30` and a no-fail gauge
(custom gauge, all deltas 0) shows the EMOJISM `_bga.wmv` behind the side
panels (`auto-run91-21.png`). `tools/xdevcopy.exe` tests the cross-device copy.

**Two things that are NOT Wine bugs, for the user:**
- The QRcircle skin's media opacity (`UIConfigureValuesV2.QRcircle.MediaFaintV2`)
  is **0** in the user's `Configure.json`, which hides the BGA on any OS. It is
  the "media" opacity slider in the skin's settings; the test run set it to 1.
- The song-select background preview (WPF `MediaPlayer` -> wmp) still shows
  nothing: WPF asks for `IWMPVideoRenderConfig` to plug in its own EVR
  presenter, which wmp does not implement. Audio-less, harmless, open.

## 2026-09-08 — Steam launch "opens then closes" (session 6)

**Symptom.** Both Steam launches at 08:32 died 8.5 s after start, at the
fourth `eglSwapBuffers` of the first composition window, with

```
.exe: ../egl-x11/src/x11/x11-window.c:2171: eplX11SwapBuffers: Assertion `sharedPixmap->status == BUFFER_STATUS_IDLE' failed.
```

That assert is inside NVIDIA's `egl-x11` platform library (1.0.6): the back
buffer it is about to present is still marked as held by the X server. The
process aborts; Wine cannot catch it (`err:seh:... Exception frame is not in
stack limits`).

**What it is not.** Environment dumps of the crashing run and of the last good
Steam run (Sep 7 21:27) are identical. egl-x11 1.0.6, kernel 7.2.3-arch1-3 and
Xwayland 24.1.13 were already in use for the good runs (the machine rebooted
at 20:17 on Sep 7 and again at 08:29 on Sep 8). Steam's `LD_LIBRARY_PATH`
shadows no GL, xcb or drm library (`ldd` identical), and the Steam runtime last
changed on Sep 2. The on-screen child GL path (`72c1a4b7`) is not involved:
with `+x11drv` tracing, `create_client_window` only takes the child branch
~34 s into a run, long after the startup swaps where the crash occurs.

**It is intermittent.** Three launches through the same compat-tool script on
the real display after the crash: one clean (100+ swaps), one that never
crashed but sat on a black window with wined3d logging
`wglSetPixelFormatWINE failed to set pixel format 90` every frame (the crash
run shows the same failure once, just before its swaps), one clean with full
tracing. The user's own later Steam launch also ran. Note that at 08:32 a
headless `tools/xvfbrun.py` run from another session was booting the same
dev build in the same prefix; it cannot share X state with the Steam process,
but it is the one difference from the later clean launches.

**Left in place.** `dlls/winex11.drv/opengl.c`: `x11drv_egl_surface_create`
now logs `eglCreateWindowSurface` failures with `eglGetError()` at WARN level
(`+wgl`), so the next occurrence shows why the second window surface for the
composition window failed to create. Nothing else changed on this path.
`QWILIGHT_ONSCREEN_CHILD_GL=0` is not expected to help. No upstream egl-x11
issue mentions this assertion.

**Also fixed: `MediaPlayer.Dispose()` threw `InvalidCastException`.** After
a song preview is closed the game disposes its `Windows.Media.Playback.MediaPlayer`;
CsWinRT does that by querying `IClosable`, which Wine's `media_player`
object did not implement, so `IObjectReference.As<IDisposable>` failed with
`E_NOINTERFACE`. `dlls/windows.media.playback.mediaplayer/main.c` now
exposes `IClosable`; `Close()` stops the frame thread and pauses the media
engine, the rest is released with the last reference as before. Verified with
`closetest-mediaplayer.c` (activate, QI `IMediaPlayer`, QI `IClosable`, Close
twice, final release reaches 0). Both changes are built but **uncommitted**;
the tree also carries an uncommitted, unrelated win32u pointer-input
experiment (`NtUserEnableMouseInPointerForThread`, `DIAG` fixmes in
`message.c`) from the previous session, which was compiled into the binaries
the Steam launches used.

## 2026-09-08 — MediaPlaybackSession freed on every Release (session 6, later)

**Symptom.** The user's 11:02 Steam session died at 11:14:45 with a native
`Fatal error. 0xC0000005` in `MediaHandlerItem.SetMediaPosition` on the
`DefaultCompute.HandleNotes` thread (steam-run.log, that session ran with
`WINEDEBUG=-all`, so only the CLR's own message is there).

**Cause.** `dlls/windows.media.playback.mediaplayer/playback.c`
`session_Release`: the `free( impl )` was outside the `if (!ref)` block, so
every Release of a `MediaPlaybackSession` freed it while the `MediaPlayer`
still held it; the game gets and releases `PlaybackSession` routinely, then
seeks through the freed object. Session-5 code. Fixed, and the session now
takes the engine under a critical section with its own reference in every
method (`session_get_engine`), because the game seeks from its note thread
while disposing the player elsewhere. Verified with
`sessiontest-mediaplayer.c` (build line in its header: it needs Wine's
generated headers, `-I../wine-build/include -I../wine/include`, which is also
why `bgatest-chain.c` does not build against plain mingw headers).

## 2026-09-08 — "PipeWire ALSA clients left behind after closing the game" (open)

The prefix uses `Audio=alsa` (`user.reg`), so every stream is a
`pipewire-alsa` client named after the process; those only outlive the
process if the process outlives its window. Headless (llvmpipe, Xvfb :10,
close via `WM_CLOSE` from `scratchpad/closewin.c`) the 1.x dev build exits in
3 s and leaves no client. Not reproduced on the real display; the user's
sessions from 09:11 on ran with `WINEDEBUG=-all` and three of them show no
shutdown lines at all. `tools/audiocheck.sh` lists live Wine processes and
Wine/ALSA-plug-in PipeWire clients with their owning pid: run it right after
closing the game to see whether a `Qwilight.exe` is still alive (the known
teardown crash on the GL thread, or `winedbg --auto` holding a crashed
process, are the candidates).

## 2026-09-08 — Wave 2: CoreMessaging DispatcherQueue (agent coremessaging-dq)

`coremessaging!GetDispatcherQueueForCurrentThread` was `@ stub`; the resolver is the
app-local `CoreMessagingXP.dll` (rva 0x9368c, LoadLibrary + GetProcAddress with
fail-fast on NULL), signature `HRESULT (IDispatcherQueue **)`, semantics: S_OK + NULL
when the thread has no queue, AddRef'ed queue otherwise (its `EnsureSystemDispatcherQueue`
creates a controller only on NULL). `dlls/coremessaging` now has a real thread-affine
`IDispatcherQueue`/`IDispatcherQueue2` with a thread registry, a message-only window so
`TryEnqueue` runs on the owning thread, `ShutdownQueueAsync`, the
`Windows.System.DispatcherQueue` statics and the export. Test `dqtest.c` (8 scenarios)
passes. The beta's whole Mica dispatcher sequence now runs; it then dies because
`Windows.System.Power.PowerManager` is unregistered (wave 2, agent system-power).
Details: `survey/impl-coremessaging-dq.md`.

## 2026-09-08 — Wave 2: PowerManager, CompositionCapabilities vtable (agent system-power + main)

New `dlls/windows.system.power` with `IPowerManagerStatics` (IID verified from
wuceffectsi.dll, values from `GetSystemPowerStatus`, events never fire); test
`powertest.c` passes. The next fault was ours: `ICompositionCapabilities` in
`include/windows.ui.composition.idl` had the events before the getters, but the
shipped binary calls `AreEffectsFast` at slot 7 and `add_Changed` at slot 8, so
`add_Changed` landed on a getter that wrote a byte through the delegate pointer.
Reordered (IDL + `capabilities.c`). The beta now reaches
`MicaController` -> `SystemCompositorManager::GetSystemCompositor` ->
`ICompositor::CreateColorBrushWithColor` (fallback brush, effects unsupported),
which is a stub in `dlls/windows.ui.composition/compositor.c`: the first real
Windows.UI.Composition object needed. Details: `survey/impl-system-power.md`.

## 2026-09-08 — Wave 2 tail: from XAML markup to a running (black) window (main)

Fixed by hand between agent waves, each found by the previous fix's next failure:
- `user32!SetWindowCompositionAttribute` returned FALSE; FrameworkUdk turns that into E_FAIL
  and XAML fail-fasts on `Window.SystemBackdrop`. Now returns TRUE (nothing stored).
- `uxtheme` lacked `GetThemeAnimationProperty/Transform`, `GetThemeTimingFunction`
  (imported by Microsoft.UI.Xaml; missing export = process kill when called). Added as
  E_NOTIMPL stubs; XAML tolerates the failure.
- `usp10!ScriptSubstituteSingleGlyph` forwarded to a gdi32 export that did not exist, so
  `WinUIEdit.dll` (WinUI 3 TextBox) could not load. Implemented in
  `dlls/gdi32/uniscribe/usp10.c` as "no substitution".
- New stub DLL `dlls/elscore` (Extended Linguistic Services: Mapping*), also imported by
  WinUIEdit.dll.
- XAML's ultimate fallback font: it looks up "Segoe UI Variable" then "Segoe UI" in the
  DWrite system collection and fail-fasts (E_UNEXPECTED in
  `PALFontAndScriptServices::GetUltimateFallbackFontFamily`) if absent. Prefix registry
  `HKCU\Software\Wine\Fonts\Replacements` now maps the Segoe UI family names to
  "Noto Sans" (dwrite honours that key). Wine-side candidate: ship such a default.
- `dxgi!IDXGIFactory7::RegisterAdaptersChangedEvent` was E_NOTIMPL; now S_OK with a
  cookie (never signalled).
- `Windows.ApplicationModel.DesignMode.DesignModeEnabled` was E_NOTIMPL; Win2D's
  CanvasControl constructor reads it and CsWinRT turned E_NOTIMPL into a managed
  NotImplementedException ("Cannot create instance of type CanvasControl"). Now FALSE.
- combase's fail-fast dump prints the stowed exception's captured stack and the current
  stack as module+offset (the stowed-exception struct layout was wrong: the binary form
  carries address + stack fields together). This is what made each of the above findable
  in one run; symbolise with `resolve.py survey/beta.xaml.map <rva>`.

Result: the beta runs indefinitely with its main window mapped, opens its DB, runs its own
startup code (the `Qwilight.TwilightSystem` static constructor throws, caught by the app),
but the window is black: the 2.4 in-process engine binds its swapchain through a private
dcomp partner method Wine stubs and never calls SetContent. Wave 3 agent "presentation" is
on it; evidence in `survey/DESIGN.md` (wave 3 section) and `survey/impl-main-*.log`.

## 2026-09-08 evening — Wave 3: render device initialises, input layer becomes the wall

- Presentation agent (cut off by the API limit, resumed later): `IDCompositionDeviceInternal`
  {6b556968-...} on the dcomp device (dwmcorei's `CDCompDevice::Create` fails outright without
  it: SetCommitCompletionEvent + batch ids; Commit signals the event), partner slots 45/46 as
  GetCurrentBatchId/GetLastConfirmedBatchId, visual slots 40/41 as SetRelativeSizeX/Y (names
  from FrameworkUdk's callers; comments in `dcomp_private_iface.idl`).
- Main: `IDCompositionDesktopDevicePartner3` {28d6ad3d-...} accepted by the device QI; win32u
  `NtCreateCompositionInputSink(params /*0xe8 bytes*/, &handle)` and
  `NtCloseCompositionInputSink` implemented (placeholder handle; the udk fast-fails if the sink
  is refused) with `dlls/wow64win` thunks; wintypes reports `UniversalApiContract` 15
  (Windows 11 22H2) instead of 10, because FrameworkUdk selects its platform function tables
  by that version and the pre-11 tables lack input sites.
- Result: the render thread's dcomp device now initialises and the beta reaches WinUI's
  input layer 0.1 s after window creation, which fast-fails (c0000409) in
  `Microsoft.InputStateManager!InputRootConfigurationProxy::OnHostWindowIdChanged` because
  `Windows.UI.Internal.Input.InputSite` (system-private WinRT class; statics IID
  {73f80911-7a51-5b0f-906b-f6402ea6c3cb}) is not registered. Agent "input-site" owns that;
  agent "presentation" continues on the black window (no SetContent from the 2.4 engine).
  Details: `survey/DESIGN.md` "Wave 3 state".

## 2026-09-08 late evening — beta runs end to end, render loop idles (main)

- input-site agent: new `dlls/windows.ui.internal.input` (`Windows.UI.Internal.Input.InputSite`
  statics `GetForVisual`, `ActivationConfigurationInputObject` with activate/eat policies),
  contract reverse-engineered from FrameworkUdk / Microsoft.InputStateManager / Microsoft.UI.Input;
  the 0xe8-byte sink block is never read back and no further sink syscall is called. The beta
  runs indefinitely again and the whole Microsoft.UI.Content window tree comes up.
- presentation agent: `IDCompositionDeviceInternal`, batch ids, visual/surface private slots,
  offsets in `do_composite` (dcomptest-tree 83/0). Main: process-global batch ids in dcomp
  (Windows batch ids are DWM-global; the UI thread commits on a different device object than
  the render thread queries), dxgi factory `IsCurrent` -> TRUE and
  `RegisterOcclusionStatusEvent` -> S_OK + cookie (the render loop checks both each frame).
- Open: window still black. A relay trace of the wait primitives shows the dwmcorei render
  thread initialising its device, doing three compositor-clock waits, polling its I/O
  completion port and five events, then blocking forever in a six-handle infinite wait: an idle
  dispatcher that never receives a frame request. Handed to the presentation agent with the
  evidence (`survey/impl-main-relay-run.log`, DESIGN.md "Wave 3 follow-up"). Pointer input
  (per-thread EnableMouseInPointer, user32 ordinal 2505 HandleDelegatedInput stub that will kill
  the process on first input) is with agent input-pointer.
- Also seen: the game's `TwilightSystem` static ctor fails because
  `Windows.Web.Http.Filters.HttpBaseProtocolFilter` is not implemented in Wine (caught by the
  app; online features will need it).

## 2026-09-08 night — Windows.Web.Http, Win2D activation, and the real black-window cause

- The presentation agent's second round: dcomp's compositor clock and composite thread ran at
  1 Hz because `GetDeviceCaps(VREFRESH)` returns 1 under Wine (documented as "default rate"),
  now guarded (`dcomp_refresh_period()`). winedbg backtraces showed both the render thread
  (`CScheduler::WaitForWork`) and the XAML UI thread idle, correctly: XAML never scheduled a
  frame because the game's `Qwilight.TwilightSystem` static constructor threw
  (`Windows.Web.Http.Filters.HttpBaseProtocolFilter` not activatable), the XAML StaticResource
  failed and `Window.Content` was never set.
- web-http agent: new `dlls/windows.web.http` (+ `include/windows.web.http.idl`): filter,
  cache control, HttpClient with factory, header collections, HttpMethod, request and content
  types, and eight async operations that complete with WININET_E_CANNOT_CONNECT; IIDs and vtable
  order from the winmd via ilspycmd; `httptest.c` passes. TwilightSystem now constructs and
  XAML parses to the end.
- Win2D's eight `Microsoft.Graphics.Canvas.*` runtime classes are not in the app manifest and
  Wine's combase resolves activatable classes from the registry only, so they were registered
  in the prefix against the app-local `Microsoft.Graphics.Canvas.dll` (works completely). The
  proper fix is an app-local activation fallback in `dlls/combase`.
- Current stop: `ICompositor::CreateSpriteVisual` stub in `dlls/windows.ui.composition`, hit by
  `Window.SystemBackdrop = MicaBackdrop`, surfaces as a managed NotImplementedException and aborts
  the app's launch code before it sets `Window.Content`. Agent composition-3 is implementing the
  visual family over real dcomp visuals.

## 2026-09-08 late night — the black window is cross-device texture sharing (main)

The chain that keeps XAML from ever rendering, diagnosed to the root:
`dxgi_resource_CreateSharedHandle` returns E_NOTIMPL. Stack (captured with a temporary
RtlCaptureStackBackTrace in the stub, since removed): `marshal.dll` <- `dcompi.dll`
(+0x3b8cb etc.) <- `Microsoft.UI.Xaml.dll` <- `CoreMessagingXP.dll`, on the XAML UI thread
~5 ms after that thread created a fresh D3D11 device. The dwmcorei render thread has its OWN
D3D11 device. WinAppSDK's lifted compositor shares XAML's rendered textures between the two
devices through NT shared handles (`IDXGIResource1::CreateSharedHandle` +
`ID3D11Device::OpenSharedResource1`), which is **genuine cross-device resource sharing** — the
exact capability wined3d does not have, and the same limitation that made the shipped 1.x
D3DImage build unworkable (HANDOFF gotcha). Both `CreateSharedHandle` and all three
`OpenSharedResource*` in Wine's d3d11/dxgi are E_NOTIMPL stubs. Wine's d3dkmt shared-object
plumbing (`dlls/win32u/d3dkmt.c` NtGdiDdDDIShareObjects/OpenResourceFromNtHandle) and
winevulkan external memory exist but wined3d does not use them.

Two routes, both large / strategic (needs a decision):
1. Implement same-adapter cross-device sharing in wined3d's GL backend (share GL objects
   across wined3d devices; an "adopt this shared texture" path; cross-device sync). Days of
   wined3d work, upstreamable, keeps everything else as-is.
2. Run the beta on DXVK (Direct3D-over-Vulkan), which implements shared NT handles.
   **Experiment done** (isolated prefix `qwpfx-beta-dxvk`, DXVK 3.1 d3d11/dxgi/d3d10core set
   native): DXVK loads on the RTX 5090 via winevulkan, gets past the wined3d sharing wall
   (CreateSharedHandle no longer even reached the same way), but the run then crashes in
   `dcompi.dll+0x30fad` right after DXVK's `D3D11DXGIDevice::QueryInterface` refuses three
   private interfaces ({a44472e1-...} = an ID2D1 device interface, {f13ebcd1-...},
   {26c5dc23-...}) that WinAppSDK's compositor QIs on the D3D11 device — dcompi dereferences
   the failed QI. So DXVK trades the sharing wall for a private-interface wall; not obviously
   closer. DXVK also loses the wined3d-specific hacks (composition-swapchain window hosting,
   on-screen child GL), which 2.0 mostly does not use, and the dxgi factory occlusion/current
   stubs (DXVK logs "RegisterAdaptersChangedEvent: Stub" x28 — harmless).

Also landed (main): `do_composite_dxgi_surface` no longer applies DCX_CLIPCHILDREN when every
visible child is WS_EX_NOREDIRECTIONBITMAP (the App SDK's DesktopChildSiteBridge covers the
whole client area and would clip the composed content to nothing). Not yet exercised because
nothing draws yet, but required before any frame can show.

## 2026-09-09 — Wave 5: cross-device resource sharing in wined3d (route 1, chosen by the user)

The user picked route 1 (wined3d GL sharing) over DXVK. Measured first (`survey/impl-main-shared-diag.log`
line 1176, a temporary desc dump in the dxgi stub): the App SDK's first shared texture is
`A8_UNORM 32x32, RENDER_TARGET|SHADER_RESOURCE, usage SHARED|SHARED_NT_HANDLE`, **no keyed mutex**,
created on the XAML UI thread and (on Windows) opened by the dwmcorei render thread's own D3D11
device through `OpenSharedResource1`. The two devices come from two DXGI factories, hence two
`struct wined3d` instances: the GL share group must be process-global per adapter LUID.

Design is in `survey/DESIGN.md` "Wave 5": one root GL context per adapter that every device
context shares with; a process-global `wined3d_shared_resource` registry (`dlls/wined3d/shared.c`,
new) whose entries own one GL texture name adopted by one wined3d texture per device; NT/KMT handles
are real d3dkmt objects from win32u (`D3DKMTCreateAllocation2` + `D3DKMTShareObjects`, private
runtime data carries the share id + desc; keyed mutexes are win32u's kmt mutex objects); cross-device
GPU ordering by ARB_sync fences inserted on Flush/Present/keyed-mutex release and `glWaitSync`ed by
consumers on an epoch change. Cross-process sharing is out of scope (refused cleanly).

Landed by main: the frozen public API (`wined3d_resource_create_shared_handle`,
`wined3d_resource_get_shared_handle`, `wined3d_device_open_shared_resource[_by_name]`,
`wined3d_resource_{acquire,release}_keyed_mutex`) as stubs in `dlls/wined3d/shared.c`, declared in
`include/wine/wined3d.h`, exported in `wined3d.spec`; builds. Agents wined3d-share (wined3d core)
and d3d-glue (dxgi/d3d11/d3d10 + `sharedtest.c` acceptance program) are running in parallel; their
reports go to `survey/impl-wined3d-share.md` and `survey/impl-d3d-glue.md`.

Progress (09:00, after a PC freeze/reboot at ~08:25 that killed both agents mid-run; the journal
shows a clean user reboot, no OOM/GPU kernel messages, cause unknown): d3d-glue is done
(`survey/impl-d3d-glue.md`): IDXGIResource1::CreateSharedHandle / GetSharedHandle, IDXGIKeyedMutex,
ID3D11Device::OpenSharedResource / OpenSharedResource1 / OpenSharedResourceByName, d3d10
OpenSharedResource, `d3d11_texture2d_desc_from_wined3d()`, acceptance program `sharedtest.c`
(323 checks). Against wined3d-share's first landing: **317/323**; the six failures are wined3d's:
(1) a Flush ordering race (the fence is inserted on the producer's CS thread after `Flush()` has
returned, so a consumer that reads immediately sees the previous contents; a 100 ms sleep hides it)
and (2) an `A8_UNORM` render target that stores the clear's red channel instead of alpha (pre-existing,
same device, no sharing; A8 is emulated as R8 with a sampling swizzle and the write side is not
swizzled) — the beta's exact first shared texture. wined3d-share was resumed with both.

**Wave 5 landed (09:00):** `sharedtest.exe` 315/315 on llvmpipe; the Flush race is fixed by making
`Flush()` CS-synchronous when shared resources exist (`device.c:4992`), the A8 clear by routing
`colour.a` into the R8 storage (`texture_gl.c:250`). Report `survey/impl-wined3d-share.md`. The
beta's `CreateSharedHandle` now succeeds and XAML goes on to make views on the texture — but the
window stays black and **nobody ever opens the handle** (no `OpenSharedResource*`).

## 2026-09-09 — Tracing the black window instead of guessing (main)

Per the user's instruction: measure, don't assume. Facts from `survey/impl-main-diag{2,3,4}-run.log`
(+ `impl-main-diag2-bt.txt`, a `winedbg bt all` at 35 s):
- **After 0.07 s past `CreateSharedHandle` the process logs nothing at all for the rest of the run**
  (with `+d3d11,+dxgi` on: total silence for 57 s). All 24 threads idle: the XAML UI thread in
  its message loop (`FrameworkApplication::RunDesktopWindowMessageLoop`), the dwmcorei render
  thread waiting on one handle, three wined3d CS threads (three D3D11 devices), the dcomp
  composite thread sleeping, CoreMessaging dispatchers waiting. So it is not presentation: no
  frame is ever produced, the app's UI is dead. No audio sink input exists either (FMOD never
  started).
- `RoReportUnhandledError` was a silent stub; it now prints the error + stack (kept), and the
  class-not-found path in `RoGetActivationFactory` dumps a stack (**TEMP DIAG**, `roapi.c:170`,
  remove). That gave two events on the UI thread, 4 ms apart, both inside the **first XAML tick**
  (`CXcpDispatcher::MessageTimerCallback` -> `OnReentrancyProtectedWindowMessage`):
  1. **Unhandled managed `InvalidCastException`** ("Specified cast is not valid." =
     CsWinRT's exception for a bare `E_NOINTERFACE`), reported through
     `DirectUI::ErrorHelper::ReportUnhandledError`. XAML then stops ticking for good — that is the
     black window. The last Wine refusal before it, on the same thread, 1 ms earlier:
     `dwrite:dwritetextanalyzer_QueryInterface {b7e6163e-7f46-43b4-84b3-e4e6249c365e}` — the
     public `IDWriteTextAnalyzer` IID **+1**, a private DirectWrite interface. The only module that
     carries that GUID is `Microsoft.Internal.FrameworkUdk.dll`, in
     `TextAnalysis_GetContentReadingDirection_Impl` (udk rva 0x29510): it QIs the analyzer for it,
     fails the call on E_NOINTERFACE, and calls vtable slot 3 of the result as
     `HRESULT (IDWriteTextAnalysisSource *source, UINT32 position, UINT32 length,
     DWRITE_READING_DIRECTION *direction, BOOL *is_ambiguous)` (argument mapping from the
     disassembly at 0x180029696-0x1800296de). WinUI uses it for content-based reading direction
     of text elements. Fix: implement that interface on Wine's text analyzer (first strong
     character decides; none -> LTR + ambiguous).
  2. A C++ exception from `wuceffectsi!SystemBackdropInternal::BaseController::InitializeNewCompositor`
     -> `CreateCrossFadeEffectBrush` -> `winrt::CompositionEffectSourceParameter(name)`: the class
     is not in `dlls/windows.ui.composition`. **Caught by wuceffectsi itself** (the unwind target
     is `InitializeNewCompositor+0x31c`), so the Mica crossfade is skipped, not fatal. Needs
     `CompositionEffectSourceParameter` + `Compositor::CreateEffectFactory` + an effect brush that
     renders as its fallback colour eventually (Mica/Acrylic), not on the first-frame path.
- Sanity of the environment: the 1.x build renders in the same Xvfb/llvmpipe setup, and XAML's
  UI thread is alive in its message loop, so the headless setup is not what blocks the frame.

Fixes that followed, each found by re-running and reading the next unhandled error (all in the
first XAML tick, all Wine gaps that surfaced as managed exceptions or XAML fail-fasts):
1. `dlls/dwrite/analyzer.c`: private text analyzer interface {b7e6163e-...365e} with
   `GetContentReadingDirection` (first strong character decides).
2. `dlls/dwrite/layout.c`: `IDWriteTextLayout4::SetFontAxisValues`/`GetFontAxisValueCount`/
   `GetFontAxisValues` now keep the values on the layout's format (whole-text only, per-range is a
   FIXME) instead of E_NOTIMPL.
3. `dlls/ntdll/resource.c`: **MUI satellite resource modules**. WinUI's strings live in
   `<lang>\Microsoft.ui.xaml.dll.mui`; the base DLL only carries a "MUI" resource. Wine had no
   redirection, so `LoadString` failed with ERROR_RESOURCE_TYPE_NOT_FOUND. `LdrFindResource_U` /
   `LdrFindResourceDirectory_U` now retry a failed lookup in the satellite (user UI language, then
   the resource's ultimate fallback language, then en-US; loaded once per module, cached), and
   `LdrAccessResource` resolves entries that live in the satellite. The MUI resource header layout
   was decoded from the shipped DLL (offsets at +0x54..+0x80: main/mui type lists, language,
   fallback language).
4. `dlls/dwrite/analyzer.c`: `IDWriteFontFallbackBuilder::AddMappings` (replays another fallback's
   mappings, system or custom); `dlls/dwrite/font.c`: `IDWriteFontFamily2::GetMatchingFonts` with
   axis values reduced to weight/stretch/style.
5. `dlls/d2d1/geometry.c`: `CombineWithGeometry` for all geometry types as an approximation
   (both inputs flattened and emitted under a fill mode that is exact for nested/disjoint shapes:
   EXCLUDE/XOR even-odd, UNION winding, INTERSECT = second input). XAML uses EXCLUDE for border
   rings (`BaseContentRenderer::MaskCombinedRenderHelper`); before this it fail-fasted.
6. `dlls/dxgi/device.c`: `IDXGIDevice2::EnqueueSetEvent` flushes and signals the event (was
   E_NOTIMPL -> managed NotImplementedException).
After 1-5 the beta survives 20+ s, XAML's render walk runs, the game draws 1282x720 Direct2D
content on the XAML device and starts decoding images. Still black. Remaining, in flight:
`Windows.System.Threading.ThreadPoolTimer` (missing class -> XAML fail-fast in
`ThreadPoolService::InitOnceCallback`, agent threadpool-timer), Mica's
`CompositionEffectSourceParameter`/effect brushes (agent composition-effects), and
`CoUnmarshalInterface` failing to QI `IBuffer` {905a0fe1-...} when a `Windows.Storage.Streams.Buffer`
crosses apartments (main).
Measured after fix 6 (`survey/impl-main-diag11/12-run.log`, `+dxgi,+dcomp`): the unhandled managed
errors are gone; the run now ends only in the ThreadPoolTimer fail-fast (~20 s in, when the first
page's images start decoding). Up to that point there are **zero swapchain Presents, 3 dcomp Commits,
no SetContent, no composite**: no frame has been produced yet, so the black window is still "nothing
drawn", not a presentation defect. dwmcorei's output swapchain is a `CreateSwapChainForComposition`
swapchain hosted by the dxgi hack in a child window next to the XAML island bridge (`factory.c:471`),
sized/shown like the bridge; `IDXGISwapChainPartner::GetCompositionSurface` still returns a dummy
handle. Those are the next suspects once frames exist. `windows.storage` InMemoryRandomAccessStream
made agile (IAgileObject) so CsWinRT no longer needs a COM proxy for it.

With `Windows.System.Threading.ThreadPoolTimer` in place (agent threadpool-timer,
`survey/impl-threadpool-timer.md`) the fail-fast moved: the dwmcorei render thread queries
`D3DKMTQueryAdapterInfo(KMTQAITYPE_ADAPTERTYPE)` 325 times (win32u: "type 15 not handled",
STATUS_NOT_IMPLEMENTED), calls `D3DKMTEscape` once, and dcompi raises exception 0xe0434e49 =
`Microsoft::WRL2::FailFast::OutOfMemory` (dcompi rva 0x4d6d0), which .NET reports as an unhandled
exception and the process dies ~7 s in. `dlls/win32u/d3dkmt.c` now answers ADAPTERTYPE with
RenderSupported|DisplaySupported and traces the escape (type/flags/size) so the escape's purpose
can be read from the next log.

**First frames (09:35, `survey/impl-main-diag15-run-10.png`).** With ADAPTERTYPE answered the beta
renders: 5340 swapchain Presents in 18 s and the 10 s screenshot shows the song-select screen
(title, list frames, background art, "double click to auto play"). It then dies at ~18 s in the same
dcompi `FailFast::OutOfMemory`, reached through `FailFast::ForHR(E_OUTOFMEMORY)`: the dwmcorei
render thread re-creates its D3D11 device **611 times in 18 s** (every frame). Each cycle:
`DXGID3D10CreateDevice` -> `CheckFormatSupport` x9 -> D2D private device creation ->
`SetMaximumTextureMemory` -> `ID3D11FunctionLinkingGraph::SetInputSignature` **E_NOTIMPL**
(Wine's d3dcompiler has no HLSL function-linking implementation: `d3dcompiler_43/linker.c` stubs)
-> `IDXGIDevice3::Trim` -> retry. dwmcorei builds its shaders with the D3D11 linking API
(`D3DLoadModule`, `D3DCreateFunctionLinkingGraph`, `D3DCreateLinker`). Fix (prefix, not code):
the native Microsoft `d3dcompiler_47.dll` shipped inside the prefix's Edge WebView
(`Program Files (x86)/Microsoft/EdgeWebView/Application/152.0.4191.62/`, x64) copied to
`system32` (the builtin copy kept as `d3dcompiler_47.dll.wine`) with
`HKCU\Software\Wine\DllOverrides\d3dcompiler_47 = native`. Implementing the linker in Wine
(vkd3d-shader has no DXBC library linking) is a separate, large project.

With the native compiler the device loop is gone and dwmcorei renders frames, but it still hit
`FailFast::ForHR(E_OUTOFMEMORY)` after ~1 s: every frame it wraps its render target through
`IDXGIResource1::CreateSubresourceSurface()` and hands that surface to
`ID2D1DeviceContext::CreateBitmapFromDxgiSurface(options TARGET|CANNOT_DRAW)`. Wine's d2d1 asked the
surface for `ID3D11Texture2D` directly, which a dxgi subresource surface refuses, computed
"surface options 0" and returned E_INVALIDARG. `dlls/d2d1/bitmap.c`: new `d2d_surface_get_resource()`
falls back to `IDXGISurface2::GetResource()` (parent texture + subresource index; index 0 only, FIXME
otherwise), used by the options check and by bitmap creation from a surface.

**Result (09:45, `survey/impl-main-diag20-run-*.png`):** the beta runs 90 s without a crash,
~250 presents/s, song-select screen visible and animating. 1.x dev build regression check
(`tools/xvfbrun.py 951`, `QW_AUTO=30`): menu, song, 1448 presents, 0 d2d errors. The TEMP DIAG
stack dump in `RoGetActivationFactory` is removed again (it flooded 1.x logs); the
`RoReportUnhandledError` reporting stays.

## 2026-09-09 — First Steam launches of the beta on the real display (main)

Launch options `QWILIGHT_DEV_BUILD=0 QWILIGHT_WINEDEBUG=... %command%`. Two findings:
1. **Instant exit = the game's Steam check.** `Qwilight.ValveSystem` calls `SteamClient.Init()`
   whenever `SteamAppId` is "1910130" and `Environment.Exit()`s on failure; plain Wine has no Steam
   client bridge (Proton's lsteamclient), so `SteamAPI_Init` fails ("did not locate a running
   instance of Steam"). The launcher (`~/.steam/root/compatibilitytools.d/qwilight-wine/run`) now
   unsets `SteamAppId`/`SteamGameId`/`SteamOverlayGameId` for the Steam copy
   (`QWILIGHT_KEEP_STEAMAPPID=1` restores). Steam features (cloud, friends, beta-branch name) are off
   until a client bridge exists.
2. **On a real X display the compositor takes its hardware presentation route** — with NVIDIA
   (`survey/steam-beta-run1.log`) and with Mesa llvmpipe (`steam-beta-run2.log`) alike, so the
   decision is display-environment-based, not GPU-based; on headless Xvfb it falls back to plain
   swapchain presents (which our dxgi child-window hosting shows). Hardware route:
   `IDXGISwapChainPartner::GetCompositionSurface` (dummy handle) -> `IDCompositionSurfaceFactory::CreateSurface`
   -> `SetContent` -> per frame `CreateSurfaceFromHandle(dummy)` E_NOTIMPL + private
   `surface_Unknown4`; result: NVIDIA = dcompi `FailFast::ForHR(E_OUTOFMEMORY)` after ~165 frames;
   Mesa = the window flashes between our dcomp composite and the hosted swapchain window, then an
   access violation on the render thread. Agent presentation-2 owns the real composition-surface
   implementation (`survey/impl-presentation-2.md`). `steam-run.log` (5.7 GB) rotated to
   `steam-run.log.old-20260909`.

**presentation-2 landed** (`survey/impl-presentation-2.md`): real composition surface handles,
`CreateSurfaceFromHandle`, visual clips/transforms, a rewritten `do_composite`, contract-15 private
surface slots (Resize at slot 11 — the NVIDIA fail-fast was a window resize hitting the wrong slot).
There is no display-based decision: dwmcorei always takes the surface-handle route; on Xvfb its
failure was merely harmless. With it, the real-display launch stays up and shows the logo, mode
labels and hints, but still flashed. Traced on the user's display (`survey/impl-main-display1-run.log`):
the new "withdraw the dxgi hosting window while dcomp composites the swapchain" step ran
`SetWindowPos(SWP_HIDEWINDOW)` synchronously from the presenting thread on a window owned by the UI
thread, while the UI thread waited for a lock the presenter held -> `RtlpWaitForCriticalSection
... blocked by 05ec` 60 s stall on the UI thread, and meanwhile both outputs kept drawing (the
flashing). Fix: `SWP_ASYNCWINDOWPOS` on the withdraw and on the pre-existing hosting-window sync
(`dlls/dxgi/factory.c:542,564`).
Still flashing after that. Measured instead of guessed (per-layer dumps of every composite blit,
`WINE_DCOMP_DUMP`, temporary): each composite pass draws exactly two layers into the bridge window —
the XAML atlas surface 1374x965 at 1:1 (real content, 80% opaque) and the swapchain plane scaled by the
DPI matrix {1.0734, 0, 0, 1.0734, 0, 96} (fully transparent headless; the game canvas on the user's
display). Neither blit erases anything; only 2 WM_PAINT/WM_ERASEBKGND in 25 s; 2 X exposes. The
remaining painter is the swapchain's own present into the (hidden) dxgi hosting window through the
on-screen-child-GL / offscreen-blit paths, which races the composite. Removed the painter instead of
the race: new `WINED3D_PRESENT_NO_DRAW` (`include/wine/wined3d.h`, `wined3d/swapchain.c`, `cs.c`:
the frame is loaded/flushed but not drawn into the window; latency semaphore still released) and dxgi
passes it for composition-bound swapchains (`dxgi/swapchain.c:586`).


## 2026-09-09 — The flashing was half-drawn frames: compositor read the game's back buffer mid-draw (main)

**Method (user: measure per frame, don't conclude).** `WINE_DCOMP_DUMP=<dir>` now takes
`WINE_DCOMP_DUMP_SKIP=<n>` so the 400 per-layer BMPs cover the song-select phase instead of the
splash. Headless run `survey/impl-main-diag35-run.log`, 200 composite passes, two layers each:

| layer | what it is | per-pass result (200 passes) |
|---|---|---|
| XAML atlas (`IDCompositionSurface`, R8G8B8A8, 1:1) | 98.4 % transparent, premultiplied (no channel > alpha, transparent pixels are 0,0,0,0) | identical every pass (white-pixel metric 26.8412 % x40) |
| game swapchain plane (`IDXGISwapChain1`, B8G8R8A8, opaque) | the whole scene: skyline, list boxes, "DOUBLE CLICK TO AUTO PLAY" | **32 of 200 passes half-drawn**: yellow text absent (0 px vs 3693), white boxes half (2843 vs 5615) or absent (0) |

`survey/beta-halfdrawn-plane-1507.png` vs `survey/beta-fulldrawn-plane-1509.png`. So the
per-frame inconsistency is not the blend, not the atlas, not ordering of layers: `do_composite`
took `IDXGISwapChain::GetBuffer(0)` of the game's swapchain, i.e. the buffer the game is
rendering the *next* frame into (with `WINED3D_PRESENT_NO_DRAW` nothing ever rotates or copies
it), and copied it at a random point of the game's clear→draw sequence. That is exactly the
"first UI element visible, then the second, never both" the user described.

**Second finding in the same dumps:** the plane's destination rect was `96,0 – 4832,2465` for a
2560x1332 window, i.e. drawn 1.85x too large. The game calls `IDXGISwapChain2::SetMatrixTransform`
(1/CompositionScale, as SwapChainPanel apps must) after every ResizeBuffers, and dxgi's
implementation was a semi-stub that stored the matrix and "did not apply it to presentation".
That is the "UI elements at wrong positions" report: the XAML overlay was 1:1, the scene 1.85x.

**Third finding:** the reproducible render-thread crash (`wined3d.dll+0x2097ee`,
`wined3d_shader_decref` on pointer -1) was caught with a TEMP DIAG stack in
`wined3d_shader_decref` (still in `dlls/wined3d/shader.c`: prints `Garbage shader` + module
frames and returns instead of faulting): it fires from d2d1's `flush_queued_draw` on XAML's
render thread, `wined3d_device_context_set_shader` releasing the *previous* shader of the
immediate context's state. The compositor thread was swapping `ID3DDeviceContextState`s on that
same immediate context every pass (`create_bgra_surface_from_rgba`'s R8G8B8A8→B8G8R8A8 draw,
plus d2d1's own state swaps inside `CopyFromBitmap`/`GetDC`), racing d2d1's install/restore
logic on the render thread. Not proven to the last instruction, but after the change below it
did not fire once in 2x60 s (before: 12 s and 27 s into every run).

**Fixes.**
- `dlls/dxgi/swapchain.c`: composition-bound swapchains keep a *presented surface*: at
  `Present()` (before the NO_DRAW wined3d present, on the app's thread so it is ordered after its
  draws) back buffer 0 is `CopyResource`d into a private D3D11 texture that is published as
  private-data interface `GUID_wine_dxgi_presented_surface` on the swapchain; released on
  ResizeBuffers/last Release. `SetMatrixTransform` is now a TRACE (stored as before).
  `dxgi_private.h` includes `d3d11.h`.
- `dlls/dcomp/device.c`: `dcomp_swapchain_get_surface()` composes the presented surface when the
  swapchain has one (else `GetBuffer(0)` as before) and returns the swapchain's matrix transform,
  which `do_composite` pre-multiplies onto the visual matrix for that content.
  `do_composite_dxgi_surface()` rewritten: `CopySubresourceRegion` into a per-visual **staging**
  texture, `Map`, CPU copy (R/B swizzle for RGBA formats) into a per-visual DIB, `GdiAlphaBlend`
  as before. No D2D, no `ID3DDeviceContextState`, no `ID3D11Multithread`: only stateless copies
  touch the app's immediate context from the compositor thread. `create_bgra_surface_from_rgba`
  deleted. Visual gains `staging`/`layer_dc` fields (`dcomp_private.h`), freed by
  `dcomp_visual_release_layer()` from `visual_Release`.

**Result (headless, `survey/impl-main-diag36-run.log`, dump36):** plane 0/200 half-drawn passes
(yellow 3693/3693/3693, white 7672/7673/7672), atlas identical every pass, plane rect
`96,0 – 2656,1332` (1:1; the 96 px offset is the game's own centring of its 2368-wide 16:9
content, the right 96 px of the plane are black), no crash and no `Garbage shader` in 60 s.
Composed screen: `survey/beta-songselect-composed-2026-09-09.png` (mode buttons inside their
box, FILE/RANK list inside the list frame). 1.x regression `tools/xvfbrun.py 3`: 540 presents,
0 errors, settings dialog renders (`shot-run3.png`).

**Not yet checked:** the user's "slight outline on white elements". The atlas is correctly
premultiplied and the blend is `AC_SRC_ALPHA`, so the D2D grayscale text antialiasing into a
transparent atlas (Wine d2d1) is the next suspect, not the compositor. Needs a zoomed grab on the
real display after the relaunch.

**TEMP DIAG still in the tree (remove before commit):** `dcomp_debug_dump_layer` (+ `WINE_DCOMP_DUMP_SKIP`)
in `dlls/dcomp/device.c`; the `Garbage shader` guard in `dlls/wined3d/shader.c`.

## 2026-09-09 — "UI very incomplete": XAML text tiles were wiped by Direct2D Clear (main)

**What XAML tried to draw vs. what showed.** `+dwrite` trace of a headless run
(`survey/impl-main-diag37-run.log`, 3.4 GB): the song-select page creates text layouts for
`F1: Help`, `F5, F6: Refresh`, `F7: Downloader`, `F8: Multiplay`, `F9: Collection`,
`F10: Challenges`, `F11: Notifications`, `SPACE: Game settings`, `◀, ▶: Select difficulty level`,
`Online`/`Offline`, `Hall of Fame`, `Classification settings`, `Difficulty Table Settings`,
`Please enter a word to search for`, `JUDGE: EZ, HP: 0％`, `130 BPM×7.7＝1000 BPM (×1.00)`,
`0 / s (PEAK: 0 / s)`, `0 (SC: 0, LN: 0, MN: 0)`, `0：00`, `🔙All` and three Segoe glyphs — and
every one of them was laid out *and* drawn (`dwritetextlayout_Draw`, 3–4 times each). Only
`F11: Notifications` and the per-frame `🔙All` were on screen. The mode-button labels and `↑BPM`
never go through DirectWrite at all (skin images).

**Where the tiles went.** XAML's compositor (dwmcorei) rasterises each text into a shared
A8 atlas (`wined3d_shared_resource` id 3, 1728x992, `+d3d_shared`; `dlls/wined3d/shared.c` now has
its own `d3d_shared` debug channel). Dumping that atlas from the render device (temporary
`WINE_SHARED_DUMP` helper, since removed) showed 1334 non-zero pixels — one tile. The `+d2d`
trace shows how a tile is drawn: `ID2D1DeviceContextUnknown::method10(atlas bitmap)` → new bitmap
view, **`method7(view, tile RECT)`**, `SetTarget(view)`, `Clear()`, `SetTransform(tile)`,
`DrawGlyphRun()`, `method7(view, NULL)`. Wine's `d2d_device_context_unknown_method7` was a
`stub!` and `Clear()` clears the whole target, so every tile wiped every earlier tile and the last
one drawn (F11) survived. Element surfaces use the same call with `(2,0)-(1282,720)` on 1312x736
bitmaps (the 2 px gutters).

**Fix (`dlls/d2d1`):** `struct d2d_bitmap` gains `has_bounds`/`bounds`; method7 stores them
(TRACE, no longer a stub); `d2d_bitmap_bounds_clip()` (bitmap.c) intersects the scissor rect in
both draw paths (`flush_queued_draw` and `d2d_device_context_draw`, which `Clear()` uses) with the
target bitmap's bounds. Result: atlas 15072 non-zero pixels and stable, the whole key-hint bar
renders (`survey/beta-songselect-hints-2026-09-09.png`, atlas crop `survey/beta-atlas-after.png`);
1.x regression `tools/xvfbrun.py 4`: 632 presents, 0 errors.

**Dark scene in some runs is the game, not Wine.** Runs `diag40/41/43` showed the XAML overlay
but no skin scene (skyline/list boxes); `diag42/44` had it. In the dark runs the game issued zero
`DrawGlyphRun`s and ~800 presents/s of cleared frames: `DrawingSystem` in NoteFile mode draws only
while `BaseUI.LoadedSharing` is free and `FaultText` is empty, and `FaultText` is "Loading…" for the
whole skin load. The beta's `yucky/Configure.json` has `DefaultUIDate = 0` and
`AutoGetDefaultUI = true`, so `TwilightSystem.GetDefaultUIDate` fetches `@Default.zip` (54 MB) from
the server **on every launch** (the date is only saved on a clean exit; headless runs are killed)
and reloads the base skin — `yucky/UI/@Default/@Default.zip` mtime moved during runs 43 and 44.
While that reload runs the scene is black; on llvmpipe it can take the rest of a 50 s run. Not a
Wine bug; a clean exit (or a manual `DefaultUIDate`) stops the re-download.

**Also learned:** the beta copy has no chart folders (`DefaultEntryItems` = the Favorites entry
only, `DB.db` 92 KB) so the empty song list is correct; `◀ ▶` (U+25C0/25B6) render as tofu in the
hint bar (dwrite fallback gap, small); the stats/`Online`/`Hall of Fame`/search placeholder texts
are drawn into the atlas but not shown — most likely collapsed by the app (no chart selected, not
logged in), to be checked against the XBF in `Qwilight.pri` (`scratchpad/pri-strings.txt`).
The `wintypes`/`storage` QueryInterface fixme storms are .NET reference-tracker probes
(`IReferenceTracker` {11d3b13a-…}, `IReferenceTrackerTarget` {64bd43f8-…}); harmless.
`WINE_SHARED_DUMP` (glGetTexImage from the bind path) itself broke the scene when enabled — it was
removed, don't resurrect it as-is.

## 2026-09-09 — Geometry Outline() implemented; what the user's interactive run hits that headless never does (main)

The user's Steam session log (83 min on the real display) has 12274 `d2d_path_geometry_Outline`
stubs, 12344 `d2d_factory_unknown_method2` (private combine: mode INTERSECT of a
`ID2D1RectangleGeometry` with a `ID2D1PathGeometry` under a 1.073437 scale, flags = float bits of
0.25 = tolerance) and 8824 `d2d_device_unknown_method3` stubs; no headless run has any of them, and
a run with synthetic pointer hover (`scratchpad/betahl10-hover.sh`, XTest via
`scratchpad/xhover.py`) has none either. 2.4/s over the session looks like the text caret blink
(rect ∩ clip, then Outline), not general chrome. Still, `Outline()` returning E_NOTIMPL drops the
shape, so `dlls/d2d1/geometry.c` now implements it for path, ellipse, rectangle, rounded-rectangle
and transformed geometries as `Simplify(LINES)`: streaming the flattened figures with the
original fill mode renders identically for fill consumers (the real Outline only differs when the
result is stroked). Geometry groups already delegated to the path. 1.x regression
`tools/xvfbrun.py 5`: 0 errors.

Remaining d2d private stubs seen per frame on the render thread: `unknown_method8(rect, aa_mode)`
(the SwapChainPanel / element rects, probably a dirty or clip rect), `unknown_method13(p2)`
(flushes the queued draw; semi), `unknown_method5(target)` (SetTarget alias),
`device_unknown_method3(0x7d0, 0xc80000, p3, p4)` (2000 / 13 MB: a cache or memory limit hint),
`factory_unknown_method3` (gradient stop texture; implemented). None is known to drop content.

## 2026-09-09 — "cursor invisible, nothing interactable": WinUI input island never receives pointer input (main)

The user's screenshot of the real display is **pixel-identical to headless** (skin scene, mode
row, Back All, sort list, hint bar, search underline, "double click to auto play"), so no draw is
actually missing any more — the remaining problem is purely input: no cursor over the window and
nothing responds to clicks.

Traced with synthetic input (`scratchpad/betahl10-hover.sh` + `scratchpad/xhover.py`: XTest motion
+ clicks over the game window on :6, `+win +msg +cursor`), run `survey/impl-main-diag46-run.log`:

* The pointer-input plumbing from wave 4 is present and works: `NtUserEnableMouseInPointerForThread`
  sets the per-thread flag (`dlls/win32u/input.c`), `NtUserEnableMouseInPointer enable 1` sets the
  global flag, `process_mouse_message()` (`dlls/win32u/message.c:2596`) converts mouse→`WM_POINTER`
  when `is_mouse_in_pointer_enabled(hwnd)`. The standalone `pointertest.exe` passes 39/39.
* **Yet zero `WM_POINTER` messages are generated in the beta** (no msg 0x245–0x24a anywhere). The
  hardware mouse messages that reach the WinUI top-level (`WinUIDesktopWin32WindowClass`, here
  0x2006e) arrive as plain `WM_MOUSEMOVE`/`WM_LBUTTONDOWN` with **wParam 0 / lParam 0 (no
  coordinates)** — i.e. these are WinUI's own forwarded/hit-test pokes, not the raw positioned
  hardware stream. `window_from_point()` for the real motion resolves to the sized content child
  (0x10080, 2560x1332), but the `InputSiteWindowClass` window (0x20074) is created **0x0 and hidden
  (`SWP_HIDEWINDOW`) and is never sized** — exactly the block `impl-input-pointer.md` §5 and
  `impl-input-site.md` §6.2 predicted. So pointer input is never hit-tested into XAML and nothing
  reacts; `WM_SETCURSOR` is never dispatched to the content window, so Wine never sets the X cursor
  and the pointer is invisible over the window.

**Root cause (not fixed): the WinUI content island's input site is never given content/size.** On
Windows the `DesktopChildSiteBridge`'s `InputSiteWindowClass` child is sized and shown when the
content island receives its content and lays out. The 2.0 beta renders through the dcomp
shared-visual composition path (which we made work), but that path does **not** drive the
`Microsoft.UI.Content` island's input-site sizing, which is a separate mechanism
(`Microsoft.UI.Content.dll` / `InputStateManager`). Until the input site is sized and shown,
`window_from_point`/hit-testing cannot route pointer input to XAML, so the app is display-only.

This is the next real feature for the beta and it is substantial (reverse-engineering the content
island → input-site sizing hand-off, wave-3/4 territory), not a small stub. Two rendering fixes
this turn are unrelated and stand on their own: the glyph-atlas `method7` bounds and
`ID2D1Geometry::Outline`.

## 2026-09-09 — Pointer input reaches XAML: composition input sink routing, delegated input, TF_Notify (main, session 7)

**Result.** In the headless hover run (`survey/impl-main-route11-run.log`, screenshot
`impl-main-route11-run-50.png`) the 2.0 beta takes mouse input: a click on the mode row toggled the
button (it now reads `VHARD LIFE` and shows its "Hard life gauge" tooltip), a flyout opened, a popup
with a scrollbar appeared, the cursor is set per pointer message. **Not yet confirmed on the real
display** — the user should try it next.

**How input is wired in WinUI 3 (WinAppSDK 2.4) with the switcher off**, established from
`Microsoft.UI.Input.dll` / `Microsoft.InputStateManager.dll` disassembly and `+relay` runs, because
none of it is documented anywhere public:

1. Each island gets an `InputSiteWindowClass` child of the `DesktopChildSiteBridge`. It stays
   **0x0 and hidden by design** — `InputSiteHwndWinRT::SetPhysicalSize_NoLock` only records
   `UIA_HWNDWidth`/`UIA_HWNDHeight` window props for UI Automation. It is not the pointer target.
2. Pointer input is delivered through *composition input sinks*. The island's `PointerInputObserver`
   (XAML's `InputPointerSource`) only builds pointer events on the UI thread when
   `IsPointerInputOnUIThread()` was true at construction, which needs `TryGetSwitcherVisual()` — a
   `{ac235818-…}` `ISwitcherProxyInterfaceAccess` QI that only dcompi's *switcher* classes answer.
   With the switcher off (this app, see `impl-switcher-off.md`) that path is dead by design and the
   `WM_POINTER*` the child window receives are dropped in `PopulatePointerEventArgs` (`+0x1a`).
3. The real consumer is `InputSinkInfrastructure::WindowProcedureCallback`, the wndproc of
   InputStateManager's message-only window (`MITMessageOnlyWindowClass`, thread 0174) — the one
   window the process creates a sink for (`NtCreateCompositionInputSink`, params `{size 0xe8; four
   records {kind 2, HWND}}`). It reads the pointer with `GetPointerInputTransform`,
   `GetPointerType`, `GetPointerInfo`, `GetPointerFrameInfoHistory`, `GetPointerDeviceRects`, keys its
   per-window state on `POINTER_INFO.hwndTarget`, hit-tests the registered input sites with their
   `SetConversionTransform` matrices and delivers frames over CoreMessaging to the island's
   `InputThreadController` → `PointerInputObserverWinRT::DeliverInputMessage` → XAML.
4. `DelegateInput(threadId, callback, context, hwnd, 0x1000, 0)` (user32 ordinal 2503) registers
   the **DelegatedMasterInputThread** — its own thread id, a thunk that calls
   `DelegatedMasterInputThread::DelegateInputCallback(MSG*)` as `callback(MSG *, context)`, and the
   message-only window. The callback gets the first look at each delegated message and returns an
   `HDIOPTION` (1 = queued/pass, 3 = pending; `HandleDelegatedInput` releases deferred ones). It only
   processes **`PT_TOUCHPAD`** pointers (`cmp [type], 5`); mouse returns at once. So delegation
   matters for touchpads, not for the mouse.
5. `PointerInputObserverWinRT::NotifyTextFrameworkOfPointerEvent_Callback` resolves
   `msctf!TF_Notify` with `GetProcAddress` on every press/release and fail-fasts (`c0000409`) when
   it is missing.

**Wine changes (all uncommitted, in the tree):**

| file | change |
|---|---|
| `dlls/win32u/input.c` | `NtCreateCompositionInputSink` parses the block and registers `{sink, hwnd}`; `get_composition_input_sink_window(host)` = the registered sink child of a host window, with an `InputSiteWindowClass` class-name fallback because the island's own sink is an implicit one made by WinAppSDK's `marshal.dll` (never reaches win32u); `get_composition_input_sink_target(host)` = the process' registered sink window that is not the host's child (InputStateManager's). Pointer records (`struct pointer`) are now **process-wide** under `pointer_lock` (they were per thread; the ISM reads them from its input thread). `NtUserGetPointerInfoList` with a NULL buffer is a count query, not a crash. Delegated input: `delegate_input()`, `forward_delegated_input()` (posts `WM_WINE_DELEGATED_INPUT` to the delegate thread), `process_delegated_input()` (runs the callback through `KeUserModeCallback(NtUserCallDelegatedInput)` and delivers the message), `handle_delegated_input()` (FIXME). |
| `dlls/win32u/message.c` | `process_mouse_message`: a host with a sink child routes its mouse messages to that child (`hittest = HTCLIENT`) and forces the mouse→`WM_POINTER` conversion for it; the pointer message is posted to the sink target window (the ISM's) and given to the delegate thread first when one is registered. `handle_internal_message` handles `WM_WINE_DELEGATED_INPUT`. |
| `dlls/win32u/window.c`, `include/ntuser.h` | `NtUserCallHwndParam_{DelegateInput,UndelegateInput,HandleDelegatedInput}` + param structs; `WM_WINE_DELEGATED_INPUT`; `NtUserCallDelegatedInput` user32 callback + `struct delegated_input_params`. |
| `dlls/user32/input.c`, `user_private.h` | `DelegateInput`/`UndelegateInput`/`HandleDelegatedInput` real signatures; `User32CallDelegatedInput` callback. |
| `dlls/wow64win/user.c` | `wow64_NtUserCallDelegatedInput` thunk. |
| `dlls/msctf/msctf.c`, `.spec` | `TF_Notify(UINT, WPARAM, LPARAM)` stub returning TRUE. |

**What did not work, so nobody repeats it:** the udk "containment" override
(`Microsoft.Internal.FrameworkUdk.CBS.dll` exporting `GetWinAppSdkChangeEnabled`, reporting change
`0x3c00bf2` disabled) flips Microsoft.UI.Input onto its legacy path — identical outcome, the observer
still needs the switcher visual. The stashed session-5 `FindWindowEx` redirect was right in spirit
(route to the child) but delivered to the wrong consumer.

**Tracing recipe.** `HKCU\Software\Wine\Debug\RelayInclude = user32.*` plus `WINEDEBUG=+relay` gives a
readable per-thread log of what WinUI asks user32 around each message (`survey/impl-main-route5-run.log`
is one: the InputSite wndproc → `GetPointerInfo` → nothing). The key was removed again afterwards.

**Steam launch crashed on first mouse move (16:04, same day).** `c0000409` in
`InProcInputHandler::ProcessPointerFrameContact+0xb6` (ISM, line 0x191): `GetPointerInfo()` returns the
pointer's *latest* state, and the `WM_POINTERUPDATE`s posted to the ISM window were not coalesced, so at a
real mouse's rate two queued updates read the same `frameId`; the ISM took the second as a contact of an
in-progress frame with an empty gathered-pointer vector. (The sibling check at line 0x4eb — `GetPointerInfo`
vs `GetPointerFrameInfoHistory` frame ids — would trip the same way.) Fix: `post_sink_pointer_message()` /
`process_sink_pointer_message()` (`WM_WINE_SINK_POINTER`): every pointer message posted to the sink
window carries a snapshot of the pointer record, installed as `user_thread_info.pointer_snapshot` while
the window procedure runs (`find_pointer()` returns it), and an update overtaken by a newer update is
dropped — the Windows coalescing model. Headless 1 ms-motion stress (`impl-main-route13-run.log`) runs clean.

**Steam launch at 16:12 died differently — not input.** Xlib's *default* error handler printed
`X Error of failed request: BadWindow (X_UnmapWindow), resource 0x600001, serial 310` right after
`dxgi_create_composition_window` hosted the Win2D swapchain window, before any input. Wine installs its
own X error handler on every display it opens, so this is a non-Wine X connection in the process —
NVIDIA's `egl-x11` platform layer, the same component as the session-6 `eplX11SwapBuffers` assertion —
unmapping a window Wine had already destroyed. Intermittent (never in an earlier session of the log).
If it recurs, try `QWILIGHT_ONSCREEN_CHILD_GL=0` in the launch options to take the blit path instead
of the on-screen child GL window; not investigated further here.

**Open in this area.** Keyboard input into XAML not verified. `HDIOPTION` semantics beyond 1/3 unknown
(`deliver_delegated_input` delivers everything). Touchpad/pen untested. The class-name fallback should
become real once the lifted implicit sinks (`marshal.dll` `LegacyMarshalerCreateImplicitCompositionInputSink`)
are understood. `WM_POINTERCAPTURECHANGED`, non-client pointer messages still ungenerated (see
`impl-input-pointer.md` §5).

## 2026-09-09 — Frame pacing: the compositor's 16 ms floor; my tooling killed the user's game (main, session 7b, latest)

**"Frame timing is inconsistent, most visible in gameplay, the cursor too."** The composition
thread paced itself with `Sleep()` to a `min_period = max(refresh_period, 16)` — the 60 Hz cap
from the CPU read-back days — and only then composed and presented; on the display the target
swapchain's `Present()` blocks on vsync (the swap interval cannot be changed for the backup-DC
context, error 0x591), so on a high-refresh panel each frame took 2 or 3 vblanks, alternating.
Now `min_period` is 1 ms on the GPU path: every game present is composed and the display paces
it. The stats FIXME gained interval numbers (`interval avg/min/max ms, >1.5x refresh, >2.5x`), the
process-wide counters are only touched by the thread that drew a window target (two dcomp devices
run two threads; their counts used to add up), and dxgi prints `game present stats` (interval of
the game's own presents) every 2 s. Untested on the display.

**Flyout ("Difficulty table settings") freeze.** The 21:46 run shows the flyout asking the system
compositor for `ICompositorWithBlurredWallpaperBackdropBrush` {0d8fb190-f122-5b8d-9fdd-543b0d8eb7f3}
(SDK reflection; one method, `TryCreateBlurredWallpaperBackdropBrush`) right after `ICompositor3`.
Implemented on the interop compositor, returning the same no-draw `CompositionBackdropBrush`.
What happened after the refusal is unknown: the run was ended by my tooling (next paragraph).

**My headless tooling killed the user's game.** `tools/xvfbrun.py` ended with `wineserver -k`,
and the scenario scripts found "their" game with `pgrep -x Qwilight.exe | head -1`; the launcher's
new leftover cleanup also ran `wineserver -k`. With the user's Steam instance in the same prefix,
the 21:46 run died at 21:47:55, the second `xvfbrun.py` finished, with exit status 0 — reported as
"crashed when scrolling". Fixed: `xvfbrun.py` and the launcher never end the session while any
`Qwilight.exe` is running; the scripts scope the PID to their own launcher's session (`LPID=$!`,
session id match). House rule added: no headless run while the user is testing on the display.

## 2026-09-09 — "Nothing happens when a folder finishes processing": the popup path needed ICompositor3 (main, session 7b, latest)

**Symptom.** After adding a folder the game sat there; restarting showed all the songs, so the
scan had completed and only the UI reaction failed. The user's 21:17 run (21 GB, `+seh,+win,
+x11drv,+opengl`) has no crash and no first-chance .NET exception; instead three
`RoReportUnhandledError 0x80004005 "Call failed."` on the XAML thread (stack:
`ErrorHelper::ReportUnhandledError` ← `CXcpDispatcher::OnReentrancyProtectedWindowMessage` ←
`MessageTimerCallback`). The 40 ms before the first one are the acrylic popup build-up seen in the
profile fail-fast (shared visual handles, `DesktopAcrylicController`, `UISettings`,
`visual2_put_RelativeSizeAdjustment(1,1)`, `sprite_visual_put_Brush`) followed by
`warn:composition:compositor_QueryInterface {c9dd8ef0-6eb1-4e3c-a658-675d9c64d4ab} not
implemented` — that IID is `Windows.UI.Composition.ICompositor3` (SDK reflection), whose one
method is `CreateHostBackdropBrush()`; wuceffectsi's SystemBackdropInternal asks the *system*
compositor for it while building the popup's acrylic. E_NOINTERFACE became E_FAIL in the
popup code, XAML reported it as an unhandled error, and the toast/flyout that follows the folder
scan (and the profile flyout, and whatever else uses an unconstrained popup) never opened.

**Fix.** `include/windows.ui.composition.idl`: `ICompositionBackdropBrush`
{c5acae58-3898-499e-8d7f-224e91286a5d} (empty), `ICompositor3`, runtimeclass
`CompositionBackdropBrush`. `dlls/windows.ui.composition/compositor.c`: `ICompositor3` on the
interop compositor; `brush.c`: a `CompositionBackdropBrush` object (ICompositionBackdropBrush +
ICompositionBrush, nothing draws it: the popup background stays whatever is behind it). Verified
only that the settings dialog and the 1.x regression still pass; the popup itself has no headless
trigger, so the user re-tests add-folder and the profile.

**Also in that run:** `err:file:resolve_reparse_point failed to read: No data available` twice on
scan threads (a Linux symlink inside the added folder; the scan continued), and
`swapchain_gl_set_swap_interval Failed to set swap interval 0 ... last error 0x591` on the
display — the swap interval is never applied for the context on the backup DC, a lead for the
"frame timing is inconsistent" report.

## 2026-09-09 — "Only the game scene shows, the XAML UI is gone": the GPU compositor bound itself to the wrong D3D device (main, session 7b, latest)

**Symptom.** From about 20:10 every untraced headless run (and the user's full-screen runs) showed
only the game's D3D scene: no song list, no buttons, no hint bar. Runs with `+dcomp`/`+composition`
tracing were fine, as were runs on the CPU compositor (`WINE_DCOMP_CPU=1`), which made it look
like a Wine regression for an hour. It was a start-up race that the user's 20:08 exit (empty song
DB → faster game-side start-up) began to lose consistently.

**Cause.** `dlls/dcomp/gpu.c` created the GPU compositor on the device of the *first* layer it
composed. XAML's drawing surfaces come from `IDXGIDevice::CreateSurface()` on dwmcorei's device and
are not shareable, while the game's presented snapshot is shared (NT handle). When the game's
swapchain was composed before XAML's first surface, the compositor lived on the game's device and
every XAML layer was skipped (`dcomp_gpu_open_shared ... not shared; layer skipped`, a once-only
FIXME that had been there all along). The other order worked. Tracing slowed XAML's side just
enough to win the race.

**Fix.** `do_composite_dxgi_surface()` now creates the compositor on the rendering D3D device of
a dcomp *surface factory* — searched across all live composition devices (new global list
`all_devices`, `composition_device.global_entry`), because XAML keeps its surfaces on dwmcorei's
dcomp device and its window targets on another — and pins it (`dcomp_gpu_pin()`). Until a surface
exists the layer is deferred (the very first frames). A fallback rebind (`rebind_device`, at most
once, never when pinned) moves the compositor to the device of an unshareable layer if the
preferred choice was wrong. The game's snapshot opens on XAML's device as before. Verified: two
windowed runs and one full-screen-start run all show the complete UI, at 2560x1440 too, so the
"XAML UI mostly missing in full screen" item is the same bug.

**Also done on the way (kept):** `dcomp/surface.c` `Resize()` preserves the overlapping pixels
and drops the committed copy (which had the old size, so `CopyResource()` into it silently
failed); `dxgi/factory.c` posts the hosting window's hide unconditionally on the withdraw
transition and forces the show on restore (a queued async SetWindowPos made `IsWindowVisible()`
lie). TEMP DIAG left in: `TEMP DIAG: compositor of target ... created`, `TEMP DIAG: unshareable
texture` (3 prints) in dcomp, `TEMP DIAG: withdrawing/restoring/hosting window` in dxgi/factory.c.

**Lesson recorded in HANDOFF:** the `check_geometry_type` count is XAML's drawing activity, not
visibility; judge headless runs by the screenshot, and a run whose behaviour changes with tracing
is a race, not a code path.

## 2026-09-09 — "Going into full screen crashes"; Steam keeps saying "running" (main, session 7b, latest)

**What the logs say.** Two Steam runs, two different endings, no Wine-visible exception in either:
* 20:06 (toggled Full screen in Visual Settings): `ResizeBuffers` ×4 (the presenter switch), then
  7 s of the XAML render thread presenting in a tight loop (~1500 `Present1`/s), then the
  top-level window 0x2006E is destroyed (`IsWindowInDestroy` flood), followed by an orderly XAML
  shutdown (`UnregisterUnhandledErrorHandler`, `dxgi_device_Trim`, `ShutdownQueueAsync`). The
  game closed its own window. Also once: `wined3d_swapchain_resize_buffers Something's still
  holding back buffer 0` — harmless ERR (wined3d continues), see the cache fix below.
* 20:11 (game now *starts* in full screen, `Configure.json` `WindowedMode:false`): 2.5 s after the
  start-up `ResizeBuffers`, worker threads 0274/0294 log `wined3d_texture_gl_bind Failed to
  generate a texture name`, `FBO 0 is incomplete`, `glClientWaitSync returned 0` = the GL context
  is no longer current/valid (the X drawable behind it went away), then the process ends.
  `WINE_X11_ONSCREEN_CHILD_GL=1` (our launcher default) parents the game's swapchain child X
  window to the toplevel's *whole* X window (`dlls/winex11.drv/init.c`, `window.c:2392`); if
  winex11 recreates the whole window (`set_window_visual`, `window.c:2616`) the GL child dies
  with it. Not proven yet: needs a display run with `+win,+x11drv,+opengl`.

**Headless reproduction.** With `WindowedMode:false` the game starts at 2560x1440 headless too
(no WM): no crash, but the XAML UI was missing — that turned out to be the compositor device race
described in the next section, not a full-screen problem; with that fixed the full-screen start
renders completely. The user's config was switched back to `WindowedMode:true` meanwhile
(backup of the full-screen config in the session scratchpad). What remains full-screen-specific
is the display-only ending (GL context loss / self-close), still unexplained.

**Steam "still running".** The launcher `exec`ed wine, so nothing observed what outlived the game.
`~/.steam/root/compatibilitytools.d/qwilight-wine/run` now runs wine, logs the exit status, waits up
to 10 s with `wineserver -w`, logs whatever processes are still alive and ends the prefix session
with `wineserver -k` (backup `run.bak-20260909b`). The next "still running" case will show the
culprit in `steam-run.log` under "processes still alive after exit".

**GPU compositor caches keyed by texture address.** `dlls/dcomp/gpu.c` cached opened shared
textures and sampleable copies by the source `ID3D11Texture2D *`; D3D reuses addresses after a
resize, so a stale entry could be served for a new texture. Entries now carry a private-data cookie
(`GUID_wine_dcomp_gpu_cookie`) set on the source; a reused address without the cookie evicts the
entry.

## 2026-09-09 — Settings dialog crashed after opening; profile flyout fail-fast; stub and glyph scanners (main, session 7b, later)

**"Opening settings shows the settings then crashes."** The user's `steam-run.log` ended with
`wine: Call from ... to unimplemented function ninput.dll.SetMouseWheelParameterInteractionContext`.
The dialog's ScrollViewer creates an InteractionContext and configures it; Wine's `ninput.dll` had
that entry (and eleven others) as spec stubs. Implemented as parameter stores in
`dlls/ninput/main.c`: `Set/GetMouseWheelParameterInteractionContext`,
`Set/GetInertiaParameterInteractionContext`, `SetCrossSlideParametersInteractionContext` /
`GetCrossSlideParameterInteractionContext`, `SetPivotInteractionContext`, `Reset`/`Stop`,
`Add/RemovePointerInteractionContext`, `ProcessPointerFramesInteractionContext` (FIXME: no
gestures are produced). The enums `MOUSE_WHEEL_PARAMETER`, `INERTIA_PARAMETER`,
`CROSS_SLIDE_THRESHOLD` and `CROSS_SLIDE_PARAMETER` were added to `include/interactioncontext.h`
from the SDK header. Headless: settings dialog + 26 wheel events, no crash. The wheel does not
scroll the dialog's list yet (InteractionContext produces no output), see Known-open issues.

**"Clicking on profile still crashes."** The user's run (19:46) fail-fasted at
`Microsoft.UI.Input.dll` RVA 0xce037 = `ContentExternalBackdropLink::EnsureSystemBackdropVisual`
+0x3af (module base taken from a `+module` headless run, then `./resolve.py survey/beta.input.map`).
The profile opens an unconstrained popup with an acrylic backdrop; that code creates a *system*
(`Windows.UI.Composition`) visual through the compositor's interop device and
`FAIL_FAST_IF_FAILED`s the `QueryInterface` for `IVisual2` {3052b611-56c3-4c3e-8bf3-f6e1ad473f06}
(identified by GUID lookup in `Microsoft.Windows.SDK.NET.dll` with `dotnet fsi`). Wine's
`windows.ui.composition` visual answered only `IVisual`. Added `IVisual2` to
`include/windows.ui.composition.idl` (order ParentForTransform, RelativeOffsetAdjustment,
RelativeSizeAdjustment, verified by metadata token order) and to `dlls/windows.ui.composition/
visual.c` (stored only, FIXME when a non-zero RelativeSizeAdjustment is set). Not reproduced
headless: no click I found opens that popup (avatar = Game Settings, name label = nothing), so
this is verified only by disassembly; the user has to re-test. Next thing that will run after
the QI is `Microsoft.Internal.FrameworkUdk!DCompPrivates_OpenAndAttachSharedTarget`.

**Scanners (user request: "detect everything we still need to implement").**
* `tools/stubscan.py [--log LOG ...]` — static: parses the import and delay-import tables of
  every native DLL/EXE in the game directory (own PE parser, no pefile), resolves `api-ms-*`
  names through `dlls/apisetschema/apisetschema.spec`, and lists imports whose Wine `.spec` entry
  is a `stub` or missing (or where Wine has no such builtin at all: those are the game's own
  DLLs like MRM/CoreMessagingXP/InputStateManager, listed for completeness). Dynamic: unique
  counts from logs of `Failed to find library` (WinRT classes), `unimplemented function`,
  `stub!`/`semi-stub` fixmes, unknown IIDs, `Unhandled` E_NOTIMPL. Output of this session in
  `survey/stubscan.txt`. Real remaining spec stubs the beta links: `ninput` ordinals 2500/2501/
  2505, `shcore` #244 + `CreateRandomAccessStreamOnFile`, `wintypes.RoGetMetaDataFile`,
  `d3dcompiler_47.D3DReflectLibrary` (native override anyway), `combase.HSTRING_User*64`
  (Widgets only), `profapi` #114, `ntdll.DbgPrompt`; missing exports: `d2d1` ordinals #1-#5/#13
  (private D2D factory entry points used by dwmcorei/Win2D), `kernel32` package APIs
  (`GetPackageInfo`, `OpenPackageInfoByFullName`, ...), `OfferVirtualMemory`/`ReclaimVirtualMemory`,
  `SetThreadpoolWaitEx`, `powrprof.PowerUnregisterFromEffectivePowerModeNotifications`,
  `iphlpapi.GetIpNetEntry2`/`ResolveIpNetEntry2` (msquic), `secur32.SetCredentialsAttributesW`.
  Delay-loaded ones only bite when reached.
* `tools/glyphscan.py` — collects every non-ASCII code point from `Assets/Language.json` (all
  locales), the C# string literals (`#US` heap of `Qwilight.dll`; the raw-string scan of the DLL
  gives 23k code points of embedded tables and is useless) and the skin's text files; per Unicode
  block prints count, samples, whether `dlls/dwrite/analyzer.c` has a fallback entry, and whether
  fontconfig has a font. Output `survey/glyphscan.txt`: 890 code points, 15 blocks.

**Glyph findings.** Emoji in the C# literals are the remaining "character blocks":
`💊 EASY JUDGMENT`, `💣 HARD JUDGMENT`, `🌐 SLOWER`, `Favorites ⭐`, `❌`, `👍 #,##0`, `Qwilight 🔧`
(the box after "Game settings" in the hint bar), `🔙`, `👑` (the toast). The only fonts here with
those code points are Noto Color Emoji (CBDT colour bitmaps, which Wine's dwrite cannot rasterise)
and Unifont Upper. Added `Noto Emoji` (Google's monochrome outline emoji font) first in the
fallback lists for 2600-27BF, 2B00-2BFF and 1F300-1F9FF, and new entries for Greek (0370-03FF,
1F00-1FFF) and 2000-218F (punctuation, superscripts, currency, letterlike ™, number forms). To get
emoji the user installs a monochrome outline emoji font (`ttf-noto-emoji-monochrome` from the AUR,
or drop `NotoEmoji-Regular.ttf` into the prefix's `windows/Fonts`). WinUI's own icons (dialog
close X, check marks, chevrons) are `FontIcon`s in Segoe Fluent Icons / Segoe MDL2 Assets private
use code points from WinUI's `generic.xaml`, not from the game; they need `SegoeIcons.ttf` /
`segmdl2.ttf` from a Windows installation copied into the prefix's `windows/Fonts`.

**Beware when testing headless while building.** One scenario run started seconds before a
`make` relinked `dwrite.dll` showed the old "UI mostly invisible" picture (`survey/nf/wheel2.log`,
`check_geometry_type` 176 instead of ~2000); the same scenario re-run after the build was fine.
Do not build while a headless run is starting.

## 2026-09-09 — Profile/settings click crashed: two missing WinRT/Win32 pieces; ◀ ▶ tofu (main, session 7b, later)

**Symptom.** "Clicking on the user profile crashes the game", "clicking on settings also doesn't
seem to work". The avatar at the top-left opens the Game Settings dialog. Headless (`SCENARIO=profile
scratchpad/betahl10-scenario.sh`) the click ended in a .NET exception → fail-fast `c0000409`.

**Cause 1 — `Windows.Globalization.NumberFormatting` did not exist in Wine.** The dialog's sliders
use `NumberBox`/`Slider` formatting: `RoGetActivationFactory` for
`SignificantDigitsNumberRounder` and `DecimalFormatter` failed with "Failed to find library", which
C#/WinRT turns into a `TypeLoadException`. Added the namespace to `windows.globalization.dll`:
`include/windows.globalization.numberformatting.idl` (GUIDs and vtable order reflected out of
`Microsoft.Windows.SDK.NET.dll` with `dotnet fsi`, see `scratchpad/guids.fsx`/`vt.fsx`) and
`dlls/windows.globalization/numberformatting.c`: `DecimalFormatter` (`INumberFormatter`/`2`,
`INumberFormatterOptions`, `INumberParser` (E_NOTIMPL), `INumberRounderOption`,
`ISignedZeroOption`, `ISignificantDigitsOption`, `IDecimalFormatterFactory`),
`SignificantDigitsNumberRounder` and `IncrementNumberRounder` (`INumberRounder` + their option
interfaces, all eleven `RoundingAlgorithm`s). Formatting is invariant-culture only (`.` and `,`).
The prefix needs `wineboot -u` once so the three `ActivatableClassId` keys exist.

**Cause 2 — `bcp47mrm.dll.GetDistanceOfClosestLanguageInList` was a spec stub.** With the
formatter in place the dialog got further: `Microsoft.Windows.ApplicationModel.Resources.
ResourceManager` (`MRM.dll` from the game directory) resolves the dialog's localized strings and
calls the stub, which raises `EXCEPTION_WINE_STUB` → `RoReportUnhandledError 0x80004005 "Call
failed."` and the dialog never opened. Implemented: exact tag = 0.0, same primary language with
shared further subtags = 0.1, same primary language only = 0.2, unrelated = 1.0, lowest over the
delimiter-separated list.

**Result.** Headless the avatar click opens Game Settings (tabs, sliders, BPM box, F1–F8 save
slots all render; `survey/nf/scenario-P1-profile.png`), no fail-fast. Not yet confirmed on the
display.

**◀ ▶ tofu ("a lot of character blocks").** `Assets/Language.json` uses U+25C0/U+25B6/U+25A0/
U+25B2/U+25BC (Geometric Shapes) in the hint bar and elsewhere. WinUI links `dwrite.dll` (Wine's,
not the bundled `DWriteCore.dll`) and Wine's system font fallback table in `dlls/dwrite/analyzer.c`
had no entry for U+2500–25FF, so no fallback font was tried. Added
`{ "2500-25FF", "Noto Sans Symbols2, Noto Sans Symbols 2, DejaVu Sans, Unifont" }`. The remaining
boxes (the dialog's close button, the toast icon, "Game settings □" in the hint bar's key glyphs)
are `FontIcon`s in Segoe Fluent Icons / Segoe MDL2 Assets private-use code points, which no free
font provides; they stay boxes unless the user installs that font into the prefix.

## 2026-09-09 — Scroll wheel fail-fast, resize losing the composited game, resize keeping the old XAML layout (main, session 7b)

**Wheel → `c0000409` in `LiftedFrameBuilder::StartNewFrame` ("FrameId is going backwards!").**
ISM base recovered from the session-7 crash (`ProcessPointerFrameContact+0xb6` = 0x2247a);
the new address resolves to `StartNewFrame+0x67` (`survey/ism.map`), whose check is
`new frameId <= last frameId → wil FailFast`. Cause: a wheel is not composition-sink input — the
focus window (XAML's `InputSiteWindowClass`) gets `WM_POINTERWHEEL` and injects it into the ISM
itself (`InputSiteConnection::InjectPointerWheel`) with the pointer's *current* frame id, while
older `WM_POINTERUPDATE`s were still queued for the ISM's thread; those then arrived with smaller
ids. Reproduced headless with XTest wheel bursts (`xscenario.py` wheel experiment: 2 fail-fasts).
Fix (`dlls/win32u/{input,message}.c`): a routed wheel is only *posted* to the ISM target like every
other pointer message (`post_sink_pointer_message(target, msg, forward_hwnd)`), and when the ISM
thread reaches it, `process_sink_pointer_message()` forwards it synchronously
(`WM_WINE_SINK_POINTER`, `forwarded` stage) to the input-site window on the UI thread with the
snapshot installed, so the injection happens after every earlier update. 40 wheels headless: no
fail-fast.

**Resize left the game scene but no XAML (GPU compositor).** After `ResizeBuffers` XAML binds
the swapchain to a new composition surface *before* releasing the old one; releasing the old
`IWineDCompHandleSurface` cleared the `GUID_wine_dxgi_composition_bound` tag, dxgi restored the
game's hosting child window and the game presented straight into it at ~900 fps over the
compositor. `surface_handle_set_bound()` (`dlls/dcomp/surface.c`) is now a reference count.

**Resize keeps the old XAML layout until alt-tab (open).** The island is composed at the new
window size but XAML never re-laid out (buttons at their old positions, hint bar old width);
alt-tabbing out and in fixes it. Headless, a Wine-side `SetWindowPos` on the top-level
(`scratchpad/tools/resizewin.exe`) *does* make XAML resize the island surface
(`surface_Resize 2192x1166`), so the difference is the window-manager-driven path
(`ConfigureNotify → NtUserSetRawWindowPos`); to be reproduced under `kwin_x11` on Xvfb
(`dbus-run-session -- kwin_x11 --replace` works there).

**Other inputs the beta uses (inventory from `Qwilight.dll` strings):** `Windows.Gaming.Input.Gamepad`
(Wine: `windows.gaming.input`), `Windows.Devices.Midi.MidiInPort` (Wine: `windows.devices.midi`
over winmm/ALSA; the app activates it at startup), Vortice DirectInput/XInput (`dinput8`, `xinput1_4`),
raw keyboard input (`RegisterRawInputDevices`/`GetRawInputData`), HidSharp. None testable headless
(no devices); the user has to try them and report the log.

## 2026-09-09 — "~10 fps everywhere": dcomp composited on the CPU; GPU compositor added (main, session 7b)

**Symptom.** With the atlas and pixel-format fixes in, the user still saw the whole window update at
roughly 10 fps, and the UI flashing at its old size while resizing. Headless timing of the GDI
compositor (`composite stats` FIXME every 2 s, TEMP DIAG in `composite_thread_proc`) at 1706x873:
~7 ms read-back (`CopySubresourceRegion` + `Map` of every layer) + ~7 ms `GdiAlphaBlend` per
frame on llvmpipe; on the user's box the `Map` additionally waits for everything the game queued at
~500 presents/s, and the whole thing ran at the 500 Hz panel period.

**Change 1 (throttle, `dlls/dcomp/device.c`).** The composition thread waits on
`__wine_dxgi_frame_event` (set by `dlls/dxgi/swapchain.c` `d3d11_swapchain_update_presented_surface()`
after each snapshot) instead of sleeping the panel period, composes at most every 16 ms and only
when a frame arrived, the device `batch_id` changed (Commit), the client size changed, or 500 ms
passed.

**Change 2 (GPU compositor, new `dlls/dcomp/gpu.c`, default on; `WINE_DCOMP_CPU=1` restores the
GDI path).** Per target a D3D11 swapchain is created on the target window
(`CreateSwapChainForHwnd`, B8G8R8A8, DISCARD, 1 buffer) on the device of the first content
texture; each visual's content is drawn as a textured quad (vertex-id strip, premultiplied
source-over blend, opacity in a constant buffer, scissor = the visual clip; the destination
rectangle comes from the same translate+scale matrix the GDI path used). The frame is recorded on
a deferred context and executed with `ExecuteCommandList(…, restore_state = TRUE)` so the
application's threads never see the compositor's pipeline state, then `Present(0)`. Content on
another D3D device (the game's Win2D device vs. XAML's) is opened on the compositor device through
`IDXGIResource1::CreateSharedHandle` / `ID3D11Device1::OpenSharedResource1` (cached per source
texture, cache capped at 8); for that the dxgi presented snapshot is now created with
`D3D11_RESOURCE_MISC_SHARED | SHARED_NTHANDLE`. `composite_target` gains `struct dcomp_gpu *gpu`,
released with the target. Headless: picture identical to the GDI path (`survey/scenario-A-start.png`
run route55), 60 composites/s, 0 ms CPU per frame in the stats; 1.x regression `tools/xvfbrun.py 4`
clean (`survey/xvfbrun-gpu.log`). **Needs the user's display**: the swapchain lands on the
DesktopChildSiteBridge child (target hwnd 0x10080), i.e. the same child-window GL presentation
path whose pixel format egl-x11 refused for the composition child — if the picture is black or the
log shows `Failed to create the compositor swapchain`, set `WINE_DCOMP_CPU=1` and report.

Not handled yet: rotation/skew transforms (warned, as before), the one-off "lives on another device
and is not shared; layer skipped" FIXME for the first frame before any snapshot exists (harmless).

## 2026-09-09 — "Performance is really bad" + resize crash: wined3d re-tried a failing swapchain DC on every context activation (main, session 7b)

**Symptom (user, real display).** Sluggish everything after the UI fix; resizing the window killed
the process with the known NVIDIA egl-x11 assertion (`eplX11SwapBuffers: Assertion
sharedPixmap->status == BUFFER_STATUS_IDLE`, see session 6). The Steam log
(`=== 18:02:55` launch) shows the cause of both: from the first present of the game's Win2D
composition swapchain (`wine_dxgi_composition` child 0x1009c, withdrawn because dcomp composites it)
the wined3d CS thread logs `wglSetPixelFormatWINE failed to set pixel format 90 on device context
0x19010067` **~2000 times a second** (8469 in 5 s; the game presents ~200-500/s). Headless never
hits it (0 in every route log) — on the user's NVIDIA/XWayland egl-x11 stack the composition child's
window surface cannot be created (session 6 saw the same failure intermittently).

**Mechanism.** `wined3d_context_gl_set_gl_context()` falls back to the device's backup DC when the
pixel format cannot be set — fine once — but `wined3d_context_gl_update_window()` compared
`context_gl->dc` with `swapchain->dc` and, since the context was now on the backup DC, re-acquired
the swapchain DC on **every** activation: fail → wglSetPixelFormatWINE → backup → wglMakeCurrent
→ next draw batch, again. Thousands of EGL surface/context switches per second explain the
performance and are the obvious trigger for the egl-x11 pixmap-state assertion on resize.

**Fix.** `dlls/wined3d/wined3d_gl.h`: `wined3d_context_gl` gains `swapchain_dc` (the swapchain DC
last picked up); `context_gl.c`: `update_window()` compares against that instead of the DC in use,
so a context that fell back to the backup DC stays there until the swapchain's DC actually changes
(`wined3d_swapchain_set_window()`). Verified: beta scenario unchanged, 1.x regression
`tools/xvfbrun.py 4`: 459 presents, 0 errors. **Needs the user's display to confirm** (perf and the
resize crash); to see *why* the surface creation fails, launch with
`QWILIGHT_WINEDEBUG=warn+module,+timestamp,warn+seh,warn+combase,warn+wgl` — the session-6 WARN in
`x11drv_egl_surface_create` prints `eglGetError()`.

**Follow-up (same session).** The user's next run (`=== 18:23:05`) shows the churn gone (19
`wglSetPixelFormatWINE` failures in 25 s instead of 8469 in 5 s) and a clean close, no crash. Report
was now "still frames displayed and flashing when resizing" (meaning to be clarified). Second
change landed for the compositor cost: `dlls/dcomp/device.c` `composite_thread_proc()` no longer
spins at the panel period (2 ms on the user's 500 Hz monitor, each iteration a CPU read-back of
every layer); it composes at most every 16 ms and only when dxgi signalled a presented frame
(`__wine_dxgi_frame_event`, set in `d3d11_swapchain_update_presented_surface()`), the device
`batch_id` changed (Commit), the target's client size changed, or 500 ms passed. Headless: scenario
unchanged.

**Also in the log, not fixed.** `ResizeBuffers` on the composition swapchain logs
`wined3d_swapchain_resize_buffers Something's still holding back buffer 0` (non-fatal, resize
continues); `d3d11_swapchain_Present1` is called at ~500/s with no cap — each present snapshots
the 2560x1332 back buffer for the compositor; the XAML island composes at ~16 fps and Wine's dcomp
compositor reads both layers back to the CPU every refresh (`do_composite_dxgi_surface`: staging
copy + `Map` + swizzle + `BitBlt`). If performance is still poor after this fix, that CPU
compositor is the next target (GPU composition into a window swapchain).

**Add-folder crash (user report, unreproduced).** Headless, double-clicking the `All` entry opens
the FOLDER OPTION list and clicking `FOLDER OPTION` does nothing visible; the user's 17:47 run ends
without any exception in the log. Ask how the folder was added.

## 2026-09-09 — "UI invisible until hovered": dcompi's guard rectangles were ignored, XAML's alpha-mask Clear() wiped the shared atlas (main, session 7b)

**Symptom (user, real display, after input started working).** Parts of the UI invisible or only
appearing on hover; hovering flickers other elements in for a moment. Reproduced headless with
`scratchpad/betahl10-scenario.sh` (`xscenario.py`: focus click, click the `Y-ATK LIFE` mode button →
"Game Mode Settings" dialog, `Escape`): after the dialog closes the bottom hint bar never comes back
(`survey/scenario-W-escape.png` before the fix). Before the fix the very first frame was already
incomplete — no avatar, no `Difficulty Table Settings`/`Classification settings` buttons, no search
box, no tabs, no stats text, and the dialog rendered as loose fragments over a dim layer.

**What actually happens.** All lifted-composition surfaces of the app live in atlases owned by
`dcompi.dll` (`DirectComposition::CAtlasSurfacePool`, one 1728x992 A8 texture for glyphs and
alpha masks, 2592x1472 BGRA ones for element caches; `CreateTexture2D … misc 0x802`). Two BeginDraw
flavours exist in `CAtlasSurfacePool::BeginDraw` (dcompi +0x3a24c):

* D2D flavour (IID `ID2D1DeviceContext`, used by dwmcorei's text renderer): `method10` (shared view
  of the atlas) → `method7(view, tile)` (per-bitmap bounds, implemented earlier today) → the caller's
  `SetTarget`/`Clear`/`DrawGlyphRun` → `method7(NULL)` on EndDraw. Safe.
* raw flavour (IID `ID3D11Texture2D`/`IDXGISurface`, used by `Microsoft.UI.Xaml!AlphaMask::Impl::RasterizeElement`
  through `DCompSurface::BeginDraw` for rounded-corner/shape masks): dcompi calls
  `CDxDevice::SetGuardRect(texture, updateRect)` on the **private D3D11 partner device**
  (`{26c5dc23-e49c-4b0a-8f79-e7b1ac804d32}`, slots 6/7/8 = `SetGuardRect`/`SetEmptyGuardRect`/`RemoveGuardRect`),
  `DiscardResource`s the atlas and hands the *whole* texture + offset to XAML. XAML then does
  `CreateBitmapFromDxgiSurface(atlas, NULL)` → `SetTarget` → `BeginDraw` → **`Clear()`** → `FillGeometry`
  → `EndDraw`, with no clip of its own. On Windows the guard rectangle clamps every draw and clear
  into that texture to the update rect; commit `745561f9` had implemented the partner device with the
  three guard-rect methods as no-ops ("rendering hints"), so in Wine each mask rasterisation cleared
  the entire glyph atlas. Every text drawn earlier vanished until something re-rasterised it (hover
  → visual-state change → re-render), which is exactly the reported behaviour. The
  `WINE_D2D_DUMP` atlas dumps show the non-zero pixel count collapsing to the newest tile at each
  mask draw (`0087 → 0088` in `survey/impl-main-route35-run.log`'s dump series).

**Fix.** Guard rectangles implemented in wined3d:
* `struct wined3d_texture` gains `guard_rect`/`guard_rect_set` (client copy) and the same in
  `async` (CS copy, set by the new `WINED3D_CS_OP_SET_GUARD_RECT`); `wined3d_texture_set_guard_rect()`
  exported (`wined3d.spec`, `include/wine/wined3d.h`).
* `ffp_gl.c`: `scissor()` enables the scissor test when render target 0 has a guard rect;
  `scissorrect()` clamps the application's scissor rects to it (or uses it alone when the app has
  no scissor). `context_gl.c` `context_apply_draw_state()` re-applies both when the guard status of
  render target 0 changes (`context->applied_guard_rect`). `cs.c`: both clear emit paths intersect
  `draw_rect` with the guard rect. Vulkan backend untouched.
* `dlls/d3d11/device.c`: the partner-device methods call `wined3d_texture_set_guard_rect()`
  (`SetEmptyGuardRect` = empty rect, `RemoveGuardRect` = NULL).

Result (`survey/scenario-*.png`, run `impl-main-route39`): the first frame shows the complete UI,
the Game Mode Settings dialog renders as a real dialog (checkboxes, toggles, scrollbar, its mode
row), and after `Escape` the page is identical to the start frame (hint bar included).
1444 `SetGuardRect` calls in a 45 s run. 1.x regression `tools/xvfbrun.py 4`: 581 presents, 0 errors (`survey/xvfbrun-guard.log`).

**Also fixed on the way.** `ID2D1DeviceContextUnknown::method8(rect, aa)` (was a stub) is the clip
for the next private queued draw (`method13`), `NULL` lifts it: `SetTransform → method8 → method13`
brackets every XAML D3D draw; now stored on the context (`has_private_clip`/`private_clip`, target
pixel space) and intersected into the scissor in `flush_queued_draw()`. Not the cause of this bug
but correct.

**Dead ends recorded so they are not repeated.** (1) The mode "popup" seen in the hover runs is the
app's Game Mode Settings dialog (opened by clicking a mode-row button; it needs the window to have
been clicked once first), not a rendering fault — it just rendered badly because of the atlas wipe.
(2) `d3d_state_installed` (D2D keeping its D3D state installed between BeginDraw/EndDraw) is not
involved (`WINE_D2D_NO_BRACKET_STATE` experiment, since removed). (3) XAML's element content is
not drawn through D2D at all: per island frame there are 4 private queued draws (`method13`) that
composite atlas tiles; the D2D tile brackets on the render thread are `Clear`-only. (4) The
per-tile `CopyResource(staging ← atlas)` + tiny `CreateBitmap(src_data)`/brush/rect-geometry
objects that are created and released without a draw are dwmcorei's CPU glyph path being prepared
and dropped; harmless.

**Tools added (all env-gated, TEMP DIAG, off by default).** `WINE_D2D_DUMP=<dir>` writes the
current target after every queued draw and every atlas when a tile finishes (BGRA and A8);
`WINE_D2D_DUMP_TRIGGER=Z:\tmp\d2ddump-trigger` gates it on a file the test script touches;
`WINE_D2D_DUMP_SKIP/COUNT/SMALL/A8ONLY` filter. `+xamltex` (d3d11) traces texture creation
(size/array/format/bind/misc), destruction, `CopyResource`/`CopySubresourceRegion`/`UpdateSubresource`.
`+sinkinput` (win32u) prints the mouse-routing decision per message. `d2d_bitmap_init` and
`flush_queued_draw` now TRACE their resource/target. `scratchpad/xscenario.py` +
`betahl10-scenario.sh` drive the dialog scenario with a screenshot after each step;
`xhover.py` honours `XHOVER_NOCLICK`/`XHOVER_STAY`.

## 2026-09-08 — Qwilight 2.0 beta (WinUI 3, WinAppSDK 2.4): first look (session 6)

The Steam beta branch (buildid 25171610, assembly version 1.17.13, installed
09:11) is a pure WinUI 3 app: no WPF assemblies, Windows App SDK **2.4.0**
(1.x used 1.8), Win2D still present, WebView2 bundled, media through
FFmpegInteropX (`FFmpegMediaSource` -> `MediaStreamSource`) with no
`ffprobe.exe`. The compat tool used to redirect every launch to the 1.x dev
build, so the user's first "beta" launch was still 1.x; it now runs the dev
build unless `QWILIGHT_DEV_BUILD=0` is in the launch options.

Run headless with `scratchpad/betahl.sh` (Xvfb :6 + llvmpipe, Steam copy as
shipped); it needs no display. Each fix below was found by running until the
next failure. All are built, all uncommitted.

1. **bcp47langs: `GetApplicationLanguages` and `LanguageListAsMuiForm`
   were stubs.** `Microsoft.Internal.FrameworkUdk.dll` calls them at startup
   and Wine aborts the process on a stub call. Signatures read from the
   caller's disassembly: `GetApplicationLanguages(void *ctx /*0*/, WCHAR delim
   /*';'*/, HSTRING *langs)` and `LanguageListAsMuiForm(WCHAR delim, HSTRING
   list, DWORD flags /*0*/, HSTRING *out)`. Both, and the existing
   `GetUserLanguages` stub that never filled its output, now return the user
   default locale; MuiForm returns a copy of its input.
2. **wintypes: `PropertyValue::GetRuntimeClassName` was E_NOTIMPL and
   `IAgileObject` was refused.** The XAML parser asks every boxed value for
   its class name; the failure surfaced as a C++ `hresult_error` inside
   `XamlControlsResources`'s constructor (symbolised with the public PDB) and
   a XAML parse fail-fast (0x802B000A). Now reports
   ``Windows.Foundation.IReference`1<T>`` / ``IReferenceArray`1<T>``. Note the
   array flag is 1024, not `PropertyType_UInt8Array` (1025): masking with the
   latter made a Double claim to be an array of Single, and CsWinRT then asked
   for `IReferenceArray<Single>` and threw. Fixed.
3. **dwrite: `IDWriteFontSet::QueryInterface` refused `IUnknown`.** Win2D
   wraps every DWrite object it hands out and starts with `As<IUnknown>`; the
   game builds a custom `CanvasFontSet` from its font files in a static
   constructor, so this was a `TypeInitializationException` fail-fast
   (0x80131534, E_FAIL). One line.

**Where it stops now (open).** The app reaches `OnLaunched`, constructs
`MainWindow`, and dies loading its XAML. The XAML `Window` creation runs
`Microsoft.UI.Windowing.Core!AppWindowTitleBar::IsNonClientCustomizationSupported`,
whose probe `CTitleBar::EvaluateNonClientCustomizationSupported` activates
the **system** `Windows.UI.Composition.Compositor`, queries the factory for
`IInteropCompositorFactoryPartner` {22118adf-23f1-4801-bcfa-66cbf48cc51b},
calls `CreateInteropCompositor(..., IID {b403ca50-7f8c-4e83-985f-cc45060036d8})`
and queries the result for {ca67b562-1c32-4017-9dd9-3d4b7e2510aa} (not in
any header here). Any failure is `THROW_IF_FAILED` in the caller, the throw
happens inside a window-creation callback, Wine cannot unwind through a user
callback (`err:seh:user_callback_handler ignoring exception e06d7363`) and
the process then faults in coreclr. Wine has the interface definitions
(`include/windows.ui.composition*.idl`) but no implementation of
Windows.UI.Composition at all, and a stub would only turn the probe into a
"supported" answer that WinUI then acts on. This is the composition work the
handoff already lists, now the first hard blocker for 2.0, and it is
independent of the game's own `MicaBackdrop` (which is requested right
after).

**Correction (survey, later on 2026-09-08).** The throw in `beta9.log` is in
`dcompi.dll`, not in the windowing DLLs: `Microsoft.UI.Xaml.Window` ->
`CreateWindowEx` -> `CompositionHelper::Initialize` (xaml rva 0x270028) ->
`dcompi!Compositor::CreateInteropCompositor` -> `SwitcherHelper::Initialize`
(dcompi rva 0x1dbf60) -> `RoGetActivationFactory("Windows.UI.Composition.Compositor")`
-> `winrt::hresult_class_not_registered`. The `CTitleBar` probe uses WIL's
non-throwing loggers and its caller just records "not supported"; it had not
even run yet. `{b403ca50-…}` is the public `ICompositor`; `{ca67b562-…}` is
`IDCompositionDesktopDevicePartner6`. The CodeWeavers dcomp series is already
in this tree via wine-staging (66 commits), so there is nothing to port; the
prefix's `system32/dcomp.dll` copy is one build behind, though the loader uses
the build tree. WinAppSDK 2.x's `dcompi.dll` is a switcher proxy over the
*system* Windows.UI.Composition, and `dwmcorei.dll` is never loaded, so a real
Windows.UI.Composition is on the first-frame critical path. Five survey
reports and the design gate for the implementation wave are in `survey/`.
The win32u pointer-input experiment is stashed (`git stash list`), per the
survey's recommendation; WinUI uses the process-wide `EnableMouseInPointer`.

Smaller things seen on the way: Win2D imports `d2d1.dll` ordinal 13 which
Wine does not export (harmless until called); `Windows.Globalization`,
`UISettings`, `GeographicRegion` are still stubs; the 1.x "Failed to find
library for Microsoft.Graphics.Canvas.*" errors are harmless (CsWinRT falls
back to `DllGetActivationFactory` in the app directory).

## 2026-09-09 night — "Some UI elements look messed up": stale mask sampling inside the A8 atlas (main, session 8, OPEN)

**Symptom (user screenshots, real display).** In Game Settings › Graphics Settings the disabled
`Always` CheckBox of the "Universal BGA" row shows fragments of other UI (text pieces, ring/disc
shapes) where its box should be; the dialog's close button shows a smeared dark blob behind its
(tofu) icon. Reproduced headless with the new `SCENARIO=graphics` step of `scratchpad/xscenario.py`
(`survey/nf/scenario-G1-graphics.png`); the exact fragments differ per run.

**Not the cause (each measured, not assumed).**
* Cross-device sharing: the mask atlas is exported by the XAML/dcompi device and imported by the
  render-thread device; readbacks of both sides at the same moment (`WINE_DRAW_DIAG_DUMP` importer
  side, `WINE_D2D_DUMP` exporter side) are pixel-identical except for tiles drawn in between.
* Location bugs in wined3d: the new `WINE_GUARD_DIAG=1` `SHARED texture` reports show the A8
  atlases never leave `TEXTURE_RGB` after creation (the per-frame `DISCARDED` reports are the
  2592x1472 BGRA caches being destroyed, `texture_resource_unload`).
* Guard rectangles / atlas wipe: every mask tile is rasterised inside its guard rect
  (`survey/…/small-tiles.png` contact sheet: before/after crops of 127 tiles, all correct).
* Shaders: the XAML "text/mask" pixel shader (`ps-*.dxbc`, disassembled in
  `survey/shaderdump/all2.txt`) is simply `o0 = COLOR * tex0(uv0) * tex1(uv1).a`; the vertex
  format is `POSITION xyz, COLOR (packed), TEXCOORD0, TEXCOORD1` at stride 32. Nothing to mistranslate.
* Copies: no `CopySubresourceRegion` ever targets an A8 atlas except the 1x1 touch at creation, so
  dcompi never relocates tiles.

**What actually happens (draw-level, `survey/nf/graphics14.log` + dumps).** XAML draws rounded
rectangles as tessellated geometry whose TEXCOORD1 addresses an alpha-mask tile in the 1792x1728 A8
atlas. Two mappings exist:
* nine-grid: the "Disable" button (radius 4) maps its corners onto a shared 22x22 disc tile and its
  straight edges onto the disc's 1-px centre row/column — correct, and every button/card is fine.
* element-sized: the checkbox's 40x40 box maps 1:1 onto a 40x40 outline tile at (609,104) — correct.
* **broken:** the checkbox's whole-control rounded rect (131x41 at island (997.5,1033.5)) maps 1:1
  onto atlas (1549.5,61.5)-(1680.5,102.5) as if an element-sized mask lived there. No 131x41 tile
  was ever rasterised in any run (`GUARD set` never shows one), and that region holds a row of
  22x22 disc tiles plus the `Always` glyph tile, which is exactly the on-screen garbage. The close
  button is the same pattern (element-sized sample of foreign tiles, blurred by bilinear filtering).
  So XAML holds a mask-cache entry for a surface that was never drawn (or was freed and its atlas
  space reused by dcompi) and keeps drawing with it. Which Wine behaviour makes XAML/dcompi do that
  is still open; candidates worth testing next: a failed/skipped `AlphaMask::RasterizeElement`
  BeginDraw for that element (nothing visible in the d2d/dxgi traces at tab-open time, but the
  raw-flavour BeginDraw path returns Wine objects), and pointer-keyed caching defeated by Wine's
  immediate heap address reuse (addresses are recycled within microseconds here).

**Tools added (all TEMP DIAG, env-gated, off by default).**
* d3d11 `WINE_GUARD_DIAG=1`: FIXME per `SetGuardRect`/`SetEmptyGuardRect`/`RemoveGuardRect`
  (d3d11 + wined3d texture pointers); wined3d `cs.c` FIXME per draw/clear whose RT has a guard rect
  (`guard_diag`); wined3d `texture.c` FIXME + backtrace when a shared texture's GL location is
  invalidated or reloaded (`SHARED texture …`).
* d3d11 `WINE_GUARD_DUMP=<dir>` (+`_TRIGGER`, `_COUNT`, `_A8ONLY`): BMP of the guarded texture at
  `SetGuardRect` and `RemoveGuardRect`.
* d3d11 `WINE_DRAW_DIAG=<trigger file>`: per immediate-context draw, the RT, viewport, scissor,
  shaders, vertex/index/constant buffers, PS SRVs (as d3d11 texture pointers + size) and ps cb0
  data; `WINE_DRAW_DIAG_RECT=l,t,r,b` prints every triangle touching the rect (vertices decoded
  from shadow copies taken at `Unmap`); `WINE_DRAW_DIAG_DUMP=<dir>` + `_W=<width>` dumps the srv1
  texture of that width (importer side).
* d3d11 `WINE_SHADER_DUMP=<dir>`: every created VS/PS bytecode as `<kind>-<wined3d ptr>.dxbc`;
  `scratchpad/disasm.exe` (mingw, uses the prefix's native d3dcompiler_47 `D3DDisassemble`) turns
  them into text: `tr -d '\0' < all.txt | sed 's/==== /\n==== /g'`.
* `scratchpad/xscenario.py` `SCENARIO=graphics` (`AX`/`AY` avatar fractions, default 0.086/0.095 for
  the 2560x1440 window): avatar → Graphics Settings tab → two scrollbar drags; touches
  `/tmp/guard-trigger` before the tab click and removes it after the G1 shot.

**Also learned.** The dialog itself is rendered into a 2592x1472 BGRA cache in four 1280x720
quadrants (guarded, `DiscardResource`d, drawn on the render thread); the game's own Win2D canvas is
the other 2560x1440 target (format 0x76), the XAML island is format 0x3a; XAML batches up to ~250
quads per draw in a 16 KB vertex ring; `SetEmptyGuardRect` follows every mask's `EndDraw` (guard
"nothing" between draws), it is not a clip-all before drawing.

## 2026-09-10 — Skin background videos, the start-up BadWindow crash, and the "garbage on buttons" quad identified (main, session 8b)

**"Some background images do not render" — they are videos, now playing.** `yucky/UI/@Default/tmp/Media/{0,1}.mp4`.
The beta loads them with `MediaSource.CreateFromStream(IRandomAccessStream, "video/mp4")`, a stub in
`windows.media.core` (the 1.x path is `CreateFromUri`). Implemented: the source keeps the stream and
content type; `IWineMediaSourceInternal` (`include/wine/winrt_media_source.h`) gained `get_stream`;
the player (`windows.media.playback.mediaplayer`) wraps the stream as `IStream` with shcore's
`CreateStreamOverRandomAccessStream`, then `MFCreateMFByteStreamOnStream`, sets
`MF_BYTESTREAM_CONTENT_TYPE` and calls `IMFMediaEngineEx::SetSourceFromByteStream` with a pseudo URL
carrying the subtype as extension (`winrt-stream.mp4`). `mfplat.MFCreateMFByteStreamOnStreamEx` is
still a stub, that is why shcore is used. Headless the song-select background animates again
(`survey/nf/media5-{20..60}.png` differ frame to frame; before, black).

**Start-up "X Error of failed request: BadWindow (X_UnmapWindow)" — process exit, ~1 run in 3 after a
reboot, never with `+x11drv` tracing (timing).** Xlib's default handler exits the process. A
map/unmap queued for a window another thread's connection has already destroyed is harmless, so
`ignore_error()` in `dlls/winex11.drv/x11drv_main.c` now ignores BadWindow for `X_UnmapWindow` and
`X_MapWindow` with a WARN. The originator is not identified (the hosting-window hide posted from
the dxgi factory on withdraw/restore is the prime suspect); 6 later runs had no error and no WARN,
so the race is rare.

**The artifact quad, pinned.** With the new `WINE_MASK_DIAG=1` detector (d3d11: flags any XAML quad
whose mask UVs map 1:1 over ≥12 px but lie in no rasterised tile) two headless runs flag exactly one
element, deterministically, from the first frame after the Graphics tab opens: the disabled `Always`
CheckBox, screen (998,1034)-(1129,1075). Vertex colour `0x5d5d5d5d` = the disabled text colour with
opacity; its atlas rect (1550,63)-(1681,104) maps the `Always` glyph tile (1604,74)-(1680,102) to the
text's true screen position. So this is the CheckBox *content text* drawn as an element-sized quad
(131x41) whose surface only ever received the 76x28 glyph run (`method7` bounds + `Clear` +
`DrawGlyphRun`, guard rect (1604,74)-(1680,102)); nothing cleared the rest of the 131x41 area, and
200 ms later dcompi allocated 22x22 disc tiles inside it (y 51..73), which is the garbage seen.
Enabled text ("Disable", "Universal BGA") is drawn with a quad exactly the glyph tile's size, so
only text drawn *with opacity* (disabled controls) and controls whose content is such text show it —
matching the user's "buttons and some words, random" (random = whatever the reused atlas area held).
Open: whether XAML requested a 131x41 surface that dcompi never cleared/reserved (then a missing clear
on Wine's side: `ClearView` is a stub but is never called; `DiscardResource/DiscardView` are stubs),
or whether the surface was released and re-used while the quad kept its old rect. Next step: trace
dcompi's surface allocation calls for that element (`+d2d` `method10` desc + `method7` rects on the
1792x1728 atlas around the tab click, `survey/nf/graphics11.log` line 532503 is the glyph tile) and
compare with a text element that works.

Tools: `WINE_MASK_DIAG=1` (d3d11 `mask_diag_check`, tiles keyed by atlas size because guard rects
are set on the exporter object and draws sample the importer); `WINE_DRAW_DIAG_RECT2` second rect;
vertex colour printed as hex in `WINE_DRAW_DIAG` output. The close button and the song-select
"Difficulty Table Settings" smear are not flagged by the detector (their quads have uv0 ≠ 0, the
AA-outline kind); loosen the uv0 filter to catch them.

## 2026-09-10 — "Garbage on buttons and words": FOUND AND FIXED — d2d1 geometry INTERSECT approximation (main, session 8b)

**Root cause.** dwmcorei draws an element whose content is clipped by its parent's rounded corners
(a disabled CheckBox's text, the dialog close button's content, any text smaller than the rounded
control around it) as `FillGeometry(clip ∩ contentRect)` with the content's atlas surface as the
opacity mask. The intersection is asked through the private `ID2D1FactoryUnknown::method2(mode 1)`
→ `ID2D1RectangleGeometry::CombineWithGeometry(INTERSECT)`. This tree's
`d2d_geometry_combine_approx()` handled INTERSECT by "assuming the second geometry lies inside the
first" and emitted the *whole clip path*, so the quad covered the entire control at 1:1 atlas
coordinates and sampled whatever tiles happened to surround the glyph tile — text fragments, discs,
outlines. Which elements: exactly those whose content rectangle is smaller than the rounded clip
(when the clip is inside the rectangle the old assumption was exact, so most buttons were fine).
"Random" on the display = whatever the recycled atlas area held.

**How it was proved.** `WINE_MASK_SURVEY=1` (d3d11) listed 1153 distinct atlas-sampling quads in the
Graphics tab frame; all sat inside rasterised tiles except three strips of one element, coloured
`0x5d5d5d5d` = `TextFillColorDisabled` (#5DFFFFFF premultiplied) and shaped as a nine-grid
rounded-rect tessellation — i.e. text painted through the control's clip shape. The `method2` calls
land on the render thread at exactly the tab-open time (`survey/nf/graphics11.log` 51785.755+).

**Fix.** `dlls/d2d1/geometry.c`: for INTERSECT where either operand is an axis-aligned rectangle
(rectangle geometry, optionally under a scale/translate transform) the flattened other geometry is
clipped against the rectangle (Sutherland–Hodgman, `d2d_figure_clip_rect_emit`) and emitted exactly;
other combinations keep the approximation (110 INTERSECT and 210 EXCLUDE fixmes remain per run).
Result: `WINE_MASK_DIAG` flags nothing, the checkbox and close button render cleanly
(`survey/nf/fix1-G1.png`), `tools/xvfbrun.py 4`: 629 presents, 0 errors. Upstreamable in spirit
(upstream Wine has no CombineWithGeometry at all).

**Also on the way (kept).** `ID3D11DeviceContext1::ClearView` implemented for render-target views and
`D3D11_FEATURE_D3D11_OPTIONS` now reports `ClearView`/`DiscardAPIsSeenByDriver` TRUE — dcompi never
called it, so it is not part of this fix, but the stub was wrong.

**Still open from the user.** "Opening settings caused a freeze" on the display with today's build
(logging was off: `WINEDEBUG=-all`); needs a run with
`QWILIGHT_WINEDEBUG=warn+module,+timestamp,+seh,+mediaplayer,+mediacore`. The video path is the
newest suspect. Headless the dialog opens fine.

## 2026-09-10 — Skin/BGA video, take two: the beta's loader needs IMediaSource4 + a real OpenAsync (main, session 8b)

**Correction.** The 2026-09-10 morning entry claimed the stream path worked because the song-select
background moved; that was the *audio visualizer* (it appeared once the song list had entries). The
video did not play. The user's log showed the media sources released right after creation.

**What the beta does (IL of `MediaSystem.<Load>d__11::MoveNext`, decoded with
`scratchpad/il.fsx` + System.Reflection.Metadata).** Depending on the BGA-engine setting either
FFmpegInteropX (`FFmpegMediaSource.CreateFromStreamAsync`) or Media Foundation:
`MediaSource.CreateFromStream` → **`OpenAsync()`** (IMediaSource4, not implemented → the loader threw)
→ `new MediaPlaybackItem(src)` → `src.Duration` (stub returned null → `Length` 0 → `Handle()` never
called `Play()`) → `new MediaPlayer { Source, IsVideoFrameServerEnabled }` → later `Handle()`:
`VideoFrameAvailable` + `Play()` → `CopyFrameToVideoSurface` into a `CanvasRenderTarget`.

**Fixes.**
* `include/windows.media.core.idl` + `dlls/windows.media.core/main.c`: `IMediaSource3` (Reset/State/
  StateChanged) and `IMediaSource4`. **Vtable order caveat:** the CsWinRT projection assembly's
  metadata-token order put `OpenAsync` first, but the game's `OpenAsync` call landed on the last
  slot; the real order is the four properties first, `OpenAsync` last. Verified by the call landing.
* `OpenAsync()` now (a) snapshots the IRandomAccessStream into a memory `IStream`
  (`media_source_snapshot`; the app disposes its .NET stream after the loader returns, so every later
  Media Foundation read failed with `RO_E_CLOSED`), (b) resolves the source once with
  `IMFSourceResolver` to read `MF_PD_DURATION` (`media_source_open`), then returns a completed
  `IAsyncAction` (own implementation with `IAsyncInfo`). `Duration` returns an
  `IReference<TimeSpan>` via `PropertyValue.CreateTimeSpan`. The player takes the snapshot
  (`IWineMediaSourceInternal::get_stream` hands out an `IStream` clone; the player QIs for `IStream`
  before falling back to shcore). Big BGA files go through the file/URI path, so the copy only affects
  stream sources (skin media, ~1–27 MB here).
* Result headless: both skin videos open (durations 10 s / 20 s), `Play`, 1788
  `CopyFrameToVideoSurface` calls in 60 s, the animated background is visible
  (`survey/nf/media10-60.png`). `tools/xvfbrun.py 4`: 663 presents, 0 errors. Untested on the display.

**Profile pictures / `CanvasBitmap`.** Only 8 Win2D classes were registered in the prefix
(`HKLM\Software\Microsoft\WindowsRuntime\ActivatableClassId`); `Microsoft.Graphics.Canvas.CanvasBitmap`
and `CanvasTextLayout` activations failed ("Failed to find library"). All 112 runtime classes named in
`Microsoft.Graphics.Canvas.dll` are now registered (`scratchpad/win2d.reg`, imported with regedit).
Untested on the display (profile pictures are HTTP + CanvasBitmap.LoadAsync).

**Still open from the user's list.** Difficulty-table download "shows downloading then nothing" and
ComboBox dropdowns: headless, clicking "Difficulty Table Settings" opened no dialog
(`SCENARIO=table`, `survey/nf/table1-T1.png`), no error logged — needs a display log or a better
click sequence. "Lower displayed frames everywhere": not looked at this session.

## 2026-09-10 — Icon/emoji tofu: Wine's dwrite only sees fonts listed in the registry (main, session 8b)

**The finding that matters.** Dropping a `.ttf` into the prefix's `windows\Fonts`
is **not** enough for DirectWrite. GDI/win32u picks it up from the directory
(`load_file_system_fonts` in `dlls/win32u/font.c` scans `C:\windows\fonts` with
flags 0), but `dlls/dwrite/main.c:create_system_path_list` builds the system font
set **purely from `HKLM\Software\Microsoft\Windows NT\CurrentVersion\Fonts`**, and
Wine only writes that key for *external* (Unix) fonts via `update_external_font_keys`.
So a hand-installed font is visible to GDI and invisible to dwrite — and therefore
to XAML, Win2D and the whole beta UI. `wineboot -u` does not help.

Fix: add a registry value per font. A value with no backslash gets `%WINDIR%\fonts\`
prefixed, so the bare filename is enough (matches the Windows convention):

```
HKLM\Software\Microsoft\Windows NT\CurrentVersion\Fonts
  "Segoe Fluent Icons (TrueType)" = "SegoeIcons.ttf"
  "Segoe MDL2 Assets (TrueType)"  = "segmdl2.ttf"
  "Segoe UI Symbol (TrueType)"    = "SEGUISYM.TTF"
```

**Diagnosing this.** `WINEDEBUG=+font` shows `insert_face_in_family_list` for the
file — that is GDI, and it lies about dwrite. The oracle is a 50-line dwrite probe
(`FindFamilyName` + `IDWriteFont::HasCharacter`), kept at
`scratchpad/fontprobe.c`, built with `x86_64-w64-mingw32-gcc -o fontprobe.exe
fontprobe.c -ldwrite -lole32` (no `-municode`; it is a console `main`). Before the
registry entries it reported `exists=0` for all three fonts; after, `exists=1` with
the expected coverage. Check the probe before believing anything about a font.

**Fonts now installed** in `qwpfx-dev/drive_c/windows/Fonts` (3 files only):
`SegoeIcons.ttf` + `segmdl2.ttf` (Segoe Fluent Icons v1.54 / MDL2 v1.86, from a
Windows 11 install — WinUI's FontFamily string in `Microsoft.ui.xaml.dll` is
literally `Segoe Fluent Icons,Segoe MDL2 Assets`) and `SEGUISYM.TTF` (Segoe UI
Symbol). Segoe UI Symbol is plain `glyf`, no COLR, and covers every emoji literal
the game uses (`💊 💣 🌐 ⭐ ❌ 👍 🔧 🔙 👑`) plus the Geometric Shapes — so
`ttf-noto-emoji-monochrome` is **not** needed after all.
`dlls/dwrite/analyzer.c`: added `Segoe UI Symbol` to the fallback lists for
`2600-26FF`, `2700-27BF`, `2B00-2BFF` and `1F300-1F9FF`.

**Result.** The toast icons (both the blue ⓘ and the green ✓ — they were Fluent
Icons PUA glyphs, not the `👑` the earlier note guessed), the grid icon under the
search box, `⬅BACK`, the folder icons and the hint bar's `◀ ▶` all render. No tofu
left on song select.

**Do not install the Segoe UI *text* family and drop the `Fonts\Replacements`
mapping.** `HKCU\Software\Wine\Fonts\Replacements` maps `Segoe UI*` → `Noto Sans`;
removing those while dwrite cannot actually resolve a real `Segoe UI` fail-fasts the
app at startup — `WinUIEdit.dll` → `IDWriteFactory6::GetSystemFontCollection` →
`c0000602`, stowed `0x8000ffff`, nested type `0x4c4d4158` ("XAML"). Reproduced with a
single `SEGOEUI.TTF` present but unregistered. The replacements stay.

**Bisecting startup like this:** back-to-back headless runs give false "stuck on the
splash" results — two of five runs hung until a `pkill -x Qwilight.exe; pkill -x Xvfb;
sleep 3` was added between them. `scratchpad/trial.sh <tag>` does that and reports
PROGRESSED / STUCK by comparing frame hashes. "alive: yes" alone proves nothing.

## 2026-09-10 — Difficulty tables "downloading… then nothing", profile pictures: Windows.Web.Http had no transport (main, session 8b)

**Cause.** The beta does all its web traffic through `Windows.Web.Http.HttpClient` (`GetAsync`,
`SendRequestAsync` with `HttpStringContent`/`HttpBufferContent`, `EnsureSuccessStatusCode`,
`Content.ReadAsStringAsync/ReadAsBufferAsync/WriteToStreamAsync`; member list from the metadata via
`scratchpad/refs.fsx`). Wine's `windows.web.http` (Wave 2) built the object graph but every operation
completed with `WININET_E_CANNOT_CONNECT` (`no_transport_callback`, 21 times in the user's run), so
the "Downloading difficulty table…" toast was followed by nothing, and avatars never loaded.

**Fix: a winhttp transport** (`dlls/windows.web.http/transport.c`, ~600 lines):
`HttpResponseMessage`, `HttpResponseHeaderCollection` (raw list, typed getters return NULL), an
`IBuffer`+`IBufferByteAccess`, and `transport_send_callback` which runs on the module's thread pool
(returns `STATUS_PENDING` on the synchronous first call), cracks the URL, replays request headers
(list, User-Agent product tokens, Authorization scheme+token), sends the content body, enables
decompression, reads status/reason/raw headers/body and hands back a response whose content is a new
`CONTENT_BYTES` kind. `message.c`: content read operations now return real data (UTF-8 for strings,
BOM stripped; `WriteToStreamAsync` writes+flushes the `IOutputStream` and waits by polling
`IAsyncInfo`). `client.c`: URI overloads build a request (`GET`/`POST`/`PUT`/`DELETE`), the client's
default headers are merged into the request, no-filter clients go straight to the transport;
`filter.c`'s `SendRequestAsync` uses it too. `async.c`: HSTRING results are carried as `VT_UI8` (the
old `VT_UNKNOWN` slot would have AddRef'd/Released an HSTRING); the HSTRING handed out by
`GetResults` is owned by the caller. Headless: six HTTPS `GET`s to taehui.net → 200 (note/UI dates,
avatar drawings, title, 149 KB `qwilight.json`), the avatar + title + rank render
(`survey/nf/table2-T2.png`). The Difficulty Table Settings dialog itself could not be opened
headless (two clicks on the button, no dialog, no error), so the table download flow is untested;
the user must retest on the display.

**v1 settings ported** (user request): `/mnt/Yucky/Old/SteamLibrary/steamapps/common/Qwilight/SavesDir/Configure.json`
(1.17.2) merged into the beta's `yucky/Configure.json` (backup `Configure.json.bak-20260910-104546`):
32 keys taken from v1 (all input bundles, mode components, volumes, judgment/level wants, tutorial
flags, avatar login cipher; skin-specific config dicts merged), machine-specific keys kept from the
beta (window size, audio device, folder paths, skin selection, dates). 14 options changed from an
object to an int between versions and were not ported (fit/hunter/BPM modes, failed audio/drawing
handling, auto-enter sites, auto judgment meter, font family). `SavesDir/{UI,Level,Comment}` copied
into `yucky/` without overwriting (148 skin files, 84 tables, 7263 records).

## 2026-09-10 — Garbled non-ASCII file names (difficulty tables, skins): no locale under Steam (main, session 8b)

**Symptom.** The difficulty-table dropdown shows names like `g:gDPi#fe:&h!(` and the game cannot open
the table it lists; the user saw the same in v1. Each Japanese character became the low 7 bits of
its UTF-8 bytes (`難易度表` → `i#fe:&h!(`).

**Cause.** Steam starts the compat tool with no `LANG`/`LC_*`. Wine converts Unix file names with the
process locale's charset; for "C" that is the US-ASCII table, which maps every byte through
`& 0x7F`. Proven with `scratchpad/lsw.c` (FindFirstFileW code-point dump): no locale → `発狂` becomes
`0067 0019 003a 0067 000b 0002`; `LANG=en_US.UTF-8` → `767a 72c2`. The dropdown lists file names
(`LevelSystem.LoadLevelFiles` strips `.json`), so the mangled name never matches a file.

**Fix.** The launcher (`~/.steam/root/compatibilitytools.d/qwilight-wine/run`) exports
`LANG=en_US.UTF-8` (and clears `LC_ALL`/`LC_CTYPE`) unless the inherited locale is already UTF-8; the
environment dump now includes `LANG`/`LC_*`. Affects every non-ASCII path: skins, charts, BGA files.

## 2026-09-10 — ComboBox drop-down shows as a black rectangle (session 8b)

**Symptom.** The user's screenshot: the Difficulty Table Settings drop-down sits inside a rectangle
whose transparent parts are black. Reproduced headless with the Graphics Settings "BGA engine"
ComboBox (`CX=0.86 CY=0.925 SCENARIO=graphics …`, `survey/nf/combo2-G4.png`).

**Cause.** WinUI 3 hosts every unconstrained popup in its own top-level window
(`Microsoft.UI.Content.PopupWindowSiteBridge`, `WS_POPUP`, owner = main window,
`WS_EX_NOACTIVATE | WS_EX_NOREDIRECTIONBITMAP`) and creates a DirectComposition target for it. On
Windows DWM composes such a window with per-pixel alpha over whatever is behind it. Wine's dcomp
compositor presented the target through an opaque swapchain on the window and cleared it to opaque
black first (`dcomp_gpu_begin_frame`), so the rounded corners, the shadow and everything outside the
flyout came out black. Second problem found on the way: GDI output to a `WS_EX_NOREDIRECTIONBITMAP`
window (the default erase, `flush_window_surfaces` from the main thread) landed in the window
surface and overwrote the composed frame; on Windows such a window has no redirection surface and
GDI drawing to it is discarded.

**Fix.** `dlls/dcomp/target.c`: `create_target()` marks a target `alpha` when its window is an owned
`WS_POPUP` with `WS_EX_NOREDIRECTIONBITMAP` (opt-out `WINE_DCOMP_ALPHA_POPUPS=0`) and sets
`WS_EX_LAYERED` on it (HACK: styling the application's window). `dlls/dcomp/gpu.c`: alpha targets
render into an offscreen `B8G8R8A8` texture cleared to transparent (`dcomp_gpu_ensure_offscreen`),
which `dcomp_gpu_end_frame` copies to a staging texture, maps and hands to
`dcomp_target_present_alpha` (`device.c`), which copies it into the target's DIB and calls
`UpdateLayeredWindow(ULW_ALPHA, AC_SRC_ALPHA)`; the compositor's premultiplied blend gives the DIB
the right alpha. The software path clears alpha targets to transparent and presents the same way.
`dlls/win32u/dce.c` `update_visible_region()`: an empty visible region for DCs on a top-level window
with `WS_EX_NOREDIRECTIONBITMAP` (opt-out `WINE_GDI_NOREDIRECTIONBITMAP=0`). The main window is not
affected (its ex-style is 0; only its DesktopChildSiteBridge child carries the flag).

**Verified headless** (`survey/nf/combo8.log`, `combo9.log`): the popup's own X window (depth 32,
mapped) holds the composed flyout with transparent surroundings, rounded corners and shadow
(`survey/nf/scenario-xwin-e00013.png`); clicking "FFmpeg™" inside it selects the item and closes the
popup (`combo9-G5.png`). **The Xvfb root screenshot still shows a black box** — an X server without
a compositing manager cannot display a depth-32 window's alpha (the root `GetImage` of a
different-depth inferior is undefined). The user runs KDE on Wayland (`kwin_wayland` + Xwayland),
which composites ARGB windows, so the display test is the real verification. On a bare X11 session
without a compositor the popup would still be a black box; an XShape fallback (cut the window to
alpha > 0) could be added for that case. Also affected by the win32u change: dcomp's software present
path (`GetDCEx` + `BitBlt`, only with the GPU compositor disabled) would now be discarded for such
windows. 1.x regression `tools/xvfbrun.py 4`: 629 presents, 0 errors.

**Note on headless runs:** `betahl10-scenario.sh` runs the real Steam install
(`/mnt/Yucky/SteamLibrary/steamapps/common/Qwilight`); scripted clicks change settings in memory,
but the run is killed, so `Configure.json` (mtime 11:01:51, before these runs) was not rewritten.

## 2026-09-10 — Start-up crash lead: GC handle scan faults, special user APCs (session 8b, OPEN)

The user's "sometimes the layout is messed up, then it crashes" at start-up. One headless run in ~20
(`survey/nf/combo4.log`) died ~22 s in with `Internal CLR error (0x80131506)` /
`EXCEPTION_ACCESS_VIOLATION` at `coreclr+0x13e443` on a thread-pool thread inside
`SqliteConnection.Open()` (managed stack: `Qwilight.DB.GetHandled` ← `BaseNoteFile.SetData` ←
`MainViewModel.LoadEntryItem`, i.e. the parallel chart-folder load). Resolved with the Microsoft
symbol server PDB (`scratchpad/coreclr.pdb`, `coreclr-syms.txt`, llvm-pdbutil): the faulting
function is **`TableScanHandles`** (GC handle-table scan) reading `0x60ee0783` — the GC walked a
handle table while it was being changed, i.e. a thread the GC believed stopped was still running, or
the GC ran on a thread that had not really reached a safe point.

Suspicion: CoreCLR (.NET 8+) probes `QueueUserAPC2(…, QUEUE_USER_APC_FLAGS_SPECIAL_USER_APC |
QUEUE_USER_APC_CALLBACK_DATA_CONTEXT)` at start-up (`Thread::InitializeSpecialUserModeApc`) and,
when it succeeds, suspends threads for GC by injecting an activation APC that must interrupt the
thread *wherever it is in user mode* and hand the callback the interrupted `CONTEXT`. Wine's server
queues it as an ordinary user APC (only the WoW64 check looks at `SERVER_USER_APC_SPECIAL`;
`call_user_apc_dispatcher` prints `flags 0x3 are not supported` ~1800 times per run) and delivers it
at the thread's next alertable wait with the syscall-frame context. The activation therefore never
interrupts running managed code; the CLR waits for GC polls instead, and a late APC fires with a
context the CLR did not expect. Not proven to be the cause.

**Toggle to test:** `WINE_NO_SPECIAL_APC=1` (TEMP DIAG, `dlls/ntdll/unix/thread.c`
`NtQueueApcThreadEx2`) makes the special-flag call fail with `STATUS_NOT_SUPPORTED`; the CLR then
falls back to `SuspendThread`/`GetThreadContext` redirection (what every .NET app used before
Windows 10 20H1). Verified headless (`survey/nf/noapc1.log`): the game starts, the settings dialog
and the ComboBox work, no `flags 0x3` lines. If the user's start-up crashes stop with it in the
launcher environment, the fix is either to refuse the special flag in Wine or to implement real
special APCs (signal the thread, deliver with the interrupted context). The other crash signatures on
record (`survey/beta9.log`, `impl-system-power-run*.log`: `0xC0000005` in
`Application.LoadComponent` during `MainWindow.InitializeComponent`) are a different, earlier failure.

## 2026-09-10 — Freeze when opening the Difficulty Table dialog (and earlier Game Settings): FLS lock deadlock (session 8b)

**Symptom.** The user's game froze while opening the Difficulty Table Settings dialog (an earlier
freeze on opening settings looked the same). Process alive, 136 threads, render thread silent,
compositor reporting 4 frames/2 s.

**Evidence** (`steam-run.log`, 2.2 GB, `QWILIGHT_WINEDEBUG=warn+module,+timestamp,+seh`): the XAML
render thread (016c) stops at 10396 s; from 10465 s 27 threads print
`RtlpWaitForCriticalSection … "ntdll/thread.c: fls_section" wait timed out … blocked by 01e0`
(01e0 is a .NET thread-pool thread), then `loader_section … blocked by 0434` (a thread holding the
loader lock while waiting for the FLS lock). No exception, no other activity. The
`d2d_device_unknown_method3` lines that fill the tail run every 2 ms for the whole session and are
unrelated; the `MicrosoftEdgeUpdate.exe /c` processes are the WebView2 runtime's self-update checks
(the prefix has EdgeWebView 152 installed for `EdgePanel`) and happened earlier without freezing.

**Cause.** `RtlProcessFlsData()` (thread exit) and `RtlFlsFree()` ran the FLS callbacks while
holding the global `fls_section`. CoreCLR's FLS callback is its thread-detach hook, which can wait
for a GC in progress; when the thread pool trims many idle threads at once, one callback blocks and
every other exiting thread queues on the lock, the runtime can never finish what the first callback
waits for, and the process is dead. Windows does not serialise FLS callbacks this way. Upstream
Wine 11.16 has the same code, so this is an upstream bug too.

**Fix.** `dlls/ntdll/thread.c`: `RtlProcessFlsData(flags & 1)` unlinks the thread's `TEB_FLS_DATA`
under the lock, releases it, then runs the callbacks (the callback chunk tables are never freed;
once unlinked nothing else touches this thread's slots). `RtlFlsFree()` collects the per-thread
values under the lock, clears them, releases the lock and calls back afterwards (falls back to the
old in-lock call if the temporary array cannot be allocated). Verified: `tools/xvfbrun.py 4` 599
presents / 0 errors; beta headless `survey/nf/fls1.log` (settings + ComboBox scenario) clean. The
deadlock itself was not reproduced headless; the user retests on the display. Without a debugger
(ptrace_scope 1) the blocked callback's stack could not be captured; if it recurs with this fix,
the next step is `QWILIGHT_WINEDEBUG=…,+thread` and `winedbg --auto`-style stacks.

## 2026-09-10 — Gameplay rendering freezes on the first note (beta) and never recovers: missing D2D ChromaKey effect (session 8b)

**Symptom.** In play the Win2D scene stops the moment a note is hit; XAML dialogs still render on
top. Same shape as the v1 "freeze after every judged note" (session 5) but permanent.

**Evidence** (`steam-run.log` with `+seh`): the Win2D render thread (03c4) goes from
`create_device_context → GetDesc1 → Present1` at ~600 frames/s to `create_device_context →
GetDesc1 → C++ exception (e06d7363, thrown in Microsoft.Graphics.Canvas.dll, caught by Win2D's
exception boundary) → no present`, 3000 iterations/s from 11042.481 s on. No managed exception is
visible (.NET 10 no longer raises SEH for managed throws), no d2d diagnostics (the run had no
`warn+d2d`). Win2D's own `DrawImage` path only needs ColorMatrix and Border (both present), so the
persistent failure had to be an image the game keeps drawing every frame. `DrawingSystem.LoadBMS`
wraps every BMS BGA bitmap in a Win2D `ChromaKeyEffect { Color = Black, Tolerance = 1/512 }`; Wine's
d2d1 had no `CLSID_D2D1ChromaKey`, so `CreateEffect` failed the first time a BGA image was drawn
(which the user perceives as "when a note is hit"), Win2D threw, the game's loop caught the
exception (`DrawingSystem.cs:3200`, Sentry) and skipped the present — for every frame, forever,
because the BGA image stays in the scene.

**Fix.** `include/d2d1effects_2.idl`: `CLSID_D2D1ChromaKey` and `D2D1_CHROMAKEY_PROP`.
`dlls/d2d1/effect.c`: the ChromaKey effect (Color vector3, Tolerance float, InvertAlpha bool,
Feather bool; defaults black / 0.1). Wine renders effect chains by drawing the source bitmap
(`d2d_effect_get_source_bitmap`), so a registration alone would draw the BGA layers with opaque black
backgrounds; instead the key is applied while sampling: `d2d_effect_get_source_bitmap()` now also
returns a `struct d2d_chroma_key` for a ChromaKey effect in the chain, `d2d_device_context_draw_bitmap`
stores it on the bitmap brush (`d2d_brush.u.bitmap.chroma`), `d2d_brush_fill_cb` passes it in the
brush constant buffer (`chroma_flags` in the former padding, key colour + tolerance in `data[2]`),
and the runtime-compiled pixel shader's `brush_bitmap()` zeroes pixels whose straight (unpremultiplied)
colour is within the tolerance of the key (per-channel max distance; `Feather` ramps the alpha,
`InvertAlpha` inverts). Verified with `scratchpad/chroma.c` (mingw, Wine headers) on Xvfb :7:
tolerance 1/512 keys pure black and keeps (1,1,1); tolerance 0.01 keys both. `tools/xvfbrun.py 4`:
625 presents / 0 errors (the shader compiles); beta headless settings scenario clean
(`survey/nf/chroma1.log`). Not yet verified on the display in play.

## 2026-09-10 — Stutter in play, second settings freeze, compositor pacing (session 8b, IN PROGRESS)

**Measurements from the display log** (500 Hz panel, 2560x1440): the game (Win2D thread 03c4) presents
~330/s with intervals 1.7–9 ms (`game present stats` TEMP DIAG in dxgi); the dcomp compositor composed
only ~150–180 of them per second (`composite stats … interval avg 6 ms`) and presented them with sync
interval 0, so the display saw an uneven subset of frames. Headless timers (llvmpipe) show
`do_composite` at ~11 ms per frame, i.e. the CPU-side visual walk + per-layer draw setup is the
compositor's cost, not GPU work.

**Changes.** `dlls/dcomp/gpu.c`: the window swapchain is now presented with sync interval 1
(`WINE_DCOMP_SYNC_INTERVAL=0` restores the old immediate present), so composed frames reach the
display at the panel's cadence and each shows the newest game snapshot. `dlls/dcomp/device.c`: the
2 s `composite stats` line now has real per-frame timers: `wait` (sleep + frame event),
`compose` (`do_composite`), `gpu` (begin/end frame incl. present). Not yet done: cache the shader
resource views the compositor creates per layer per frame (`dcomp_gpu_draw`), and honour
`D2D1_COMPOSITE_MODE_PLUS` in `d2d_device_context_DrawImage` (the game's additive hit-note effect;
currently drawn source-over, "Unhandled composite mode 0x9").

**Second freeze (opening settings after a skin change), not yet understood.** Log signature: the
main thread (0024) stops logging, spins at 100 % CPU and holds an unnamed critical section
(`0x382410 "?"`); two new threads time out on it; XAML's render thread keeps polling
`d2d_device_unknown_method3` (see below) but commits ~5 frames/s instead of ~120; the game's Win2D
thread stops presenting. A second thread created around the freeze also spins at 100 %. No
FLS-lock timeouts (that fix holds). The process was closed before stacks could be taken.
TEMP DIAG added for the next occurrence: `dlls/ntdll/sync.c` prints, on every critical-section
timeout (first 40), the owner thread's `rip` as module+RVA and the waiting thread's own stack as
module+RVA (modules resolved by walking the PEB list without the loader lock). Symbols for
`dwmcorei`, `dcompi`, `Microsoft.WindowsAppRuntime`, `Microsoft.Internal.FrameworkUdk`,
`Microsoft.UI.Windowing.Core` and `coreclr` are in the scratchpad (`*.pdb`, from the Microsoft
symbol server); `Microsoft.ui.xaml.pdb` is not published (404).

**`d2d_device_unknown_method3` identified.** The private `ID2D1Device` method XAML calls ~400 times/s
with `(2000, <bytes>, p3, p4)` is called from `dwmcorei!CDeviceManager::ClearD2DCaches` — a cache
trim ("resources unused for 2000 ms"), harmless as a stub. Caller RVAs are printed once per call
site by a TEMP DIAG in the stub.

## 2026-09-10 — Doubled/torn scrolling notes and half the frames dropped: compositor wake-up (session 8b)

**Symptoms.** With the vsync'd compositor a scrolling note showed as a "blur" (two offset copies);
`composite stats` showed ~314 composed frames per 679 game presents with a ragged 6 ms cadence.

**Causes.** (1) `__wine_dxgi_frame_event` was an auto-reset event shared by every dcomp composite
thread in the process (one per dcomp device); each game present woke one of them at random, so
the thread owning the window target got about half of the frames and composed the others a
refresh period late. (2) dxgi set the event right after queueing `CopyResource` into the shared
snapshot, i.e. before the game's command-stream thread had executed the copy or fenced it. The
compositor then bound the snapshot with an unchanged shared epoch, waited on nothing, and its GL
reads ran against a copy that had not executed or was half done. (3) With a single snapshot the
game could also overwrite it while the compositor's draw was still in flight (only the fenced,
already flushed draws are ordered).

**Fixes.** `dlls/dxgi/swapchain.c`: a ring of three snapshots, each present copies into the next
and publishes it through `GUID_wine_dxgi_presented_surface`; the event is manual-reset and is set
by `dxgi_wake_compositor()` after `wined3d_swapchain_present(NO_DRAW)` and a D3D11 `Flush()` —
with shared resources in use that Flush waits for the command-stream thread (`wined3d_cs_finish`),
so the copy and the present's `signal_shared_resources` fence are in the GL stream before the
compositor wakes and its `wined3d_texture_gl_wait_shared` orders the reads. `dlls/dcomp/device.c`:
the composite thread that owns a window target resets the event before composing (a present during
the composite sets it again); threads without one sleep 1 ms and leave it. `wait` in the stats is
only accumulated by the owning thread. Verified headless only (1.x 750 presents / 0 errors, beta
scenario clean); display test pending.

## 2026-09-10 — Settings/skin-change freeze: CoreCLR GC suspension never completes (special user APCs) (session 8b)

**Diagnosed with the new critical-section timeout diag** (owner rip + waiter stacks, resolved with
`coreclr.pdb`): the UI thread (0024) spins in `minipal_microdelay` inside
`ThreadSuspend::SuspendRuntime` holding the ThreadStore lock; new threads block in
`SetupThread → ThreadStore::AddThread → ThreadSuspend::LockThreadStore → CrstBase::Enter`
(`JIT_ReversePInvokeEnterRare`, i.e. native callbacks into managed code). CoreCLR stops threads
for GC with `QueueUserAPC2(SPECIAL_USER_APC | CALLBACK_DATA_CONTEXT)`; Wine accepted the flags but
delivers the APC only at the next alertable wait, so a thread busy in managed code never reports in
and the suspension spins forever. Same mechanism as the start-up `TableScanHandles` crash lead.

**Fix (HACK).** `dlls/ntdll/unix/thread.c` `NtQueueApcThreadEx2` refuses the special flag with
`STATUS_NOT_SUPPORTED` by default (`WINE_SPECIAL_APC=1` re-enables); CoreCLR's start-up probe then
fails and it uses SuspendThread/GetThreadContext redirection. The real fix is delivering special
APCs through a signal with the interrupted context (server + `call_user_apc_dispatcher`).
Headless: beta scenario clean, no `call_user_apc_dispatcher flags 0x3` lines; 1.x regression 0 errors.

**Also found:** a killed instance leaves `yucky/Qwilight.#`; the next launch then exits silently
(status 0) about ten seconds in — looks like a crash but is the game's single-instance check.
`Initializing D3D12 … D3D12CreateDevice failed 0x80070057 … exited with status 1` in the log is
Steam's `d3ddriverquery64.exe -d3d12` run through the compat tool, not the game.

## 2026-09-10 — Song-select responsiveness: disk, case-insensitive lookups (session 8b, measuring)

Install, songs (`E:\Rhythm Games` → `/mnt/Yucky`), saves and the SQLite DB are on the 18 TB
spinning disk (`sdc`, ext4 without casefold). Wine resolves each open case-insensitively: exact
name first, else a full `readdir` of the directory (`find_file_in_dir`). `dlls/ntdll/unix/file.c`
now tries the all-lower/all-upper spellings before scanning, and `WINE_FILE_DIAG=1` (TEMP DIAG, set
by the launcher) prints "case-insensitive directory scans: N in the last S s, E entries read, T ms"
every 5 s. Headless (warm cache): 57 scans / 15k entries / 3.8 ms in 35 s of menus; the largest
directories are the difficulty-table folders (~5.8k entries per scan). The user's next display log
tells whether scans matter on the cold disk. The game itself decodes full-size banner images for
song thumbnails (no cached thumbnails) and its UI blocks on that work without feedback — game side.

**Audio streams.** Each PipeWire "ALSA [wine-preloader]" stream is one Windows audio client: FMOD
plus one Media Foundation audio renderer per WinRT `MediaPlayer` the game creates (skin background
videos, BGA, previews; 12 `set_source_from_stream` opens in the first 15 s of a session). They
disappear with the process; expected behaviour, not a leak.

**Skin settings, second pass.** The beta keys per-skin blocks as `<UIEntry>\<YamlName>.yaml`
(`.\QRcircle.yaml`, `CR_Mania\CR_Mania.yaml`); the earlier port copied v1's bare-name keys, which
the beta never reads. `scratchpad/port_skins.py` mapped v1's QRcircle (positions), CR_simple(_type_1/2),
CR_Mania, CR_natural, QRCircle V2 and root Default into the beta keys, keeping beta entries the user
had already changed (`@CR_advanced`); backup `Configure.json.bak-skins-20260910-132215`.

## 2026-09-10 — Mouse wheel does not scroll WinUI ScrollViewers (session 8b, implemented, unverified)

**Confirmed against the v2 source** (see below): the settings/table dialogs use plain WinUI
`ScrollViewer`s (`StandardMoreView` derives from `DefaultScrollViewerStyle`, no custom input), so
the wheel has to work through WinUI's own machinery. The game's only wheel handler
(`MainView.OnD2DPointSpin`) acts on the D2D canvas and only while the pause window is open, so it is
not involved in the dialogs.

**Implemented.** `dlls/ninput/main.c` was a stub that generated no output. It now keeps the
registered output callback, buffers `BufferPointerPacketsInteractionContext` packets, and turns a
`POINTER_FLAG_WHEEL`/`HWHEEL` packet into a manipulation output sequence (BEGIN, delta, END) in
both `ProcessBufferedPacketsInteractionContext` and `ProcessPointerFramesInteractionContext`.
Translation per notch = `SPI_GETWHEELSCROLLLINES` x the context's `CHAR_TRANSLATION_X/Y` parameter
(40 px default), respecting the context's configured translation axes and rails; horizontal wheel
drives X, and a vertical wheel drives X when only `TRANSLATION_X` is enabled. `Makefile.in` now
imports user32; `INTERACTION_CONTEXT_PROPERTY_MEASUREMENT_UNITS` is stored instead of returning
E_NOTIMPL.

**Not verified.** Headless the synthetic XTest wheel reaches ninput only as hover packets
(flags 0x2/0x6 = INRANGE/INCONTACT), never with wheel flags, and the user's display log has **zero**
wheel packets in any ninput call. So XAML is feeding ninput pointer packets but the wheel is not
among them. win32u already converts `WM_MOUSEWHEEL` to `WM_POINTERWHEEL` and forwards it to the
input site (`dlls/win32u/message.c`, "Wheels are not sink input"), so the next step is to trace with
`+sinkinput,+ninput` whether the site receives `WM_POINTERWHEEL` and what `GetPointerInfo()` then
reports for it. The ninput change is a prerequisite either way.

## 2026-09-10 — The v2 source is public (session 8b)

`https://vcs.taehui.net/root/qwilight` (cloned to `scratchpad/qwilight-v2`, HEAD 460aede 2026-09-08)
is **exactly the beta under test**: `Qwilight.csproj` says `<Version>1.17.13</Version>`,
`Microsoft.WindowsAppSDK 2.4.0`, `net10.0-windows10.0.26100.0`, self-contained — the same version the
settings dialog shows. `~/Documents/Qwilight` is the older 1.17.2 tree. Read this one for every
future "why does the game do X" question (input, media, drawing, DB, thumbnails).

## 2026-09-10 — Mouse wheel in WinUI ScrollViewers: full chain traced, blocker found (session 8b, later)

**Chain (all verified with relay traces, symbols from the Microsoft symbol server for
`Microsoft.DirectManipulation`, `Microsoft.InputStateManager`, `Microsoft.UI.Input`):**
X wheel → `WM_MOUSEWHEEL` → win32u `WM_POINTERWHEEL` → sink → input site → XAML raises the wheel
event → XAML hands it to the lifted **DirectManipulation** (`Microsoft.DirectManipulation.dll`,
app-local) → DManip `ProcessInput` → for each interaction primitive it creates an ninput
**InteractionContext** (5 per wheel: touch/pen/mouse/touchpad/mousewheel; the first four get a
no-op callback `s_InteractionDisabledCallback`, the mousewheel one the real
`s_InteractionContextCallback`), sets `MEASUREMENT_UNITS=HIMETRIC` and the mouse-wheel parameters
(`CHAR_TRANSLATION_Y` 1584.8, `PAGE_TRANSLATION_Y` 31696 in HiMetric), feeds
`ProcessPointerFramesInteractionContext` with the wheel packet (flags `0x482002`, `InputData` ±120n)
→ ninput must emit a MANIPULATION with inertia → DManip calls ordinal **2505**
`(ctx, 1000, now_ms, prev_ms)` (output prediction; success = "a manipulation is running"), sets
inertia decelerations to FLT_MAX, calls ordinal **2500** `GetInertiaEndInteractionContext(ctx,
MANIPULATION_TRANSFORM *end, 0, 0)` (a 20-byte buffer; writing a full
`INTERACTION_CONTEXT_OUTPUT` there tripped its stack cookie and killed the process), checks
`GetStateInteractionContext`, then registers XAML's frame handle with
`CUpdateManagerImpl::RegisterWaitHandleCallback` and signals its delegate thread
(`CManagerImpl::s_ThreadProc` → `_RunDelegateThread`, a `MsgWaitForMultipleObjects` loop). That
thread wakes every frame and tries to build the scroll animation — and fails every time:
`err:ole:com_get_class_object class {d25d8842-8884-4a4a-b321-091314379bdd} not registered`.

**Blocker:** `CLSID_UIAnimationManager2` — the Windows Animation Manager **v2**. DirectManipulation
animates content through WAM2 (`IUIAnimationManager2`, `IUIAnimationTransitionLibrary2`,
`IUIAnimationVariableChangeHandler2`, `IUIAnimationStoryboardEventHandler2`; its `CorePal::
CreateTransitionFromBezier` / `ConfigureDirectManipulationCurve` build cubic-bezier transitions).
Wine's `dlls/uianimation` registers only the v1 classes (`UIAnimationManager`, `Timer`,
`TransitionFactory`, `TransitionLibrary`) and every v1 method is a stub (76 FIXMEs); the v2
interfaces are not even in `include/uianimation.idl` (mingw's `uianimation.h` has them). So no
WinUI 3 ScrollViewer can scroll by wheel (or by touchpad/inertia) until WAM2 exists.

**Implemented on the way (`dlls/ninput/main.c`, all real):** output callback storage; buffered
packets (`BufferPointerPackets`/`ProcessBufferedPackets`); wheel → manipulation output (BEGIN, then
the whole translation flagged INERTIA, `in_interaction` until `ProcessInertia*`/stop); HiMetric
vs screen output coordinates; `GetStateInteractionContext` from real state; ordinals
2500 `GetInertiaEndInteractionContext(ctx, MANIPULATION_TRANSFORM*, UINT, UINT)`,
2501 `ProcessInertiaWithTimeInteractionContext(ctx, time)`, 2502 `StopWithTimeInteractionContext(ctx,
time)`, 2505 `OutputPredictionInteractionContext(ctx, ms_ahead, now, prev)` (delivers through the
callback, returns S_OK while a manipulation is active); `MEASUREMENT_UNITS` stored. Also
`dlls/dcomp` `GetFrameStatistics` implemented (not used by this path after all).

**Tooling notes.** The prefix's `HKCU\Software\Wine\Debug\RelayExclude` (set in an earlier
session) hides `user32.MsgWaitForMultipleObjects`, `PeekMessageW`, `GetKeyState`… — `RelayInclude`
does not override it. Restricted relay runs (`RelayInclude` list) keep the log small enough for the
settings dialog to open; full relay runs do not. `scratchpad/xscenario.py` now retries the settings
click and detects the dialog by its red close button; the settings gear moved to (5.8 %, 3.8 %)
with the ported skin (`AX`/`AY`). `CPUSAMPLE=1` prints the busiest threads.

**Next:** implement WAM2 in `dlls/uianimation` (manager2/variable2/storyboard2/transition2/
transition library2 with a real scheduler: instantaneous, linear, cubic-bezier, smooth-stop,
parabolic transitions; `Update(time)` driven by DManip's delegate thread; `GetCurve` may be needed
to hand curves to dcompi) — a few hundred lines, then the wheel should scroll.

## 2026-09-10 — Mouse wheel scrolls WinUI ScrollViewers (session 8c, headless-verified)

**Result.** The wheel scrolls the settings dialog headless (`survey/nf/wheel15.log` onwards:
`WHEEL=-1 SCENARIO=graphics scratchpad/betahl10-scenario.sh …`, judged by `scenario-G3-bottom.png`
vs `scenario-G7-wheel10s.png`). Three separate problems, none of them the one the previous
writeup predicted:

1. **Windows Animation Manager v2 was not the blocker after all — but it is implemented now.**
   `dlls/uianimation/wam2.c` (new, ~1100 lines, real): `IUIAnimationManager2` (variables,
   storyboards, `Update(time)` scheduler, tag lookup, `EstimateNextEventTime`, status/handler
   events), `IUIAnimationVariable2` (bounds, rounding, change/integer-change handlers, previous
   /final values; `GetCurve` is a FIXME — no compositor curves), `IUIAnimationStoryboard2`
   (transitions in sequence per variable, keyframes at offsets / after transitions, transitions
   between keyframes, Schedule/Conclude/Finish/Abandon, truncation of storyboards that share a
   variable), `IUIAnimationTransition2`, the whole `IUIAnimationTransitionLibrary2` (instantaneous,
   constant, discrete, linear, linear-from-speed, sinusoidal ×2, accelerate-decelerate, reversal,
   cubic (Hermite), smooth-stop, parabolic, cubic-bezier-linear (Newton + bisection), vector
   variants) and `IUIAnimationTransitionFactory2` (custom `IUIAnimationInterpolator2`). The v2
   interfaces, `UI_ANIMATION_REPEAT_MODE` and the three coclasses were added to
   `include/uianimation.idl` (which now imports `dcompanimation.idl`) and `uianimation_reg.idl`;
   `main.c` dispatches the CLSIDs. Verified with `scratchpad/wam2test.c` (bezier ease-out values,
   smooth-stop duration 2·d/v = 1.8 s, truncation, manager idle). **Registration:** an existing
   prefix needs `wine regsvr32 uianimation.dll` once (done for `qwpfx-dev`).
   DirectManipulation does create `CLSID_UIAnimationManager2` on its delegate thread
   (`CUpdateManagerImpl::Update`) but only *uses* it for touch/touchpad inertia
   (`CInertiaImpl::_Initialize` → `CreateAnimation` with the transition library), which the mouse
   wheel never enters — see 3.

2. **Every X wheel click counted twice.** `dlls/win32u/message.c` forwarded `WM_POINTERWHEEL` to
   the input site through the sink queue and then still delivered the legacy `WM_MOUSEWHEEL` to
   the window; WinUI's window procedure handles both, so `InputStateManager` accumulated 240 per
   notch (`+sinkinput` shows one `0x20a` with wParam `0x780000` = 120, ninput saw 240). On Windows
   the legacy message only exists when `DefWindowProc` sees the pointer message unhandled, so the
   wheel now ends in `process_mouse_message` after the pointer message is queued
   (`accept_hardware_message` + return FALSE). ninput now sees 120 per click.

3. **DirectManipulation ignores INERTIA-flagged wheel output from a mouse.** Disassembly of
   `CViewportImpl::ProcessInput` (`scratchpad/dmanip.dis`, symbols from
   `Microsoft.DirectManipulation.pdb` via `rva2sym.py`/`sym2rva.py`): `_ReportInertiaStart` is
   only called when the input event's pointer type is touch (2) or touchpad (5) and a velocity of
   an enabled motion is non-zero; for a mouse (4) the viewport goes RUNNING and applies plain
   manipulation deltas. Also `OutputPredictionInteractionContext` (ordinal 2505) has **six**
   arguments: `(ctx, ms, now, previous, unused, MANIPULATION_TRANSFORM *predicted)` —
   `_CalculatePrediction` passes 1000 ms, three timestamps and a zeroed 20-byte transform and reads
   the prediction back (a failing HRESULT = no prediction). `dlls/ninput/main.c` now emits a wheel
   notch as one complete manipulation — BEGIN, one update with the whole translation (velocity
   filled in), END — in the `ProcessPointerFrames` call, and 2505 fills the prediction with the
   cumulative transform. `WINE_WHEEL_MODE` variants (BEGIN then INERTIA; BEGIN|INERTIA) were tried
   and scroll nothing; the classic sequence scrolls.

**Amount, unexplained factor of 5.** Per 120-notch ninput emits 3 lines × the `CHAR_TRANSLATION_Y`
parameter XAML set (1584.8 HiMetric = 1/20 of the 31696 HiMetric page = 60 px at 96 dpi), i.e.
4754.4 HiMetric ≈ 180 px. Wine's `NtUserGetPointerDeviceRects` returns device (0,0)-(67733,38100),
display (0,0)-(2560,1440) (TEMP DIAG FIXME), which DManip's `TransformCoordinateSpace` uses
exactly as expected (× display/device). Yet the content moves **36 px** per notch, and the amount
is linear in the emitted translation (`WINE_WHEEL_SCALE=2` → 72 px, `=5` → 179 px). The extra
÷5 sits between DManip's changeset and the screen (`_TransformInteractionOutput` multiplies by
the `Matrix3x2F` XAML gives `UpdateTransform`, i.e. the viewport transform; the game's whole UI
sits in a root `<Viewbox>`); not resolved. Velocity does not affect the amount
(`WINE_WHEEL_VELOCITY_MS`). **On the display:** if a notch feels 5× too small, set
`WINE_WHEEL_SCALE=5` in the launcher and report; the right fix is to find where the 1/5 comes from.
Headless the movement lands late (llvmpipe frames): judge by the `-70.png`/`G7` shots.

**Also seen:** the first wheel over the dialog reports an unhandled `E_NOTIMPL` through
`RoReportUnhandledError` from XAML's managed event dispatch (`CCoreServices::CLR_FireEvent`,
symbolised with the Microsoft.UI.Xaml PDB now in the scratchpad): the game's XAML value
converters (`Qwilight/Modifier/*Modifier.cs`) throw `NotImplementedException` in `ConvertBack`.
Game-side, swallowed by XAML, harmless here.

**TEMP DIAG added:** `WINE_WHEEL_SCALE`, `WINE_WHEEL_VELOCITY_MS` (ninput), the pointer-rect FIXME
in `NtUserGetPointerDeviceRects`, the wParam in the `+sinkinput` trace. `scratchpad/xscenario.py`:
`WHEEL=-n` scrolls up, `G7-wheel10s` shot.

## 2026-09-10 — "The game rebuilds its database every launch" (session 8c, diagnosis)

Not Wine, mostly. The beta's chart cache is `yucky/DB.json` (`FastDB`): per-folder note-file
lists keyed by Windows path, per-chart metadata keyed by SHA-512 of the file. The library on
`E:\Rhythm Games\…\Difficulty Tables!` (→ `/mnt/Yucky`, 11,680 folders, **121,290 charts**) is
loaded every launch (`MainViewModel` walks all folders); folders missing from the cache are parsed
and hashed from disk, which on the HDD takes most of a session. The cache is saved **only on a
clean exit** (`MainViewModel` close path → `FastDB.Save()`), so every killed/crashed/headless
session loses that session's progress. After the 16:09 exit the cache held 1,407 folders; the
17:22 exit saved 9,175. The v1 install's cache (`/mnt/Yucky/Old/SteamLibrary/…/SavesDir/DB.json`,
193 MB, 124,632 charts, same structs, same E:\ paths, version field not checked on load) covers
everything; `scratchpad/DB.json.merged` = v1 cache + the beta's entries, validated against the
disk (0 of 300 sampled folders missing). Installing it into `yucky/DB.json` was left to the user
(auto-mode refused the write). Headless runs use the same directory: they never save, but a clean
exit of the user's session does, so they cannot corrupt the cache.

**Suspicious:** the 17:22 save (109 MB) had exactly one 16-byte run of NUL bytes at offset
12,998,630 (unaligned), replacing `,"highestBPM":18`; `FastDB.Load` swallows the parse error, so
that file alone would have meant an empty cache and a full rebuild on the next launch (backup:
`yucky/DB.json.corrupt-20260910-1722`). `File.WriteAllText` → Wine `NtWriteFile` (single `pwrite`
per call, short writes reported as success with the count; .NET loops on those). 16 bytes = one
XMM store: a lost vector register across a thread suspension (CoreCLR's SuspendThread hijack
now that special APCs are refused, `NtGet/SetContextThread` XMM state) is the lead to check if
it recurs — grep new saves for `\x00\x00\x00\x00`.

## 2026-09-10 — Slow display start / frozen XAML tree / no scrolling while loading: thread priorities (session 8c, diagnosis)

**Measured.** Display launches with logging: window mapped at +32 s, first frame at +41 s; headless
+13 s / +22 s. The user's no-logging launches take ~86 s to the first frame; the wineserver never
exceeds 18 % of a core (`scratchpad/startup-sampler.sh`), so in-process sync is not the lever
(re-enabling it still hangs the boot, see 2026-09-03). The commit-thread watchdog
(`dcomp_commit_watchdog`, TEMP DIAG) showed dwmcorei's render thread looping in
`CScheduler::WaitForNextFrameStart` → `DCompositionWaitForCompositorClock(0, -, 80 ms)` for 35 s
while the library load ran on every core, and the first frame only appeared when the load
finished. The frame statistics were ruled out by `WINE_DCOMP_NO_FRAME_STATS=1` (no change).

**Cause.** The game's cache load runs `Parallel.ForEachAsync` over 11,680 folders — 350–400
runnable threads at normal priority — and every thread in the process shares the same Linux
priority: `ulimit -H -e` is 0 on this machine, so `server/thread.c` cannot use `setpriority`
("RLIMIT_NICE is <= 20") and `SetThreadPriority` is a no-op. dwmcorei, Microsoft.UI.Xaml and
CoreMessagingXP all raise their render/dispatcher threads; on Windows those and the foreground UI
thread win the CPU, on Wine here they get 1/25 of a core. That is why the first frame, the popup
host, the XAML commits and the wheel all wait for the load to end, and why anything that slows
the loader (logging on, HDD contention) makes the UI *faster*.

**Remedy.** Allow negative niceness for the user (`/etc/security/limits.d/`: `<user> - nice -11`,
re-login), after which Wine maps NT priorities 1–15 to nice [-11, 11] and thread-pool workers stay
at 0. dcomp's own compositor thread now asks for `THREAD_PRIORITY_HIGHEST`. Must go in the
community setup instructions. Not yet verified on the display.

## 2026-09-10 — Profile flyout never appears and freezes the XAML tree (session 8c, FIXED, display test pending)

**Symptom.** Clicking the avatar in song select showed no menu; from then on nothing structural in
XAML ever rendered again (settings dialog audible but invisible, list frozen) while D2D content
kept updating. Alt+F4 still worked.

**Root cause (dwrite).** The flyout is a windowed popup whose `MenuFlyoutItem` text is laid out with
font axes. XAML's `CCompositeFontFamily::MapCharacters` calls
`IDWriteFontFallback1::MapCharacters(source, 0, 12, collection, L"Century Gothic", axes[5], 5, …)`
for the label "View profile"; Wine's `dlls/dwrite/analyzer.c` stubbed that method with E_NOTIMPL,
XAML turned it into E_FAIL inside `CXcpDispatcher::Tick` → `CXcpBrowserHost::OnTick` →
`CWindowRenderTarget::Draw` → `NWDrawTree` → text layout, reported it through
`RoReportUnhandledError` ("Call failed."), withdrew the popup host window and, since the same
layout fails on every tick, never committed a frame again. Implemented: axis values map to
weight/stretch/style (`wght`/`wdth`/`ital`/`slnt`), the classic `MapCharacters` does the lookup,
and the result is returned as `IDWriteFontFace5`. `scratchpad/fallback1.c` (mingw) verifies it:
S_OK, 12 characters mapped, a face returned, for an existing, a variable and a missing family.

**How it was found (the method that finally worked).** Not tracing: relay cannot see COM calls,
+dcomp/+composition showed only successful calls, and the A/B on frame statistics was noise.
Two generic, env-gated hooks did it in one run each: `WINE_RO_DUMP_HRESULT=<hex>` (combase prints
the stack at `RoOriginateError` for that HRESULT; here it only showed the report site) and
`WINE_DUMP_BACKTRACE=<module>` (ntdll prints every `RtlCaptureStackBackTrace` whose caller is in
that module — XAML captures one at every failed HRESULT check via `OnFailure<N>` →
`OnFailureEncountered` → `CaptureErrorContext`, so the first capture after the popup host was the
origin). Frames resolved with `llvm-pdbutil dump --symbols` (private procs, 174k, needed for the
cold blocks) — see `scratchpad/hrchase.py` / `rva2sym.py` / `sym2rva.py`. The earlier
"starvation" theory explains the slow start and load-time stalls, not this bug.

**TEMP DIAG added:** `WINE_RO_DUMP_HRESULT` (dlls/combase/roapi.c), `WINE_DUMP_BACKTRACE`
(dlls/ntdll/exception.c), the compositor-clock and frame-statistics FIXMEs and
`WINE_DCOMP_NO_FRAME_STATS` (dlls/dcomp/device.c).

## 2026-09-10 — F7 downloader: WebView2 pages stay blank (session 8c, FIXED headless, display test pending)

**Symptom.** F7 (download songs) opens `VoteWindow` with four `EdgePanel`s, each a WinUI 3
`WebView2`; the header (back/reload/home/URL) renders, the page area stays empty. The same control
backs the help pages (`AssistWindow`) and the level-table votes.

**What happens.** The WebView2 runtime (`Program Files (x86)/Microsoft/EdgeWebView/…/152.0.4191.62`,
installed into the prefix in session 7) launches fine: `msedgewebview2.exe --embedded-browser-webview`
plus renderer, GPU, network and storage processes. WinUI 3 hosts WebView2 by *composition*, not by an
HWND: the host-side client (`EBWebView/x64/EmbeddedBrowserWebView.dll`, loaded into Qwilight.exe)
takes the XAML `ContainerVisual`, gets the underlying dcomp device (`IDCompositionDesktopDevicePartner`,
{d14b6158-…}, the same partner interface FrameworkUdk uses), and calls
`CreateSharedResource(IID_IDCompositionVisual)` + `OpenSharedResourceHandle(visual)` — a *shared
visual* whose handle goes to the browser process, which opens it with
`CreateFromSharedVisualHandle(handle, IID_IDCompositionTarget)` and sets its own tree as the root.
Our partner vtable had both as `E_NOTIMPL` stubs, so the client printed
`"WebView2_CreateSharedResource" failed: "Not implemented."` (visible with `warn+seh` — WebView2's
debug prints go through `OutputDebugString`) and gave up; nothing else in the log said so.

**Why it is not a one-liner.** On Windows every process's visual tree lives in DWM, so a visual
shared across processes is just a node. Our `dlls/dcomp` composites per process, and wined3d refuses
cross-process shared textures (`dlls/wined3d/shared.c`: needs EXT_external_objects). The frames have
to cross as system memory:

* `server/d3dkmt.c` composition object (the shared visual handle, already used in-process by
  dcompi's `ContentExternalOutputLink`) now also records a *producer* process and a *section*;
  `dcomp_set_shared_visual_info` from a foreign process stores them (new `section` field, generation
  counter), `dcomp_get_shared_visual_info` reports `owner`/`remote` and hands the caller a new handle
  to the section when its generation is behind.
* `dlls/dcomp/remote.c` (new): the browser side composes the target it made from the handle
  offscreen (`dcomp_remote_compose()` in device.c — GDI path whatever the window targets use,
  bounds from `dcomp_visual_tree_bounds()`) and publishes premultiplied BGRA frames into a
  three-buffer section (`dcomp_remote_publish()`); the host side maps it
  (`dcomp_shared_visual_resolve()`), uploads the newest frame into a texture (GPU path) or a DIB (GDI
  path) and draws it as a layer of the shared visual (`do_composite_remote_frame()`); the composite
  loop wakes on `dcomp_remote_frames_pending()`. Nested sharing (browser ← GPU process) works the
  same way, one level deeper.
* `CreateSharedResource` / `OpenSharedResourceHandle` implemented; `create_target_from_shared_visual_handle`
  learns from the server whether the handle is foreign (`target->remote`).
* The browser's next call after opening the target was `IDCompositionDevice::CreateScaleTransform`,
  another stub. All the property transforms (translate/scale/rotate/skew, 2D and 3D), transform
  groups, effect groups (opacity + 3D transform, now applied by `do_composite`) and
  `IDCompositionAnimation` (not run: a property jumps to the animation's end value) are implemented
  in `dlls/dcomp/transform.c` and wired into both device vtables.

**What else it took (each found by one instrumented run, in order).**

* The browser process then wanted `CreateSurfaceFromHwnd` for its own child window
  (`Chrome_WidgetWin_1`): Chromium's GPU process renders into an `Intermediate D3D Window` child
  of that, with its own dcomp target. Wine cannot show a child window of another process's
  window, so the window is only a name: `create_surface_from_hwnd()` registers a shared visual
  for the HWND (server composition object with an `hwnd`), `SetContent` of such a surface makes
  the visual a consumer of it, and any window target on that window or a descendant, in any
  process, links to it (`dcomp_target_link_hwnd()`, retried once a second) and becomes a
  producer through the same section transport. Three hops: GPU process → browser → game.
* `visual_AddVisual` refused a visual that already had a parent, and the tree held no reference
  on its children: dwmcorei re-parents with plain `AddVisual` and releases its own reference
  right after, so the WebView2 output-link visual was freed under the tree (its shared handle
  closed with it) and never composed. AddVisual now re-parents (refusing cycles) and the tree
  owns its children; RemoveVisual/RemoveAllVisuals/the destructor release them.
* With the pixels flowing, the page stayed `about:blank`: `WINE_RO_DUMP_HRESULT=all` (new
  "all" mode, no stack) showed E_NOTIMPL originated on the UI thread right after
  `iertutil:uri_Equals ... stub!`, once per WebView2 — WinUI's control compares the new `Source`
  with the current one (`ShouldNavigate`) before navigating and the projection threw.
  `Windows.Foundation.Uri.Equals` is implemented (urlmon `IUri::IsEqual`, string compare for
  foreign objects).
* The browser's transparent overlay popup (`WS_POPUP | WS_EX_LAYERED | WS_EX_NOREDIRECTIONBITMAP`,
  attributes set, never painted — on Windows DWM shows nothing for it) came up as a black X
  window over the page. winex11 now leaves a `WS_EX_NOREDIRECTIONBITMAP` window unmapped while
  it has no composition target in its process (the `wine_window_*_composed` props dcomp sets),
  and dcomp re-runs the window-pos check when it creates one (XAML's own popups keep working:
  graphics dialog scenario and `tools/xvfbrun.py 4` are clean).

**Verification.** Headless `SCENARIO=f7`: the BOF21 page renders inside the dialog at the
right size (the WebView's 1130x584 frame scaled 2x by XAML's transform) 20 s after F7. Input
(clicks, wheel, typing in the page) is untested; it goes through the XAML control's
`SendPointerInput`, not the hidden window. Display test pending.

**Diagnostics that worked here (keep in the playbook).** Chromium's own log: registry policy
`HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments`, value
`Qwilight.exe` = `--enable-logging --log-file=Z:\... --v=0` (the env var
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` is ignored when the app passes environment options;
`--enable-logging` also opens a Wine console window, delete the policy afterwards — deleted).
`--log-net-log=<file>` gives a NetLog; `--remote-debugging-port=9222 --remote-allow-origins=*`
lets `curl http://127.0.0.1:9222/json` list the pages and `scratchpad/cdpnav.py <url>` navigate
one — that is how the pixel path was proven before the navigation bug was found.
`scratchpad/listwin.exe` dumps every window with styles/parent/pid (run it in the prefix while
the game is up). `WINE_RO_DUMP_HRESULT=all` lists every originated WinRT error with its thread.

**Not done / known.** `IDCompositionDevice5::CreateDynamicTexture` and the d3d11
`CheckFormatSupport` partial stub make Chromium's GPU process log warnings and skip overlays,
harmless. DPI: the WebView renders at half resolution on a 2x-scaled XAML (Chromium takes the
monitor DPI, 96 on Xvfb); on the display it should match. The frame transport is a CPU copy
per frame (GDI read-back of the browser's swapchains + memcpy into the section + texture upload
in the game), fine for a web page.
