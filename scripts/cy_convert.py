#!/usr/bin/env python3
"""Convert cached Cyprus sources (_raw/cy/cache) into LawCodex Markdown.

  syntagma/syntagma.md          Greek consolidated Constitution (CyLaw)
  syntagma/constitution-en.md   English translation, rev. 2013 (Constitute Project)
  kefalaia/cap-NNN.md           colonial Caps      (only if cached, see cy_fetch.py)
  nomoi/<year>/<n>-<year>-<slug>.md  laws          (only if cached, see cy_fetch.py)

Usage: python cy_convert.py [--out DIR]
"""
import argparse
import datetime
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import warnings
from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning
from markdownify import markdownify

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

ROOT = Path(__file__).resolve().parents[2]
RAW = Path(os.environ.get("CY_RAW", ROOT / "_raw" / "cy" / "cache"))
CYLAW = "https://www.cylaw.org"
TODAY = datetime.date.today().isoformat()

# --------------------------------------------------------------------------- helpers

GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o",
}


def translit(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("ου", "ou").replace("αι", "ai").replace("ει", "ei").replace("οι", "oi")
    return "".join(GREEK.get(c, c) for c in s)


STOP = {"o", "i", "to", "oi", "ta", "tis", "tou", "ton", "peri", "nomos", "nomou", "kai"}


def slugify(title, maxlen=70):
    t = translit(title)
    t = re.sub(r"\b(tou|ths?)\s+\d{4}\b", "", t)
    words = [w for w in re.split(r"[^a-z0-9]+", t) if w and w not in STOP]
    slug = ""
    for w in words:
        if len(slug) + len(w) + 1 > maxlen:
            break
        slug = f"{slug}-{w}" if slug else w
    return slug or "nomos"


def yq(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def gazette_date(s):
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s or "")
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


def front_matter(meta):
    lines = ["---"]
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines += [f"  - {yq(x)}" for x in v]
        elif k in ("date", "last_amended", "retrieved") or isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {yq(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def clean_md(md):
    md = md.replace("\r", "").replace(" ", " ")
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def html_fragment_to_md(node):
    """Convert a CyLaw text block to Markdown; complex tables are kept as HTML."""
    for a in node.find_all("a"):
        if not a.get_text(strip=True):
            a.decompose()
        else:
            a.unwrap()
    parts = []
    for tbl in node.find_all("table"):
        if tbl.find(attrs={"rowspan": True}) or tbl.find(attrs={"colspan": True}) \
                or tbl.find("table"):
            for t in tbl.find_all(True):
                for attr in list(t.attrs):
                    if attr not in ("rowspan", "colspan"):
                        del t[attr]
            key = f"LAWCODEXTABLE{len(parts)}X"
            parts.append(str(tbl))
            tbl.replace_with(NavigableString(key))
    md = markdownify(str(node), heading_style="ATX", bullets="-", strip=["span", "font"],
                     escape_asterisks=False, escape_underscores=False)
    for i, t in enumerate(parts):
        md = md.replace(f"LAWCODEXTABLE{i}X", "\n\n" + t + "\n\n")
    return clean_md(md)


def h(level, text):
    text = re.sub(r"\s+", " ", text).strip()
    if level <= 6:
        return f"{'#' * level} {text}"
    return f"**{text}**"


# --------------------------------------------------------------------------- CyLaw

def parse_cylaw_index(path):
    html = path.read_bytes().decode("windows-1253", "replace")
    soup = BeautifulSoup(html, "lxml")
    title = soup.find("h2").get_text(" ", strip=True)
    amend = []
    for li in soup.find_all("li"):
        a = li.find("a", href=re.compile(r"/nomoi/arith/"))
        if not a:
            continue
        ref = li.find("i")
        amend.append({"title": a.get_text(" ", strip=True),
                      "pdf": CYLAW + a["href"],
                      "gazette": ref.get_text(" ", strip=True) if ref else ""})
    full = soup.find("a", href=re.compile(r"/nomoi/enop/.*/full\.html"))
    return {"title": title, "laws": amend, "full": full["href"] if full else None}


def convert_cylaw_full(path):
    """Return (title, markdown body) from a CyLaw consolidated full.html page."""
    html = path.read_bytes().decode("windows-1253", "replace")
    soup = BeautifulSoup(html, "lxml")
    title = soup.find("h1").get_text(" ", strip=True)
    main = soup.find("div", class_="main-content")
    for c in main.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for hist in main.find_all("div", class_="full-section-history"):
        hist.decompose()
    out = []
    has_div = bool(main.find("div", class_="full-division"))
    state = {"sub": False}

    def walk(el, div_depth):
        for ch in el.children:
            if not isinstance(ch, Tag):
                continue
            cls = ch.get("class") or []
            if "preamble" in cls and "current" not in cls:
                hdr = ch.find("div", class_="preamble-header")
                body = ch.find("div", class_="preamble")
                out.append(h(2, hdr.get_text(" ", strip=True) if hdr else "Προοίμιο"))
                if body:
                    out.append(html_fragment_to_md(body))
            elif "full-division" in cls:
                state["sub"] = False
                walk(ch, div_depth + 1)
            elif "full-division-header" in cls:
                out.append(h(1 + div_depth, ch.get_text(" ", strip=True)))
            elif "full-sub-division" in cls:
                state["sub"] = True
                out.append(h(2 + div_depth, ch.get_text(" ", strip=True)))
            elif "full-section" in cls:
                lvl = 2 + div_depth + (1 if state["sub"] else 0)
                hdr = ch.find("div", class_="full-section-header")
                txt = ch.find("div", class_="full-section-text")
                if hdr and hdr.get_text(strip=True):
                    out.append(h(lvl, hdr.get_text(" ", strip=True)))
                if txt:
                    out.append(html_fragment_to_md(txt))
            elif "full-appendices-title" in cls:
                state["sub"] = False
                out.append(h(2, ch.get_text(" ", strip=True)))
            elif "appendix-enum" in cls:
                out.append(h(3, ch.get_text(" ", strip=True)))
            elif "appendix-text" in cls:
                out.append(html_fragment_to_md(ch))
            elif "footnote-title" in cls:
                t = ch.get_text(" ", strip=True)
                if not t:
                    continue
                if t == "Σημείωση":
                    if not state.get("fn"):
                        out.append(h(2, "Σημειώσεις"))
                        state["fn"] = True
                    continue
                out.append(f"**{t}**")
            elif "footnote-text" in cls:
                if not state.get("fn"):
                    out.append(h(2, "Σημειώσεις"))
                    state["fn"] = True
                out.append(html_fragment_to_md(ch))
            elif ch.name == "div" and not cls:
                walk(ch, div_depth)
            elif "clear;" in cls or "clear" in cls or ch.name == "br":
                continue
            else:
                t = html_fragment_to_md(ch)
                if t:
                    out.append(t)

    walk(main, 0)
    body = "\n\n".join(x for x in out if x)
    return title, clean_md(body)


def law_meta(index, ident, typ, src_index):
    laws = index["laws"]
    date = gazette_date(laws[0]["gazette"]) if laws else None
    last = None
    for l in laws:
        d = gazette_date(l["gazette"])
        if d and (last is None or d > last):
            last = d
    amends = [re.sub(r"\s+", " ", l["title"]) for l in laws[1:]]
    return date, last, amends


def write_cylaw(index_path, full_path, out_path, typ, ident, date=None):
    index = parse_cylaw_index(index_path)
    title, body = convert_cylaw_full(full_path)
    d, last, amends = law_meta(index, ident, typ, index_path)
    meta = {
        "title": index["title"],
        "identifier": ident,
        "type": typ,
        "jurisdiction": "cy",
        "language": "el",
        "date": date or d,
        "last_amended": last,
        "status": "in_force",
        "consolidation": "unofficial (CyLaw / Cyprus Bar Association)",
        "source": CYLAW + index["full"],
        "source_index": CYLAW + "/" + str(index_path.relative_to(RAW / "cylaw")),
        "original_pdf": index["laws"][0]["pdf"] if index["laws"] else None,
        "amendments_incorporated": amends or None,
        "retrieved": datetime.date.fromtimestamp(full_path.stat().st_mtime).isoformat(),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(front_matter(meta) + f"# {title}\n\n" + body + "\n", encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- Constitute

def write_constitute(src, out_path):
    data = json.loads(src.read_text(encoding="utf-8"))
    soup = BeautifulSoup(data["html"], "lxml")
    copy = soup.find("div", class_="constitution-content__copy")
    for c in copy.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in copy.find_all(class_="constitution-topics"):
        t.decompose()
    # Constitute carries the original enumeration in li style="list-style-type: '1 '";
    # flatten lists into paragraphs prefixed with that label.
    for li in copy.find_all("li"):
        m = re.search(r"list-style-type:\s*'([^']*)'", li.get("style", ""))
        label = (m.group(1).strip() + ". ") if m and m.group(1).strip() else ""
        first = li.find("p")
        if label:
            (first or li).insert(0, NavigableString(label))
        li.name = "div" if first else "p"
        del li["style"]
    for ol in copy.find_all(["ol", "ul"]):
        ol.name = "div"
    # Article labels inside chapters are plain <p>Article N</p>
    for para in copy.find_all("p"):
        if re.fullmatch(r"Article \d+[A-Z]?", para.get_text(strip=True)) and \
                para.find_previous(["h2", "h3"]) and para.find_previous(["h2", "h3"]).name == "h3":
            para.name = "h4"
    for hh in copy.find_all(["h2", "h3"]):
        hh.string = re.sub(r"\s+", " ", hh.get_text(" ", strip=True))
    body = markdownify(str(copy), heading_style="ATX", bullets="-",
                       escape_asterisks=False, escape_underscores=False)
    meta = {
        "title": "Constitution of the Republic of Cyprus (English translation, rev. 2013)",
        "identifier": "Cyprus_2013",
        "type": "constitution",
        "jurisdiction": "cy",
        "language": "en",
        "date": "1960-08-16",
        "last_amended": "2013-07-17",
        "status": "in_force",
        "consolidation": "unofficial English translation; amendments after 2013 NOT incorporated",
        "source": "https://www.constituteproject.org/constitution/Cyprus_2013?lang=en",
        "licence": "Constitute Project, CC BY-NC 3.0 Unported",
        "retrieved": datetime.date.fromtimestamp(src.stat().st_mtime).isoformat(),
    }
    note = ("> English translation as compiled by the Comparative Constitutions Project "
            "(constituteproject.org), Cyprus 1960 (rev. 2013). It does not reflect the "
            "amendments of 2015-2024; the Greek text in `syntagma.md` is the current "
            "consolidated version.")
    out_path.write_text(front_matter(meta) + "# Constitution of the Republic of Cyprus\n\n"
                        + note + "\n\n" + clean_md(body) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- main

def law_target(out, index_rel, number_text, title):
    """nomoi/<year>/<n>[-part]-<year>-<slug>.md or kefalaia/cap-NNN.md"""
    stem = Path(index_rel).stem
    if re.fullmatch(r"\d+[A-Za-zΑ-Ωα-ω]*", stem):  # Cap
        m = re.match(r"(\d+)(.*)", stem)
        return out / "kefalaia" / f"cap-{int(m.group(1)):03d}{translit(m.group(2))}.md", \
            f"ΚΕΦ.{stem}", "act"
    year, part, num = stem.split("_", 2)
    m = re.search(r"\(([IΙ]+)\)", number_text or "")
    p = "-" + {"I": "i", "II": "ii", "III": "iii"}.get(m.group(1).replace("Ι", "I"), "x") if m else ""
    fn = f"{num}{p}-{year}-{slugify(title)}.md"
    return out / "nomoi" / year / fn, f"Ν. {number_text}", "act"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args()
    out = Path(args.out)
    cy = RAW / "cylaw" / "nomoi"

    si, sf = cy / "indexes/syntagma.html", cy / "enop/non-ind/syntagma/full.html"
    if si.exists() and sf.exists():
        p = write_cylaw(si, sf, out / "syntagma" / "syntagma.md", "constitution",
                        "ΣΥΝΤΑΓΜΑ", date="1960-08-16")
        print("wrote", p)
    cs = RAW / "constitute" / "Cyprus_2013_en.json"
    if cs.exists():
        write_constitute(cs, out / "syntagma" / "constitution-en.md")
        print("wrote", out / "syntagma" / "constitution-en.md")

    # Laws/Caps: only present in the cache if fetched with consent (see cy_fetch.py).
    n = 0
    for yidx in sorted(cy.glob("*_index.html")):
        html = yidx.read_bytes().decode("windows-1253", "replace")
        for href, text in re.findall(r'href="/nomoi/(indexes/[^"]+\.html)">([^<]+)</a>', html):
            ip = cy / href
            if not ip.exists():
                continue
            idx = parse_cylaw_index(ip)
            if not idx["full"]:
                print("  no consolidated text:", href, file=sys.stderr)
                continue
            fp = RAW / "cylaw" / idx["full"].lstrip("/")
            if not fp.exists():
                continue
            m = re.match(r"\s*(?:Ν\.|ΚΕΦ\.)\s*(\S+)\s*-\s*(.*)", text)
            numtxt, title = (m.group(1), m.group(2)) if m else ("", text)
            target, ident, typ = law_target(out, href, numtxt, title)
            write_cylaw(ip, fp, target, typ, ident)
            n += 1
    print(f"laws/caps written: {n}")


if __name__ == "__main__":
    main()
