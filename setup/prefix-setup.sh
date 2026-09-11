#!/bin/bash
# Create the Wine prefix for Qwilight.
#
#   setup/prefix-setup.sh <wine-build dir> <prefix dir> [webview2 installer] [fonts dir]
#
# Needs, in this script's directory unless given on the command line:
#   MicrosoftEdgeWebView2RuntimeInstallerX64.exe  Evergreen Standalone Installer (x64) from
#       https://developer.microsoft.com/en-us/microsoft-edge/webview2/  (the downloader / help
#       pages are WebView2; its d3dcompiler_47.dll is also what the WinUI compositor needs)
#   SegoeIcons.ttf segmdl2.ttf SEGUISYM.TTF   Segoe Fluent Icons, Segoe MDL2 Assets, Segoe UI
#       Symbol from a Windows installation (C:\Windows\Fonts).  Not redistributable.  Do NOT add
#       the Segoe UI text family: it breaks the game's text layout.
# Missing pieces are skipped with a message; the script can be re-run.
set -e
WINE_BUILD="${1:?usage: $0 <wine-build dir> <prefix dir> [installer.exe] [fonts dir]}"
PREFIX="${2:?usage: $0 <wine-build dir> <prefix dir> [installer.exe] [fonts dir]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALLER="${3:-$HERE/MicrosoftEdgeWebView2RuntimeInstallerX64.exe}"
FONTDIR="${4:-$HERE}"
WINE="$WINE_BUILD/wine"
WINESERVER="$WINE_BUILD/server/wineserver"
[ -x "$WINE" ] || { echo "no wine at $WINE" >&2; exit 1; }

export WINEPREFIX="$PREFIX" WINEDEBUG=-all WINE_DISABLE_INPROC_SYNC=1
unset WINEDLLOVERRIDES LD_PRELOAD

echo "== prefix $PREFIX"
"$WINE" wineboot -u
"$WINE" winecfg -v win10
"$WINESERVER" -w

echo "== WebView2 runtime"
if [ -f "$INSTALLER" ]; then
  # silent install of the Evergreen runtime; takes a few minutes, spawns MicrosoftEdgeUpdate.exe
  "$WINE" "$INSTALLER" /silent /install || echo "installer exited with $? (check the prefix's Program Files (x86)/Microsoft/EdgeWebView)"
  "$WINESERVER" -w
else
  echo "   skipped: $INSTALLER not found (the downloader and help pages will stay blank)"
fi

echo "== d3dcompiler_47 from the WebView2 runtime"
DLL=$(ls "$PREFIX"/drive_c/Program\ Files\ \(x86\)/Microsoft/EdgeWebView/Application/*/d3dcompiler_47.dll 2>/dev/null | head -1)
if [ -n "$DLL" ]; then
  SYS="$PREFIX/drive_c/windows/system32"
  if [ -f "$SYS/d3dcompiler_47.dll" ] && [ ! -f "$SYS/d3dcompiler_47.dll.wine" ]; then
    mv "$SYS/d3dcompiler_47.dll" "$SYS/d3dcompiler_47.dll.wine"
  fi
  cp "$DLL" "$SYS/d3dcompiler_47.dll"
  "$WINE" reg add 'HKCU\Software\Wine\DllOverrides' /v d3dcompiler_47 /t REG_SZ /d native /f
else
  echo "   skipped: no WebView2 runtime in the prefix (the WinUI compositor cannot link its shaders without it)"
fi

echo "== icon fonts (DirectWrite only sees fonts listed in the registry)"
for entry in "SegoeIcons.ttf|Segoe Fluent Icons (TrueType)" "segmdl2.ttf|Segoe MDL2 Assets (TrueType)" "SEGUISYM.TTF|Segoe UI Symbol (TrueType)"; do
  file="${entry%%|*}"; name="${entry#*|}"
  src=$(find "$FONTDIR" -maxdepth 1 -iname "$file" | head -1)
  if [ -n "$src" ]; then
    cp "$src" "$PREFIX/drive_c/windows/Fonts/$file"
    "$WINE" reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Fonts' /v "$name" /t REG_SZ /d "$file" /f
  else
    echo "   skipped: $file not found in $FONTDIR (icons will show as boxes)"
  fi
done

echo "== Windows Animation Manager v2 (DirectManipulation inertia)"
"$WINE" regsvr32 uianimation.dll
"$WINESERVER" -w
echo "== done"
