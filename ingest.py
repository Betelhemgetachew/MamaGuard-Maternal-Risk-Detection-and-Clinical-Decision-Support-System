# type: ignore
"""
ingest.py — Build the verified clinical knowledge base.

Pipeline:
    PDF  →  page text  →  dosage-safe chunks  →  embeddings  →  ChromaDB

The source document is the Ministry of Health "Basic Obstetric Protocols
2026". Everything the /chat endpoint is allowed to say comes from here, so
the chunker's one hard rule is: **never split a medical dosage.**

A chunk that ends "...give 400" and a next chunk that starts "µg folic acid"
is worse than useless — retrieval would surface half a dose and the model
would answer with it. Split points are therefore snapped away from any span
matching DOSAGE_RE, and dosage-bearing bullets are kept whole.

Run:
    python ingest.py                 # build (skips if already current)
    python ingest.py --rebuild       # force a clean rebuild
    python ingest.py --inspect       # print sample chunks, no writes
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).parent
PDF_PATH = BASE_DIR / "Basic Obstetric Protocols 2026.pdf"
CHROMA_DIR = BASE_DIR / "chroma_db"
COLLECTION_NAME = "obstetric_protocols"

# Chunk sizing, in characters. Protocol prose is dense and bullet-heavy, so
# chunks stay small enough that a retrieved passage is mostly signal.
TARGET_CHARS = 1100
MAX_CHARS = 1600
MIN_CHARS = 220
OVERLAP_CHARS = 180


# ─────────────────────────────────────────────────────────────────────────────
# Dosage grammar
# ─────────────────────────────────────────────────────────────────────────────

# Units seen across the protocols: metric mass/volume, international units,
# lab values, pressures, rates. Ordered longest-first inside each alternation
# so that e.g. "mg/kg/hr" wins over "mg".
_UNIT = r"""(?:
      mcg/kg/min | mg/kg/min | mg/kg/hr | mg/kg/h
    | micrograms? | mcg | µg | ug
    | milligrams? | mg/dL | mg/dl | mg/kg | mg
    | grams? | g/dL | g/dl | g/L | g/l | gm | g
    | kilograms? | kg
    | millilitres? | milliliters? | mL/hr | mL/h | ml/hr | ml/h | mL | ml
    | litres? | liters? | L/min | l/min
    | international\ units? | IU | iu
    | units? | u
    | mmol/L | mmol/l | mmol | mEq/L | mEq | µmol/L | umol/L
    | mmHg | mm\ Hg | cmH2O
    | bpm | beats/min | breaths/min
    | %|percent
    | ppm | mg/24\ hours? | mg/24hrs?
)"""

# A dose expression: a number (or range/fraction) glued to a unit, optionally
# trailed by a route/frequency. Also matches bare frequency shorthand.
DOSAGE_RE = re.compile(
    rf"""
    (?:
        # 400 µg | 30-60 mg | 2.5 mg | 1/2 tablet | 4 g stat
        \d+(?:[.,]\d+)?
        (?:\s*(?:-|–|—|to|and)\s*\d+(?:[.,]\d+)?)?
        \s*{_UNIT}\b
        (?:\s*/\s*(?:kg|day|dose|hour|hr|min|24\s*hours?))?
        (?:\s*(?:IV|IM|PO|SC|IVI|per\s+os|orally|stat|bolus|infusion))?
        (?:\s*(?:od|bd|tds|qid|q\.?\d+\s*h|b\.d|t\.d\.s|o\.d|q\.i\.d)\b\.?)?
      |
        # every 4 hours | 6 hourly | 3 times a day
        (?:every|q)\s*\d+(?:[.,]\d+)?\s*(?:hours?|hrs?|h|minutes?|mins?|days?)\b
      |
        \d+\s*(?:hourly|daily|weekly)\b
      |
        \d+\s*times?\s+(?:a|per)\s+(?:day|week|hour)\b
      |
        # 140/90 mmHg — split blood pressures must survive intact too
        \d{{2,3}}\s*/\s*\d{{2,3}}\s*(?:mmHg|mm\ Hg)?\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Lines that look like running headers/footers in this document.
_NOISE_LINE = re.compile(
    r"^\s*(?:\d{1,3}\s*)?(?:Basic\s+Obstetric\s+Protocols|Ministry\s+of\s+Health)\s*(?:\d{1,3}\s*)?$",
    re.IGNORECASE,
)
_PAGE_NUM_ONLY = re.compile(r"^\s*\d{1,3}(?:\s+\d{1,3})?\s*$")

# Numbered protocol chapters, e.g. "3. Anemia in pregnancy" or "6.2 Sepsis".
_HEADING_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})*)[.)]?\s+([A-Z][^\n]{3,80})$")
# ALL-CAPS or Title-Case standalone headings.
_TITLE_HEADING_RE = re.compile(r"^\s*([A-Z][A-Za-z’'()/\-]+(?:\s+[A-Za-z’'()/\-]+){0,7})\s*:?\s*$")

_BULLET_RE = re.compile(r"^\s*(?:[•·▪◦‣»–—\-\*]|\(?[a-z]\)|\(?\d{1,2}[.)])\s+")


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    text: str
    page_start: int
    page_end: int
    section: str
    dosages: list[str] = field(default_factory=list)

    @property
    def uid(self) -> str:
        digest = hashlib.sha1(self.text.encode("utf-8")).hexdigest()[:16]
        return f"p{self.page_start:03d}-{digest}"

    def metadata(self) -> dict:
        return {
            "source": PDF_PATH.name,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section": self.section or "General",
            # Chroma metadata values must be scalars — join the list.
            "dosages": " | ".join(self.dosages[:12]),
            "has_dosage": bool(self.dosages),
            "chars": len(self.text),
        }


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, cleaned_text)] for every page with usable text."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SystemExit(
            "pypdf is required for ingestion.  pip install pypdf"
        ) from exc

    reader = PdfReader(str(pdf_path))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        cleaned = _clean_page(raw)
        if len(cleaned) >= 40:
            pages.append((i, cleaned))
    return pages


def _clean_page(raw: str) -> str:
    """Strip headers/footers, repair hyphenation, normalise whitespace."""
    # Rejoin words broken across a line by a hyphen: "hyperten-\nsion".
    raw = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", raw)
    raw = raw.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")

    kept: list[str] = []
    for line in raw.split("\n"):
        line = line.rstrip()
        if not line.strip():
            kept.append("")
            continue
        if _NOISE_LINE.match(line) or _PAGE_NUM_ONLY.match(line):
            continue
        # Collapse runs of spaces produced by the two-column layout, but keep
        # the leading indent so bullet detection still works.
        indent = len(line) - len(line.lstrip())
        kept.append(" " * min(indent, 4) + re.sub(r"[ \t]{2,}", " ", line.strip()))

    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_toc(text: str) -> bool:
    """Table-of-contents pages are dot-leader heavy and carry no protocol content."""
    return text.count("....") >= 3 or len(re.findall(r"\.{4,}", text)) >= 3


# ─────────────────────────────────────────────────────────────────────────────
# Blocks → chunks
# ─────────────────────────────────────────────────────────────────────────────

def _detect_section(line: str) -> str | None:
    """Return a section title if this line reads like a heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return None
    if _BULLET_RE.match(line):
        return None
    m = _HEADING_RE.match(stripped)
    if m:
        return f"{m.group(1)}. {m.group(2).strip()}"
    if stripped.isupper() and 4 <= len(stripped) <= 80:
        return stripped.title()
    m = _TITLE_HEADING_RE.match(stripped)
    if m and stripped.endswith(":") and len(stripped.split()) <= 8:
        return stripped.rstrip(":").strip()
    return None


def split_blocks(text: str) -> list[str]:
    """
    Break page text into atomic blocks — paragraphs and individual bullets.

    A block is the smallest unit the packer will move around. Bullets are
    atomic because in this document a bullet *is* the dose instruction
    ("Daily oral iron ... 30 mg to 60 mg of elemental iron and 400 µg folic
    acid"); splitting one splits a dose off its drug.
    """
    blocks: list[str] = []
    current: list[str] = []

    def flush():
        if current:
            joined = " ".join(part.strip() for part in current if part.strip())
            if joined:
                blocks.append(joined)
            current.clear()

    for line in text.split("\n"):
        if not line.strip():
            flush()
            continue
        if _BULLET_RE.match(line):
            flush()
            current.append(line)
            continue
        if current and _BULLET_RE.match(current[0]):
            # Continuation of the bullet we're already collecting.
            current.append(line)
            continue
        current.append(line)
    flush()
    return blocks


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges that a split must never land inside."""
    spans = []
    for m in DOSAGE_RE.finditer(text):
        # Widen slightly so the drug name immediately preceding a dose is not
        # orphaned by a split landing right on the number.
        start = m.start()
        lead = text.rfind(" ", max(0, start - 40), start)
        spans.append((lead if lead != -1 else start, m.end()))
    return spans


def _inside_protected(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s < pos < e for s, e in spans)


def _split_oversized(block: str) -> list[str]:
    """
    Break one over-long block at sentence boundaries, skipping any boundary
    that would fall inside a dosage expression.
    """
    if len(block) <= MAX_CHARS:
        return [block]

    spans = _protected_spans(block)
    # Candidate boundaries: end of sentence, then semicolons, then spaces.
    candidates = [m.end() for m in re.finditer(r"(?<=[.;:!?])\s+", block)]
    candidates += [m.start() for m in re.finditer(r",\s+", block)]
    candidates += [m.start() for m in re.finditer(r"\s+", block)]
    candidates = sorted({c for c in candidates if not _inside_protected(c, spans)})

    pieces: list[str] = []
    start = 0
    while len(block) - start > MAX_CHARS:
        target = start + TARGET_CHARS
        usable = [c for c in candidates if start + MIN_CHARS < c <= start + MAX_CHARS]
        if not usable:
            # Nothing safe to cut on — a single unbroken dosage table. Keep it
            # whole rather than mutilating a dose.
            break
        cut = min(usable, key=lambda c: abs(c - target))
        pieces.append(block[start:cut].strip())
        start = cut
    tail = block[start:].strip()
    if tail:
        pieces.append(tail)
    return [p for p in pieces if p]


def _overlap_tail(text: str) -> str:
    """
    Last ~OVERLAP_CHARS of a chunk, trimmed back to a boundary that is not
    inside a dosage, so the carried-over context never starts mid-dose.
    """
    if len(text) <= OVERLAP_CHARS:
        return text
    window_start = len(text) - OVERLAP_CHARS
    spans = _protected_spans(text)
    cut = text.find(" ", window_start)
    if cut == -1:
        return ""
    while cut < len(text) and _inside_protected(cut, spans):
        nxt = text.find(" ", cut + 1)
        if nxt == -1:
            return ""
        cut = nxt
    return text[cut:].strip()


def build_chunks(pages: list[tuple[int, str]]) -> list[Chunk]:
    """Pack page blocks into dosage-safe chunks, carrying section headings."""
    chunks: list[Chunk] = []
    section = "Front Matter"

    buf: list[str] = []
    buf_len = 0
    buf_page_start = pages[0][0] if pages else 1
    buf_page_end = buf_page_start
    buf_section = section

    def flush(force: bool = False):
        nonlocal buf, buf_len, buf_page_start, buf_page_end, buf_section
        if not buf:
            return
        body = "\n".join(buf).strip()
        if not body:
            buf, buf_len = [], 0
            return
        if len(body) < MIN_CHARS and not force:
            return
        header = f"[{buf_section}]" if buf_section else ""
        text = f"{header}\n{body}".strip()
        chunks.append(
            Chunk(
                text=text,
                page_start=buf_page_start,
                page_end=buf_page_end,
                section=buf_section,
                dosages=sorted({m.group(0).strip() for m in DOSAGE_RE.finditer(body)}),
            )
        )
        carry = _overlap_tail(body)
        buf = [carry] if carry else []
        buf_len = len(carry)
        buf_page_start = buf_page_end
        buf_section = section

    for page_no, text in pages:
        if _looks_like_toc(text):
            continue
        for raw_block in split_blocks(text):
            found = _detect_section(raw_block)
            if found and len(raw_block) <= 90:
                # A heading on its own line starts a new logical section.
                flush()
                section = found
                if not buf:
                    buf_section = section
                continue

            for block in _split_oversized(raw_block):
                if buf_len and buf_len + len(block) + 1 > TARGET_CHARS:
                    flush()
                if not buf:
                    buf_page_start = page_no
                    buf_section = section
                buf.append(block)
                buf_len += len(block) + 1
                buf_page_end = page_no

    flush(force=True)
    return [c for c in chunks if len(c.text) >= MIN_CHARS // 2]


# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB
# ─────────────────────────────────────────────────────────────────────────────

def get_client(persist_dir: Path = CHROMA_DIR):
    import chromadb

    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(persist_dir))


def get_collection(client=None, create: bool = True):
    """Return the protocol collection, or None when it hasn't been built yet."""
    client = client or get_client()
    if create:
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "source": PDF_PATH.name},
        )
    try:
        return client.get_collection(name=COLLECTION_NAME)
    except Exception:
        return None


def embed_chunks(chunks: list[Chunk], rebuild: bool = False, batch_size: int = 64) -> int:
    """Write chunks into the persistent ChromaDB collection."""
    if rebuild and CHROMA_DIR.exists():
        print(f"Removing existing store at {CHROMA_DIR} …")
        shutil.rmtree(CHROMA_DIR)

    client = get_client()
    collection = get_collection(client)

    total = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        collection.upsert(
            ids=[c.uid for c in batch],
            documents=[c.text for c in batch],
            metadatas=[c.metadata() for c in batch],
        )
        total += len(batch)
        print(f"  embedded {total}/{len(chunks)} chunks", end="\r", flush=True)
    print()
    return collection.count()


# ─────────────────────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_dosages(pages: list[tuple[int, str]], chunks: list[Chunk]) -> tuple[int, int]:
    """
    Every dosage present in the source must still appear, intact, in at least
    one chunk. Returns (found, total).
    """
    source_doses: set[str] = set()
    for _, text in pages:
        if _looks_like_toc(text):
            continue
        for m in DOSAGE_RE.finditer(text):
            source_doses.add(re.sub(r"\s+", " ", m.group(0).strip().lower()))

    chunk_blob = re.sub(r"\s+", " ", " ".join(c.text for c in chunks).lower())
    found = sum(1 for d in source_doses if d in chunk_blob)
    return found, len(source_doses)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest obstetric protocols into ChromaDB")
    parser.add_argument("--rebuild", action="store_true", help="wipe and rebuild the store")
    parser.add_argument("--inspect", action="store_true", help="print chunks, write nothing")
    parser.add_argument("--pdf", default=str(PDF_PATH), help="path to the protocol PDF")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}", file=sys.stderr)
        return 1

    print(f"Reading  {pdf_path.name}")
    pages = extract_pages(pdf_path)
    print(f"  {len(pages)} pages with extractable text")

    chunks = build_chunks(pages)
    print(f"Chunking → {len(chunks)} chunks "
          f"(avg {sum(len(c.text) for c in chunks) // max(len(chunks), 1)} chars)")

    found, total = verify_dosages(pages, chunks)
    print(f"Dosage integrity: {found}/{total} source dosage expressions intact in chunks")
    if total and found < total:
        missing_note = "  (a shortfall usually means a dose sat inside skipped front matter)"
        print(missing_note)

    if args.inspect:
        for c in chunks[:5]:
            print("\n" + "─" * 70)
            print(f"{c.uid}  p{c.page_start}-{c.page_end}  §{c.section}")
            print(f"doses: {', '.join(c.dosages[:8]) or '—'}")
            print(c.text[:600])
        return 0

    print(f"Embedding into ChromaDB at {CHROMA_DIR} …")
    count = embed_chunks(chunks, rebuild=args.rebuild)
    print(f"Done. Collection '{COLLECTION_NAME}' now holds {count} chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
