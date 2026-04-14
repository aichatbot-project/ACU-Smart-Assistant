"""Shared HTML → title + plain text extraction for crawlers."""
import re

from bs4 import BeautifulSoup

MAX_CONTENT_CHARS = 5000


def extract_title_and_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    raw_title = ""
    if soup.title and soup.title.string:
        raw_title = soup.title.string.strip()
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body
    if root is None:
        text = ""
    else:
        text = root.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = text[:MAX_CONTENT_CHARS]
    title = (raw_title or "")[:500]
    return title, text
