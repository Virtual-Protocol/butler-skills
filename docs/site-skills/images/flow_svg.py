"""Draw the two venue-skill flow diagrams as SVG files."""
import sys
from html import escape
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

FONT = "-apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif"
INK = "#1f2933"
MUTED = "#52606d"
LINE = "#7b8794"

STYLE = {
    "dev":     ("#e8f1fd", "#5b8def"),
    "gh":      ("#f1f3f5", "#868e96"),
    "review":  ("#fff4e5", "#f08c00"),
    "staging": ("#fff9db", "#e0a800"),
    "prod":    ("#e6f6ec", "#2f9e44"),
    "owner":   ("#f3f0ff", "#7048e8"),
    "butler":  ("#e8f1fd", "#5b8def"),
    "server":  ("#e6f6ec", "#2f9e44"),
    "site":    ("#f1f3f5", "#868e96"),
    "stop":    ("#fff0f0", "#e03131"),
}


class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def add(self, s):
        self.parts.append(s)

    def lane(self, x, w, title):
        self.add(f'<rect x="{x}" y="20" width="{w}" height="{self.h - 110}" rx="14" '
                 f'fill="#fafbfc" stroke="#e4e7eb"/>')
        self.add(f'<text x="{x + w / 2}" y="52" text-anchor="middle" font-size="15" '
                 f'font-weight="700" fill="{MUTED}" letter-spacing="1.5">{escape(title)}</text>')

    def box(self, x, y, w, h, kind, title, lines=(), thick=False):
        fill, stroke = STYLE[kind]
        sw = 3 if thick else 1.6
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw}"/>')
        n = 1 + len(lines)
        top = y + h / 2 - (n - 1) * 10
        self.add(f'<text x="{x + w / 2}" y="{top + 5}" text-anchor="middle" font-size="15" '
                 f'font-weight="700" fill="{INK}">{escape(title)}</text>')
        for i, ln in enumerate(lines, 1):
            self.add(f'<text x="{x + w / 2}" y="{top + 5 + i * 20}" text-anchor="middle" '
                     f'font-size="13" fill="{MUTED}">{escape(ln)}</text>')
        return (x, y, w, h)

    def arrow(self, pts, label=None, lx=None, ly=None, color=LINE, dash=False):
        d = "M " + " L ".join(f"{px} {py}" for px, py in pts)
        da = ' stroke-dasharray="6 5"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{da} '
                 f'marker-end="url(#ah)"/>')
        if label:
            if lx is None:
                (x1, y1), (x2, y2) = pts[0], pts[1]
                lx, ly = (x1 + x2) / 2, (y1 + y2) / 2 - 8
            self.add(f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="12" '
                     f'font-style="italic" fill="{MUTED}">{escape(label)}</text>')

    def title(self, text, sub):
        self.add(f'<text x="{self.w / 2}" y="{self.h - 46}" text-anchor="middle" font-size="14" '
                 f'font-weight="700" fill="{INK}">{escape(text)}</text>')
        self.add(f'<text x="{self.w / 2}" y="{self.h - 26}" text-anchor="middle" font-size="13" '
                 f'fill="{MUTED}">{escape(sub)}</text>')

    def write(self, path):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">'
                '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
                'markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" '
                f'fill="{LINE}"/></marker></defs>'
                f'<rect width="100%" height="100%" fill="#ffffff"/>')
        path.write_text(head + "".join(self.parts) + "</svg>")


def cx(b):
    return b[0] + b[2] / 2


def bottom(b):
    return b[1] + b[3]


def mid_y(b):
    return b[1] + b[3] / 2


# ---------------------------------------------------------------- 1. build and release
s = Svg(1280, 1250)
L, G, S = (30, 400), (450, 400), (870, 380)
s.lane(*L, "DEVELOPER LAPTOP")
s.lane(*G, "GITHUB")
s.lane(*S, "SERVERS")
bw, bh = 340, 72
lx, gx, sx = L[0] + 30, G[0] + 30, S[0] + 20

b1 = s.box(lx, 90, bw, bh, "dev", "1. Record the site", ["site.py record · replay · try-calls", "(Playwright)"])
b2 = s.box(lx, 200, bw, bh, "dev", "2. Claude drafts the files", ["venue.yaml + SKILL.md"])
b3 = s.box(lx, 310, bw, bh, "review", "3. Review the allow-list", ["Step 4 + security rules S1-S12"], thick=True)
b4 = s.box(lx, 420, bw, bh, "dev", "4. Push a branch", ["git push origin feat-x"])
g4 = s.box(gx, 420, bw, bh, "gh", "butler-skill-<site>", ["branch feat-x"])
b5 = s.box(lx, 560, bw, bh, "staging", "5. Test Butler installs SKILL.md", ["from the branch link", "(hub_install_url)"])
s5 = s.box(sx, 560, bw, bh, "staging", "STAGING server", ["fetches venue.yaml from the branch", "your test Butler only"])
b6 = s.box(lx, 700, bw, bh, "dev", "6. Works? Merge, bump, tag", ["version 1.3.0 · git tag v1.3.0"])
g6 = s.box(gx, 700, bw, bh, "gh", "butler-skill-<site>", ["tag v1.3.0"])
b7 = s.box(lx, 810, bw, bh, "dev", "7. Open one PR to butler-skills", ['skills.json  "ref": "v1.3.0"'])
g7 = s.box(gx, 810, bw, bh, "review", "butler-skills PR", ["reviewed by butler-maintainers", "THE release review"], thick=True)
g8 = s.box(gx, 930, bw, bh, "gh", "Hub build", ["clone the tag · pin the commit", "sha256 every file → index.json"])
g9 = s.box(gx, 1050, bw, bh, "gh", "GitHub Pages", ["index.json + files"])
s9 = s.box(sx, 1050, bw, bh, "prod", "PRODUCTION server", ["polls every 5 min · checks sha256", "caches venue.yaml"])

for a, b in ((b1, b2), (b2, b3), (b3, b4)):
    s.arrow([(cx(a), bottom(a)), (cx(b), b[1])])
s.arrow([(b4[0] + bw, mid_y(b4)), (g4[0], mid_y(g4))], "push")
s.arrow([(cx(g4) - 60, bottom(g4)), (cx(g4) - 60, 530), (cx(b5), 530), (cx(b5), b5[1])], "SKILL.md", cx(b5) + 120, 523)
s.arrow([(cx(g4) + 60, bottom(g4)), (cx(g4) + 60, 530), (cx(s5), 530), (cx(s5), s5[1])], "venue.yaml (by pointer)", cx(s5) - 40, 523)
s.arrow([(cx(b5), bottom(b5)), (cx(b6), b6[1])], "fix and repeat until it works", cx(b5) + 110, 672)
s.arrow([(b6[0] + bw, mid_y(b6)), (g6[0], mid_y(g6))], "push tag")
s.arrow([(cx(b6), bottom(b6)), (cx(b7), b7[1])])
s.arrow([(b7[0] + bw, mid_y(b7)), (g7[0], mid_y(g7))], "PR")
s.arrow([(cx(g7), bottom(g7)), (cx(g8), g8[1])], "merge", cx(g7) + 30, 916)
s.arrow([(cx(g8), bottom(g8)), (cx(g9), g9[1])])
s.arrow([(g9[0] + bw, mid_y(g9)), (s9[0], mid_y(s9))], "fetch")
s.title("Nothing reaches owners until the butler-skills PR merges.",
        "Staging tests your branch. Production runs only the tag that PR points at.")
s.write(OUT / "venue-flow-release.svg")

# ---------------------------------------------------------------- 2. an owner uses it
s = Svg(1400, 1310)
O, B, V, T = (30, 270), (320, 320), (660, 430), (1110, 260)
s.lane(*O, "OWNER")
s.lane(*B, "BUTLER (container)")
s.lane(*V, "BEVO SERVER (engine)")
s.lane(*T, "THE SITE")
ox, bx, vx, tx = O[0] + 20, B[0] + 20, V[0] + 25, T[0] + 20
ow, bwid, vw, tw = 230, 280, 380, 220

o1 = s.box(ox, 90, ow, 72, "owner", "“Long ETH $50", ["on Acme”"])
u1 = s.box(bx, 90, bwid, 72, "butler", "Reads SKILL.md, runs", ["acp site acme-perps open", "--usd 50 ..."])
v1 = s.box(vx, 200, vw, 72, "server", "Finds acme-perps in the hub index", ["its own cached, sha256-checked", "venue.yaml"])
v2 = s.box(vx, 310, vw, 72, "server", "Builds the card itself", ["never the AI · stores the", "venue.yaml sha256 with it"])
o2 = s.box(ox, 420, ow, 82, "owner", "Sees the card", ["“Open a $50 long on", "ETH on Acme” · Approve"], thick=True)
v3 = s.box(vx, 530, vw, 72, "server", "Runs the steps", ["in a cloud browser tab,", "locked to the site"])
t3 = s.box(tx, 530, tw, 72, "site", "The page", ["call / click"])
v4 = s.box(vx, 640, vw, 82, "review", "Every money request and", ["signature is HELD, then checked", "against allow + the card"], thick=True)
v5a = s.box(vx, 770, 175, 72, "stop", "No match", ["refused,", "nothing sent"])
v5b = s.box(vx + 205, 770, 175, 72, "prod", "Match", ["sent ONCE"])
t5 = s.box(tx, 770, tw, 72, "site", "The site", ["does the trade"])
v6 = s.box(vx, 880, vw, 72, "server", "Confirm", ["read the chain or the site's API", "never page words"])
u6 = s.box(bx, 990, bwid, 82, "butler", "Result", ["done / not_done / unknown", "typed fields, marked as site data"])
o7 = s.box(ox, 1100, ow, 72, "owner", "“Done. Filled", ["at $3,412.”"])

s.arrow([(o1[0] + ow, mid_y(o1)), (u1[0], mid_y(u1))], "asks")
s.arrow([(cx(u1), bottom(u1)), (cx(u1), mid_y(v1)), (v1[0], mid_y(v1))], "acp site", cx(u1) + 70, mid_y(v1) - 8)
s.arrow([(cx(v1), bottom(v1)), (cx(v2), v2[1])])
s.arrow([(v2[0], mid_y(v2)), (cx(o2), mid_y(v2)), (cx(o2), o2[1])], "card", cx(u1), mid_y(v2) - 8)
s.arrow([(cx(o2), bottom(o2)), (cx(o2), mid_y(v3)), (v3[0], mid_y(v3))], "tap Approve", cx(u1), mid_y(v3) - 8)
s.arrow([(v3[0] + vw, mid_y(v3) - 12), (t3[0], mid_y(t3) - 12)], "steps")
s.arrow([(t3[0], mid_y(t3) + 14), (v3[0] + vw, mid_y(v3) + 14)], "requests,", (v3[0] + vw + t3[0]) / 2, mid_y(t3) + 34, dash=True)
s.add(f'<text x="{(v3[0] + vw + t3[0]) / 2}" y="{mid_y(t3) + 50}" text-anchor="middle" font-size="12" font-style="italic" fill="{MUTED}">signatures</text>')
s.arrow([(cx(v3), bottom(v3)), (cx(v4), v4[1])])
s.arrow([(cx(v5a), bottom(v4)), (cx(v5a), v5a[1])])
s.arrow([(cx(v5b), bottom(v4)), (cx(v5b), v5b[1])])
s.arrow([(v5b[0] + 175, mid_y(v5b)), (t5[0], mid_y(t5))], "once")
s.arrow([(cx(v5b), bottom(v5b)), (cx(v5b), v6[1])])
s.arrow([(cx(v6), bottom(v6)), (cx(v6), mid_y(u6)), (u6[0] + bwid, mid_y(u6))], "result", cx(v6) + 40, mid_y(u6) - 8)
s.arrow([(cx(u6), bottom(u6)), (cx(u6), mid_y(o7)), (o7[0] + ow, mid_y(o7))], "tells the owner", cx(u6) - 60, mid_y(o7) - 8)
s.title("The AI asks, the owner approves, the server decides.",
        "The card, the hold and the check all run on the server, from the reviewed file.")
s.write(OUT / "venue-flow-runtime.svg")
print("ok")
