"""Generate a scoped navy + steel dark theme for AISP's legacy stylesheets.

For every rule in the legacy CSS, light backgrounds become dark navy/steel shades (hue kept, saturation
damped), dark text becomes light, light borders become a faint steel line. Each override is scoped under
`.aisp-legacy__page` so it beats the original selector and never touches the redesigned pages.
"""
import colorsys, io, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
OUT = os.path.join(ROOT, "features", "flow", "legacy-dark.css")
# stylesheets written dark from the start: converting them would turn their dark ink light
SKIP = ("livefield", os.sep + "hq" + os.sep, "slate", "record", "slip", "flow", "gamenight", "guitarists",
        "shopTheLine", "truthMeter", "verdictWall", "raceTrack", os.sep + "control" + os.sep, os.sep + "edge" + os.sep,
        os.sep + "mma" + os.sep, os.sep + "start" + os.sep, os.sep + "charts" + os.sep + "flowRiver")
SCOPE = ".aisp-legacy__page"

NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "#fff": (255, 255, 255)}
COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\bwhite\b|\bblack\b")

def parse(c):
    c = c.strip()
    if c.lower() in NAMED: return NAMED[c.lower()] + (1.0,)
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4): h = "".join(ch * 2 for ch in h)
        if len(h) not in (6, 8): return None
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (r, g, b, a)
    m = re.match(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+%?))?\s*\)", c)
    if m:
        a = m.group(4)
        a = (float(a[:-1]) / 100 if a and a.endswith("%") else float(a)) if a else 1.0
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)), a)
    return None

def lum(r, g, b): return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255

def fmt(r, g, b, a):
    r, g, b = (max(0, min(255, round(v))) for v in (r, g, b))
    return f"rgba({r}, {g}, {b}, {a:.2f})" if a < 0.999 else f"#{r:02x}{g:02x}{b:02x}"

def hls_to(h, l, s, a):
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return fmt(r * 255, g * 255, b * 255, a)

def map_bg(c):
    p = parse(c)
    if not p: return None
    r, g, b, a = p
    L = lum(r, g, b)
    if L < 0.62: return None  # already dark or a saturated brand colour: keep
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if s < 0.18:
        # navy + steel as two jobs (James 10-25): a white card is the steel frame, an off-white inset is a navy well
        if L >= 0.985:
            return hls_to(0.60, 0.215, 0.12, a)
        return hls_to(0.60, 0.075 + (1 - L) * 0.45, 0.42, a)
    return hls_to(h, 0.12 + (1 - L) * 0.25, min(0.55, s * 0.7), max(a, 0.9) if a > 0.5 else a)

def map_text(c):
    p = parse(c)
    if not p: return None
    r, g, b, a = p
    L = lum(r, g, b)
    if L > 0.5: return None
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if s < 0.25 or (L < 0.2 and 0.5 < h < 0.72):
        # neutral ink (and the legacy near-black navy ink, #10203a) stays neutral: light gray, not light blue (saturation 0.22 read as "all blue")
        return hls_to(0.60, 0.93 - L * 0.55, 0.07, a)
    return hls_to(h, max(0.62, 0.78 - L * 0.3), min(0.85, s + 0.1), a)

def map_line(c):
    p = parse(c)
    if not p: return None
    r, g, b, a = p
    if lum(r, g, b) < 0.6: return None
    return fmt(148, 163, 184, min(0.28, 0.12 + a * 0.12))

BG_PROPS = ("background", "background-color", "background-image")
TEXT_PROPS = ("color", "fill", "caret-color")
LINE_PROPS = ("border", "border-color", "border-top", "border-bottom", "border-left", "border-right", "border-top-color", "border-bottom-color", "border-left-color", "border-right-color", "outline", "outline-color", "stroke", "column-rule")

def convert_value(prop, val):
    fn = map_bg if prop in BG_PROPS else map_text if prop in TEXT_PROPS else map_line if prop in LINE_PROPS else None
    if prop.startswith("--"):
        # custom property: decide by its name
        n = prop.lower()
        fn = map_text if any(k in n for k in ("ink", "text", "fg", "muted", "dim")) else map_line if any(k in n for k in ("line", "border", "rule", "grid")) else map_bg if any(k in n for k in ("bg", "surface", "card", "soft", "white", "panel", "paper", "page", "canvas", "base", "light")) else None
    if not fn or "var(" in val and not COLOR_RE.search(val): return None
    changed = False
    def sub(m):
        nonlocal changed
        out = fn(m.group(0))
        if out: changed = True; return out
        return m.group(0)
    new = COLOR_RE.sub(sub, val)
    return new if changed else None

def scope_selector(sel):
    parts = []
    for s in sel.split(","):
        s = s.strip()
        if not s: continue
        if re.match(r"^(:root|html|body)\b", s):
            s2 = re.sub(r"^(:root|html|body)(\[[^\]]*\])?", SCOPE, s)
            parts.append(s2)
        elif s.startswith(("@", "from", "to")) or re.match(r"^\d+%$", s):
            return None
        else:
            parts.append(f"{SCOPE} {s}")
    return ", ".join(parts) if parts else None

def strip_comments(css): return re.sub(r"/\*.*?\*/", "", css, flags=re.S)

def walk(css, out, depth=0):
    i = 0; n = len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0: break
        head = css[i:j].strip()
        # find matching brace
        k = j + 1; lvl = 1
        while k < n and lvl:
            if css[k] == "{": lvl += 1
            elif css[k] == "}": lvl -= 1
            k += 1
        body = css[j + 1:k - 1]
        if head.startswith("@media") or head.startswith("@supports"):
            inner = []
            walk(body, inner, depth + 1)
            if inner: out.append(f"{head} {{\n" + "\n".join(inner) + "\n}")
        elif head.startswith("@"):
            pass  # keyframes, font-face, etc.
        elif "{" not in body:
            sel = scope_selector(head)
            if sel:
                decls = []
                for d in body.split(";"):
                    if ":" not in d: continue
                    prop, val = d.split(":", 1)
                    prop = prop.strip().lower(); val = val.strip()
                    if "!important" in val: continue
                    nv = convert_value(prop, val)
                    if nv: decls.append(f"{prop}: {nv}")
                if decls: out.append(f"{sel} {{ " + "; ".join(decls) + "; }")
        i = k

def main():
    files = []
    for base, _, names in os.walk(ROOT):
        for nm in names:
            if not nm.endswith(".css"): continue
            p = os.path.join(base, nm)
            if any(sk in p for sk in SKIP): continue
            files.append(p)
    files.sort(key=lambda p: (0 if p.endswith("styles.css") else 1, p))
    out = ["/* GENERATED by client/scripts/legacy_dark.py: navy + steel overrides for legacy pages, scoped to .aisp-legacy__page. Do not edit by hand. */"]
    total = 0
    for p in files:
        css = strip_comments(io.open(p, encoding="utf-8").read().replace(chr(0xFEFF), ""))
        rules = []
        walk(css, rules)
        if rules:
            out.append(f"/* {os.path.relpath(p, ROOT)} */")
            out.extend(rules)
            total += len(rules)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    print(f"{len(files)} files, {total} rules -> {OUT} ({os.path.getsize(OUT)//1024} KB)")

main()
