"""Selenium helpers for scraping OBS Bologna (obs.acibadem.edu.tr) pages."""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.html_extract import extract_title_and_text

logger = logging.getLogger(__name__)

BOLOGNA_INDEX_URL = "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=en"
SOURCE_LABEL = "obs.acibadem.edu.tr"
OBS_HOST = "obs.acibadem.edu.tr"
BOLOGNA_PATH_MARKER = "/oibs/bologna/"

USER_AGENT = (
    "ACU-Smart-Assistant/0.1 (+university project; respectful crawl; contact: student)"
)

MAX_EXPAND_PASSES = 12
MAX_POSTBACK_CLICKS_PER_PASS = 120
MAX_MENU_CLOSE_CLICKS = 80
PAGE_READY_TIMEOUT = 45
SHORT_WAIT = 12

# Embedded content uses dynConPage.aspx; program details use showPac in the URL.
_DYNCON_RE = re.compile(r"dynConPage\.aspx\?[^\"'\\s<>]+", re.IGNORECASE)
_SHOWPAC_ABS_RE = re.compile(
    r"https?://[^\s\"'<>]+showpac[^\s\"'<>]*", re.IGNORECASE
)
# Relative URLs and quoted fragments in onclick/HTML attributes.
_LOOSE_SHOWPAC_RE = re.compile(r"[^\s\"'<>]*showPac[^\s\"'<>]*", re.IGNORECASE)
_UNITSEL_RE = re.compile(
    r"[^\s\"'<>]*unitSelection\.aspx[^\s\"'<>]*", re.IGNORECASE
)


def build_driver() -> WebDriver:
    from selenium import webdriver

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(f"--user-agent={USER_AGENT}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    service = Service()
    return webdriver.Chrome(service=service, options=opts)


def normalize_obs_url(href: str, base: str) -> str:
    if not href:
        return ""
    h = href.strip()
    if h.startswith("#") or h.lower().startswith("javascript:"):
        return ""
    joined = urljoin(base, h)
    url, _frag = urldefrag(joined)
    url = url.replace("&amp;", "&")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != OBS_HOST:
        return ""
    path = (parsed.path or "").lower()
    if BOLOGNA_PATH_MARKER not in path and "showpac" not in url.lower():
        return ""
    return url


def _preferred_lang(url: str, target_lang: str) -> bool:
    """Drop obvious language-switch duplicates (e.g. index?lang=tr when scraping en)."""
    if target_lang.lower() != "en":
        return True
    q = parse_qs(urlparse(url).query.lower())
    if "lang" in q and q["lang"] == ["tr"]:
        return False
    return True


def _urls_from_html_regex(html: str, base: str) -> tuple[set[str], set[str]]:
    """Extract showPac and dynCon URLs embedded in onclick/JS (not always in <a href>)."""
    showpac: set[str] = set()
    other: set[str] = set()
    if not html:
        return showpac, other
    clean = html.replace("&amp;", "&")
    for m in _SHOWPAC_ABS_RE.finditer(clean):
        nu = normalize_obs_url(m.group(0).strip(), base)
        if nu:
            showpac.add(nu)
    bologna_base = f"https://{OBS_HOST}{BOLOGNA_PATH_MARKER}"
    for m in _DYNCON_RE.finditer(clean):
        path = m.group(0)
        nu = normalize_obs_url(urljoin(bologna_base, path), base)
        if nu:
            other.add(nu)
    for m in _UNITSEL_RE.finditer(clean):
        raw = m.group(0).strip('\'"')
        if raw.startswith("//"):
            raw = "https:" + raw
        nu = normalize_obs_url(urljoin(bologna_base, raw), base)
        if nu:
            other.add(nu)
    seen_loose: set[str] = set()
    for m in _LOOSE_SHOWPAC_RE.finditer(clean):
        raw = m.group(0).strip('\'"')
        if len(raw) < 8 or raw.lower().startswith("javascript"):
            continue
        if raw in seen_loose:
            continue
        seen_loose.add(raw)
        if raw.startswith("//"):
            raw = "https:" + raw
        if not raw.lower().startswith("http"):
            raw = urljoin(bologna_base, raw.lstrip("/"))
        nu = normalize_obs_url(raw, base)
        if nu:
            showpac.add(nu)
    return showpac, other


def wait_for_page_ready(driver: WebDriver, timeout: float = PAGE_READY_TIMEOUT) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        WebDriverWait(driver, min(timeout, SHORT_WAIT)).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except TimeoutException:
        logger.warning("Timeout waiting for document.readyState complete")


def _should_skip_element(href: str, text: str, skip_parts: list[str]) -> bool:
    if not skip_parts:
        return False
    blob = f"{href or ''} {text or ''}".lower()
    return any(p.strip() and p.strip().lower() in blob for p in skip_parts)


def _gather_anchor_hrefs(driver: WebDriver, base: str) -> tuple[set[str], set[str]]:
    """Returns (showpac_urls, other_bologna_urls)."""
    showpac: set[str] = set()
    other: set[str] = set()
    try:
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href]")
    except WebDriverException as e:
        logger.warning("Could not list anchors: %s", e)
        return showpac, other

    for a in anchors:
        try:
            raw = a.get_attribute("href") or ""
        except StaleElementReferenceException:
            continue
        if "showpac" not in raw.lower():
            nu = normalize_obs_url(raw, base)
            if nu:
                other.add(nu)
            continue
        nu = normalize_obs_url(raw, base)
        if nu:
            showpac.add(nu)
    return showpac, other


def _gather_from_all_frames(driver: WebDriver, base: str) -> tuple[set[str], set[str]]:
    """Walk default document and nested iframes; collect anchor hrefs + regex URLs from each frame's HTML."""
    showpac: set[str] = set()
    other: set[str] = set()

    def merge_html(html: str) -> None:
        sp, ot = _urls_from_html_regex(html, base)
        showpac.update(sp)
        other.update(ot)

    def walk_frames(depth: int = 0) -> None:
        if depth > 8:
            return
        try:
            html = driver.page_source
            merge_html(html)
            sp, ot = _gather_anchor_hrefs(driver, driver.current_url or base)
            showpac.update(sp)
            other.update(ot)
        except WebDriverException as e:
            logger.warning("gather frame: %s", e)

        try:
            frames = driver.find_elements(By.TAG_NAME, "iframe")
        except WebDriverException:
            return

        for f in frames:
            try:
                driver.switch_to.frame(f)
                time.sleep(0.05)
                walk_frames(depth + 1)
                driver.switch_to.parent_frame()
            except WebDriverException as e:
                logger.warning("iframe switch: %s", e)
                try:
                    driver.switch_to.parent_frame()
                except WebDriverException:
                    pass

    try:
        driver.switch_to.default_content()
        walk_frames(0)
    finally:
        try:
            driver.switch_to.default_content()
        except WebDriverException:
            pass

    return showpac, other


def _expand_postback_candidates(
    driver: WebDriver,
    delay: float,
    skip_parts: list[str],
    clicked: set[tuple[str, str, str]],
) -> int:
    """Click JavaScript / __doPostBack links to expand menus. Returns click count."""
    elements: list = []
    seen_fp: set[tuple[str, str, str]] = set()

    def _push_batch(found: list) -> None:
        for el in found:
            try:
                href = el.get_attribute("href") or ""
                onclick = el.get_attribute("onclick") or ""
                text = (el.text or "").strip()
                fp = (href[:600], onclick[:600], text[:200])
                if fp in seen_fp:
                    continue
                seen_fp.add(fp)
                elements.append(el)
            except StaleElementReferenceException:
                continue

    for sel in (
        "a[href*='__doPostBack']",
        "a[href*='javascript:']",
    ):
        try:
            _push_batch(driver.find_elements(By.CSS_SELECTOR, sel))
        except WebDriverException:
            continue

    try:
        _push_batch(
            driver.find_elements(By.XPATH, "//a[contains(@onclick,'__doPostBack')]")
        )
    except WebDriverException:
        pass

    clicks = 0
    for el in elements[:MAX_POSTBACK_CLICKS_PER_PASS]:
        if clicks >= MAX_POSTBACK_CLICKS_PER_PASS:
            break
        try:
            href = el.get_attribute("href") or ""
            onclick = el.get_attribute("onclick") or ""
            text = (el.text or "").strip()
        except StaleElementReferenceException:
            continue

        if _should_skip_element(href, f"{text} {onclick}", skip_parts):
            continue

        if "showpac" in href.lower() and "__dopostback" not in href.lower():
            continue

        fp = (href[:500], onclick[:500], text[:200])
        if fp in clicked:
            continue

        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            time.sleep(min(0.2, delay / 3))
            try:
                el.click()
            except ElementNotInteractableException:
                driver.execute_script("arguments[0].click();", el)
            clicked.add(fp)
            clicks += 1
            time.sleep(delay)
            wait_for_page_ready(driver, timeout=SHORT_WAIT)
        except (
            StaleElementReferenceException,
            ElementClickInterceptedException,
            ElementNotInteractableException,
            TimeoutException,
            WebDriverException,
        ) as e:
            logger.warning("Expand click skipped: %s", e)
            continue
    return clicks


def _expand_menu_close_links(
    driver: WebDriver,
    delay: float,
    skip_parts: list[str],
    clicked: set[tuple[str, str, str]],
) -> int:
    """Click nav items that use menu_close(..., 'dynConPage.aspx?...') to load iframe content."""
    try:
        driver.switch_to.default_content()
        candidates = driver.find_elements(
            By.CSS_SELECTOR, "a[onclick*='menu_close'], a[onclick*='dynConPage']"
        )
    except WebDriverException as e:
        logger.warning("menu_close find failed: %s", e)
        return 0

    clicks = 0
    for el in candidates[:MAX_MENU_CLOSE_CLICKS]:
        try:
            onclick = el.get_attribute("onclick") or ""
            href = el.get_attribute("href") or ""
            text = (el.text or "").strip()
        except StaleElementReferenceException:
            continue

        if "dynconpage" not in onclick.lower():
            continue
        if _should_skip_element(href, f"{text} {onclick}", skip_parts):
            continue

        fp = (href[:200], onclick[:700], text[:200])
        if fp in clicked:
            continue

        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            time.sleep(min(0.2, delay / 3))
            try:
                el.click()
            except ElementNotInteractableException:
                driver.execute_script("arguments[0].click();", el)
            clicked.add(fp)
            clicks += 1
            time.sleep(delay)
            wait_for_page_ready(driver, timeout=SHORT_WAIT)
        except (
            StaleElementReferenceException,
            ElementClickInterceptedException,
            ElementNotInteractableException,
            WebDriverException,
        ) as e:
            logger.debug("menu_close click skipped: %s", e)
            continue
    return clicks


def collect_bologna_urls(
    driver: WebDriver,
    delay: float,
    skip_section_parts: list[str] | None = None,
    target_lang: str = "en",
) -> list[str]:
    """
    Load the Bologna index, walk iframes, click expanders, collect showPac program URLs
    and other /oibs/bologna/ HTTP URLs. Returns sorted unique URLs (index first).
    """
    skip_parts = list(skip_section_parts or [])
    base = BOLOGNA_INDEX_URL
    all_showpac: set[str] = set()
    all_other: set[str] = set()
    all_other.add(BOLOGNA_INDEX_URL)

    try:
        driver.get(BOLOGNA_INDEX_URL)
        time.sleep(delay)
        wait_for_page_ready(driver)
    except WebDriverException as e:
        logger.warning("Initial load failed: %s", e)
        return [BOLOGNA_INDEX_URL]

    clicked_fingerprints: set[tuple[str, str, str]] = set()

    # Discover section URLs from onclick / HTML (dynConPage) before interaction.
    sp0, ot0 = _urls_from_html_regex(driver.page_source, base)
    all_showpac |= sp0
    all_other |= ot0

    section_queue: list[str] = sorted(ot0)
    seen_section: set[str] = {BOLOGNA_INDEX_URL}

    for section_url in section_queue:
        if section_url in seen_section:
            continue
        if not _preferred_lang(section_url, target_lang):
            continue
        seen_section.add(section_url)
        try:
            driver.get(section_url)
            time.sleep(delay)
            wait_for_page_ready(driver)
        except WebDriverException as e:
            logger.warning("Section GET failed %s: %s", section_url, e)
            continue

        sp, ot = _gather_from_all_frames(driver, driver.current_url or base)
        all_showpac |= sp
        for u in ot:
            if u not in seen_section and _preferred_lang(u, target_lang):
                all_other.add(u)
                if u not in section_queue and (
                    "dynconpage" in u.lower()
                    or "unitselection" in u.lower()
                ):
                    section_queue.append(u)

        spx, otx = _urls_from_html_regex(driver.page_source, driver.current_url or base)
        all_showpac |= spx
        all_other |= otx

    # Back to index for tree expansion + menu clicks (main nav lives on index).
    try:
        driver.get(BOLOGNA_INDEX_URL)
        time.sleep(delay)
        wait_for_page_ready(driver)
    except WebDriverException as e:
        logger.warning("Return to index failed: %s", e)

    for pass_idx in range(MAX_EXPAND_PASSES):
        before = len(all_showpac) + len(all_other)

        sp, ot = _gather_from_all_frames(driver, driver.current_url or base)
        all_showpac |= sp
        for u in ot:
            if _preferred_lang(u, target_lang):
                all_other.add(u)

        _expand_menu_close_links(driver, delay, skip_parts, clicked_fingerprints)
        sp, ot = _gather_from_all_frames(driver, driver.current_url or base)
        all_showpac |= sp
        for u in ot:
            if _preferred_lang(u, target_lang):
                all_other.add(u)

        _expand_postback_candidates(driver, delay, skip_parts, clicked_fingerprints)
        sp, ot = _gather_from_all_frames(driver, driver.current_url or base)
        all_showpac |= sp
        for u in ot:
            if _preferred_lang(u, target_lang):
                all_other.add(u)

        after = len(all_showpac) + len(all_other)
        if after == before and pass_idx > 0:
            break

    merged = all_showpac | all_other
    merged = {u for u in merged if _preferred_lang(u, target_lang)}
    ordered = sorted(merged)
    if BOLOGNA_INDEX_URL in merged:
        ordered.remove(BOLOGNA_INDEX_URL)
        ordered.insert(0, BOLOGNA_INDEX_URL)
    return ordered


def fetch_page_extract(
    driver: WebDriver, url: str, delay: float, retries: int = 2
) -> tuple[str, str]:
    """Navigate to url and return (title, text) via extract_title_and_text."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            driver.get(url)
            time.sleep(delay)
            wait_for_page_ready(driver)
            html = driver.page_source
            title, text = extract_title_and_text(html)
            if not title:
                title = url[:500]
            return title, text
        except WebDriverException as e:
            last_exc = e
            logger.warning("fetch_page_extract attempt %s failed for %s: %s", attempt, url, e)
            time.sleep(delay)
    if last_exc:
        logger.warning("Giving up on %s: %s", url, last_exc)
    return url[:500], ""
