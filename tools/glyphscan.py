#!/usr/bin/env python3
"""glyphscan.py [--wine WINETREE] [--dir GAMEDIR] [extra files...]

Collects every non-ASCII character the beta can show (Assets/Language.json in all locales, UTF-16
strings in Qwilight.dll, the skin's text) and reports per Unicode block: how many code points, samples,
whether Wine's dwrite fallback table (dlls/dwrite/analyzer.c) has an entry covering the block, and
whether fontconfig has any font with the sample glyphs. Private-use code points are listed separately:
those are FontIcon glyphs from Segoe Fluent Icons / Segoe MDL2 Assets and need that font.
"""
import os, re, sys, json, glob, subprocess, collections, unicodedata, zipfile

GAMEDIR = "/mnt/Yucky/SteamLibrary/steamapps/common/Qwilight"
WINE = os.path.expanduser("~/dev/qwilight/wine")

BLOCKS = []  # (start, end, name) — a compact table of the blocks that matter for UI text
for line in """0000 007F Basic Latin
0080 00FF Latin-1 Supplement
0100 017F Latin Extended-A
0180 024F Latin Extended-B
0250 02AF IPA Extensions
02B0 02FF Spacing Modifier Letters
0300 036F Combining Diacritical Marks
0370 03FF Greek and Coptic
0400 04FF Cyrillic
0500 052F Cyrillic Supplement
0530 058F Armenian
0590 05FF Hebrew
0600 06FF Arabic
0700 074F Syriac
0780 07BF Thaana
0900 097F Devanagari
0980 09FF Bengali
0A00 0A7F Gurmukhi
0A80 0AFF Gujarati
0B00 0B7F Oriya
0B80 0BFF Tamil
0C00 0C7F Telugu
0C80 0CFF Kannada
0D00 0D7F Malayalam
0D80 0DFF Sinhala
0E00 0E7F Thai
0E80 0EFF Lao
0F00 0FFF Tibetan
1000 109F Myanmar
10A0 10FF Georgian
1100 11FF Hangul Jamo
1200 137F Ethiopic
1780 17FF Khmer
1E00 1EFF Latin Extended Additional
1F00 1FFF Greek Extended
2000 206F General Punctuation
2070 209F Superscripts and Subscripts
20A0 20CF Currency Symbols
2100 214F Letterlike Symbols
2150 218F Number Forms
2190 21FF Arrows
2200 22FF Mathematical Operators
2300 23FF Miscellaneous Technical
2400 243F Control Pictures
2460 24FF Enclosed Alphanumerics
2500 257F Box Drawing
2580 259F Block Elements
25A0 25FF Geometric Shapes
2600 26FF Miscellaneous Symbols
2700 27BF Dingbats
27C0 27EF Misc Mathematical Symbols-A
27F0 27FF Supplemental Arrows-A
2900 297F Supplemental Arrows-B
2B00 2BFF Miscellaneous Symbols and Arrows
3000 303F CJK Symbols and Punctuation
3040 309F Hiragana
30A0 30FF Katakana
3100 312F Bopomofo
3130 318F Hangul Compatibility Jamo
3200 32FF Enclosed CJK Letters and Months
3300 33FF CJK Compatibility
3400 4DBF CJK Unified Ideographs Extension A
4E00 9FFF CJK Unified Ideographs
AC00 D7AF Hangul Syllables
E000 F8FF Private Use Area
F900 FAFF CJK Compatibility Ideographs
FB00 FB4F Alphabetic Presentation Forms
FE30 FE4F CJK Compatibility Forms
FF00 FFEF Halfwidth and Fullwidth Forms
1F000 1F02F Mahjong Tiles
1F300 1F5FF Miscellaneous Symbols and Pictographs
1F600 1F64F Emoticons
1F680 1F6FF Transport and Map Symbols
1F900 1F9FF Supplemental Symbols and Pictographs""".splitlines():
    a, b, n = line.split(" ", 2); BLOCKS.append((int(a, 16), int(b, 16), n))

def block_of(cp):
    for a, b, n in BLOCKS:
        if a <= cp <= b: return (a, b, n)
    return (cp & ~0xff, cp | 0xff, "U+%04X block" % (cp & ~0xff))

def fallback_table(wine):
    """[(set of (lo,hi)), fonts] parsed from dlls/dwrite/analyzer.c."""
    src = open(os.path.join(wine, "dlls/dwrite/analyzer.c"), errors="replace").read()
    entries = []
    for m in re.finditer(r'\{\s*((?:"[^"]*"\s*)+),\s*L"([^"]*)"\s*\}', src):
        ranges = "".join(re.findall(r'"([^"]*)"', m.group(1)))
        rs = []
        for r in ranges.split(","):
            r = r.strip()
            if not r: continue
            lo, hi = r.split("-") if "-" in r else (r, r)
            rs.append((int(lo, 16), int(hi, 16)))
        entries.append((rs, m.group(2)))
    return entries

def fallback_for(cp, table):
    for rs, fonts in table:
        for lo, hi in rs:
            if lo <= cp <= hi: return fonts
    return None

def fc_has(cp):
    try:
        out = subprocess.run(["fc-list", ":charset=%x" % cp, "family"], capture_output=True, text=True, timeout=20).stdout
    except Exception: return None
    fams = sorted(set(l.split(",")[0] for l in out.splitlines() if l.strip()))
    return fams


def clr_user_strings(data):
    """Yield the strings of a .NET assembly's #US heap."""
    import struct
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    ndirs_off = opt + (108 if magic == 0x20b else 92)
    clr_rva, clr_size = struct.unpack_from("<II", data, ndirs_off + 4 + 8*14)
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    soff = opt + struct.unpack_from("<H", data, pe + 20)[0]
    secs = [struct.unpack_from("<8sIIII", data, soff + 40*i) for i in range(nsec)]
    def rva(r):
        for name, vsize, va, rsize, raw in secs:
            if va <= r < va + max(vsize, rsize): return raw + (r - va)
        return None
    if not clr_rva: return
    clr = rva(clr_rva)
    md_rva, md_size = struct.unpack_from("<II", data, clr + 8)
    md = rva(md_rva)
    if data[md:md+4] != b"BSJB": return
    vlen = struct.unpack_from("<I", data, md + 12)[0]
    p = md + 16 + vlen
    streams = struct.unpack_from("<H", data, p + 2)[0]
    p += 4
    for i in range(streams):
        off, size = struct.unpack_from("<II", data, p)
        e = data.index(b"\0", p + 8)
        name = data[p+8:e].decode()
        p = e + 1
        p = (p + 3) & ~3
        if name == "#US":
            q, end = md + off + 1, md + off + size
            while q < end:
                b = data[q]
                if b < 0x80: n, q = b, q + 1
                elif b < 0xc0: n, q = ((b & 0x3f) << 8) | data[q+1], q + 2
                else: n, q = ((b & 0x1f) << 24) | (data[q+1] << 16) | (data[q+2] << 8) | data[q+3], q + 4
                if n == 0: continue
                try: yield data[q:q+n-1].decode("utf-16le", "ignore")
                except Exception: pass
                q += n

def collect(gamedir, extra):
    chars = collections.defaultdict(collections.Counter)   # cp -> Counter(source)
    def add(text, src):
        for ch in text:
            if ord(ch) >= 0x80: chars[ord(ch)][src] += 1
    lang = os.path.join(gamedir, "Assets/Language.json")
    if os.path.exists(lang):
        for key, v in json.load(open(lang, encoding="utf-8")).items():
            if isinstance(v, dict):
                for loc, s in v.items():
                    if isinstance(s, str): add(s, "Language.json:" + loc)
    dll = os.path.join(gamedir, "Qwilight.dll")
    if os.path.exists(dll):
        # only the CLR #US heap: the C#/XAML string literals, not the embedded data tables
        for s in clr_user_strings(open(dll, "rb").read()): add(s, "Qwilight.dll")
    ui = glob.glob(os.path.join(gamedir, "yucky/UI/*/*.zip"))
    for z in ui:
        try:
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    if n.lower().endswith((".yaml", ".yml", ".json", ".txt", ".lua")):
                        try: add(zf.read(n).decode("utf-8", "ignore"), os.path.basename(z) + ":" + n.rsplit("/", 1)[-1])
                        except Exception: pass
        except Exception: pass
    for f in extra:
        try: add(open(f, encoding="utf-8", errors="ignore").read(), os.path.basename(f))
        except Exception as e: print("!! %s: %s" % (f, e), file=sys.stderr)
    return chars

def main():
    args = sys.argv[1:]; gamedir, wine, extra = GAMEDIR, WINE, []
    while args:
        a = args.pop(0)
        if a == "--dir": gamedir = args.pop(0)
        elif a == "--wine": wine = args.pop(0)
        else: extra.append(a)
    chars = collect(gamedir, extra)
    table = fallback_table(wine)
    blocks = collections.defaultdict(list)
    for cp in sorted(chars): blocks[block_of(cp)].append(cp)
    print("%d distinct non-ASCII code points in %d blocks\n" % (len(chars), len(blocks)))
    print("%-42s %6s  %-9s %-9s  %s" % ("block", "count", "fallback", "fontconfig", "samples / sources"))
    for (a, b, name), cps in sorted(blocks.items()):
        fb = sum(1 for cp in cps if fallback_for(cp, table))
        fbs = "%d/%d" % (fb, len(cps)) if fb != len(cps) else "all"
        if fb == 0: fbs = "NONE"
        sample = cps[len(cps)//2]
        fams = fc_has(sample)
        fcs = "none" if fams == [] else ("?" if fams is None else "%d fonts" % len(fams))
        pua = 0xE000 <= a <= 0xF8FF
        samples = "".join(chr(cp) for cp in cps[:12]) if not pua else " ".join("U+%04X" % cp for cp in cps[:8])
        srcs = collections.Counter()
        for cp in cps: srcs.update(chars[cp].keys())
        top = ", ".join("%s(%d)" % (s.split(":")[0], n) for s, n in srcs.most_common(3))
        flag = "" if (fb == len(cps) and fams) or a < 0x80 else "  <-- "
        print("%-42s %6d  %-9s %-9s  %s  [%s]%s" % ("%s (U+%04X-%04X)" % (name, a, b), len(cps), fbs, fcs, samples, top, flag))
    pua = [cp for cp in sorted(chars) if 0xE000 <= cp <= 0xF8FF]
    if pua:
        print("\nPrivate-use code points (FontIcon glyphs; Segoe Fluent Icons / Segoe MDL2 Assets): %d" % len(pua))
        print("  " + " ".join("U+%04X" % cp for cp in pua))
    print("\nfallback entries in analyzer.c: %d" % len(table))

if __name__ == "__main__": main()
