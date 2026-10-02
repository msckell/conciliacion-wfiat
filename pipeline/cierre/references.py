"""Extract the certified figures from the published certificate PDFs (pypdf + regex).

The output is a draft. A person confirms it before it is frozen in
data/golden/certifications.json, and nothing is compared against an unconfirmed figure.
Personal names in the certificates are not extracted.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

import httpx
import yaml
from pypdf import PdfReader

from cierre import CACHE_DIR, CONFIG_DIR

_FIGURE = re.compile(
    r"number of w\s?([A-Z]{3}) tokens issued as of (\d\d\.\d\d\.\d{4}),\s*"
    r"(total|totaling)\s+([\d.,]+?)\s+as\s+reported",
    re.IGNORECASE,
)
_HEADER_CUTOFF = re.compile(r"As of (\d\d\.\d\d\.\d{4})")
_NETWORKS = re.compile(r"on-chain on the (.+?)\s+networks", re.IGNORECASE | re.DOTALL)
_SIGNED = re.compile(r"Autonomous City of Buenos Aires,\s*(\d\d/\d\d/\d{4})")
_COLLATERAL = re.compile(
    r"in the total amount of\s+([A-Z]{3}|R\$)\s*([\d.,]+\d)", re.IGNORECASE | re.DOTALL
)


@dataclass
class ParsedNumber:
    printed: str
    value: str  # Decimal as string
    decimals: int  # printed precision
    thousands_sep: str
    decimal_sep: str | None
    ambiguous: bool


def parse_printed_number(s: str) -> ParsedNumber:
    """Parse a printed amount without guessing silently.

    If both separators appear, the last one is the decimal separator. If only one kind
    appears more than once, it is a thousands separator. A single separator followed by
    exactly three digits is ambiguous and flagged.
    """
    s = s.strip().rstrip(".,")
    commas, dots = s.count(","), s.count(".")
    ambiguous = False
    if commas and dots:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
    elif commas > 1 or dots > 1:
        thou, dec = ("," if commas > 1 else "."), None
    elif commas == 1 or dots == 1:
        sep = "," if commas else "."
        tail = s.split(sep)[1]
        if len(tail) == 3:
            thou, dec, ambiguous = sep, None, True
        else:
            thou, dec = ("." if sep == "," else ","), sep
    else:
        thou, dec = ",", None
    body = s.replace(thou, "")
    if dec:
        int_part, frac = body.split(dec)
        value = Decimal(f"{int_part}.{frac}")
        decimals = len(frac)
    else:
        value = Decimal(body)
        decimals = 0
    return ParsedNumber(s, str(value), decimals, thou, dec, ambiguous)


def load_sources(path: Path | None = None) -> list[tuple[str, str, str]]:
    """(token, cutoff, url) of every published certificate, from config/certificates.yaml."""
    raw = yaml.safe_load((path or CONFIG_DIR / "certificates.yaml").read_text(encoding="utf-8"))
    return [(c["token"], c["cutoff"], c["url"]) for c in raw["certificates"]]


def _clean(text: str) -> str:
    text = text.replace("\uf0b7", "•")
    return re.sub(r"[ \t]+", " ", text)


def fetch_pdf(url: str, dest_dir: Path | None = None) -> Path:
    dest_dir = dest_dir or CACHE_DIR / "certs"
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rsplit("/", 1)[-1])
    path = dest_dir / name
    if not path.exists():
        resp = httpx.get(url, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            raise ValueError(f"{url}: not a PDF")
        path.write_bytes(resp.content)
    return path


def extract(token: str, expected_cutoff: str, url: str) -> dict:
    path = fetch_pdf(url)
    pages = [_clean(p.extract_text() or "") for p in PdfReader(path).pages]
    notes: list[str] = []

    hits = [(i + 1, m) for i, page in enumerate(pages) for m in _FIGURE.finditer(page)]
    if len(hits) != 1:
        raise ValueError(f"{url}: expected exactly one figure sentence, found {len(hits)}")
    page_no, m = hits[0]
    sym = "w" + m.group(1).upper()
    if sym != token:
        raise ValueError(f"{url}: sentence names {sym}, expected {token}")
    text_cutoff = _iso(m.group(2))
    number = parse_printed_number(m.group(4))

    header = _HEADER_CUTOFF.search(pages[0])
    header_cutoff = _iso(header.group(1)) if header else None
    if header_cutoff != expected_cutoff:
        notes.append(f"title cutoff {header_cutoff} differs from the index cutoff")
    if text_cutoff != header_cutoff:
        notes.append(
            f"the figure sentence says 'issued as of {m.group(2)}' while the title says "
            f"'As of {header.group(1) if header else '?'}'"
        )
    full = "\n".join(pages)
    coll = _COLLATERAL.search(full)
    if number.ambiguous:
        # Resolve with the document's own convention, read from the collateral amount.
        conv = parse_printed_number(coll.group(2)) if coll else None
        if (
            conv
            and conv.decimal_sep
            and not conv.ambiguous
            and conv.thousands_sep == number.thousands_sep
        ):
            notes.append(
                f"'{number.printed}' alone is ambiguous. Read as thousands separator because "
                f"the same document prints the collateral as '{conv.printed}'"
            )
            number.ambiguous = False
        else:
            notes.append(f"separator in '{number.printed}' is ambiguous and unresolved")
    if number.thousands_sep == ".":
        notes.append(f"printed with '.' as thousands separator: '{number.printed}'")

    nets = _NETWORKS.search(full)
    networks = _split_networks(nets.group(1)) if nets else []
    signed = _SIGNED.search(full)

    sentence_start = pages[page_no - 1].rfind("•", 0, m.start())
    quote = pages[page_no - 1][max(sentence_start, m.start() - 20) : m.end()]
    return {
        "token": token,
        "cutoff": header_cutoff,
        "cutoff_in_figure_sentence": text_cutoff,
        "concept": "tokens issued as reported by the issuing entity (certified as outstanding)",
        "figure_printed": number.printed,
        "figure": number.value,
        "printed_decimals": number.decimals,
        "document_url": url,
        "page": page_no,
        "quote": " ".join(quote.split()),
        "networks_listed": networks,
        "network_keys": network_keys(networks),
        "signed_date": _iso(signed.group(1), sep="/") if signed else None,
        "collateral_as_stated": (
            {"currency": coll.group(1), "printed": coll.group(2)} if coll else None
        ),
        "notes": notes,
        "parse": asdict(number),
        "unresolved": number.ambiguous or not networks,
    }


def _iso(d: str, sep: str = ".") -> str:
    mm, dd, yyyy = d.split(sep)
    return f"{yyyy}-{mm}-{dd}"


def _split_networks(raw: str) -> list[str]:
    raw = " ".join(raw.split())
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", raw)
    return [p.strip() for p in parts if p.strip()]


# Names as printed in the certificates -> keys in config/chains.yaml.
NETWORK_ALIASES = {
    "ethereum": "ethereum",
    "world chain": "worldchain",
    "base": "base",
    "polygon": "polygon",
    "gnosis": "gnosis",
    "binance smart chain": "bsc",
    "bnb smart chain": "bsc",
    "hyperevm": "hyperevm",
    "celoscan": "celo",  # printed as the explorer name
    "celo": "celo",
}


def network_keys(names: list[str]) -> list[str]:
    keys = []
    for n in names:
        key = NETWORK_ALIASES.get(n.lower())
        if key is None:
            raise ValueError(f"unknown network name in certificate: {n!r}")
        keys.append(key)
    return keys


def extract_all() -> list[dict]:
    return [extract(t, c, u) for t, c, u in load_sources()]
