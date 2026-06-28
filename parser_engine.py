"""
Universal parser engine.
Supports: rss, html (BeautifulSoup + CSS selectors), api (JSON)
"""
import requests
from bs4 import BeautifulSoup
import feedparser
import json
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def parse_source(source: dict, max_items: int = 20) -> dict:
    """
    source: dict with keys: parser_type, url/rss_url, css_item, css_title, css_link, base_url
    Returns: {"items": [...], "error": None|str, "count": int}
    """
    ptype = source.get('parser_type', 'rss')
    try:
        if ptype == 'rss':
            return _parse_rss(source, max_items)
        elif ptype == 'html':
            return _parse_html(source, max_items)
        elif ptype == 'api':
            return _parse_api(source, max_items)
        else:
            return {"items": [], "error": f"Unknown parser type: {ptype}", "count": 0}
    except requests.exceptions.Timeout:
        return {"items": [], "error": "Timeout (>15s)", "count": 0}
    except requests.exceptions.ConnectionError as e:
        return {"items": [], "error": f"Connection error: {str(e)[:100]}", "count": 0}
    except Exception as e:
        return {"items": [], "error": str(e)[:200], "count": 0}


def _parse_rss(source: dict, max_items: int) -> dict:
    feed_url = source.get('rss_url') or source.get('url', '')
    feed = feedparser.parse(feed_url)
    if feed.bozo and not feed.entries:
        raise Exception(f"RSS parse error: {feed.bozo_exception}")
    items = []
    for entry in feed.entries[:max_items]:
        title = entry.get('title', '').strip()
        link = entry.get('link', '').strip()
        summary = entry.get('summary', '') or entry.get('description', '')
        image = _extract_rss_image(entry)
        if title and link:
            items.append({
                "title": title,
                "link": link,
                "summary": _strip_html(summary)[:500],
                "image": image,
            })
    return {"items": items, "error": None, "count": len(items)}


def _parse_html(source: dict, max_items: int) -> dict:
    url = source.get('url') or source.get('rss_url', '')
    css_item = source.get('css_item', 'article')
    css_title = source.get('css_title', 'h2, h3')
    css_link = source.get('css_link', 'a')
    base_url = source.get('base_url', '').rstrip('/')

    if not url:
        raise Exception("URL не указан")

    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'lxml')

    articles = soup.select(css_item)
    if not articles:
        # Try html.parser as fallback
        soup = BeautifulSoup(resp.text, 'html.parser')
        articles = soup.select(css_item)

    items = []
    seen_links = set()

    for art in articles[:max_items * 2]:
        # Find title
        title_el = art.select_one(css_title) if css_title else None
        title = ''
        if title_el:
            title = ''.join(title_el.stripped_strings).strip()

        # Find link
        link_el = art.select_one(css_link) if css_link else art.select_one('a')
        link = ''
        if link_el:
            link = link_el.get('href', '').strip()
            # Fix relative URLs
            if link and not link.startswith('http'):
                if link.startswith('//'):
                    link = 'https:' + link
                elif link.startswith('/'):
                    link = base_url + link
                else:
                    link = base_url + '/' + link

        # Find image
        img_el = art.select_one('img')
        image = img_el.get('src', '') if img_el else ''
        if image and not image.startswith('http') and base_url:
            image = base_url + image if image.startswith('/') else base_url + '/' + image

        if title and link and link not in seen_links:
            seen_links.add(link)
            items.append({
                "title": title,
                "link": link,
                "summary": "",
                "image": image,
            })
        if len(items) >= max_items:
            break

    return {"items": items, "error": None, "count": len(items)}


def _parse_api(source: dict, max_items: int) -> dict:
    url = source.get('rss_url') or source.get('url', '')
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Try to find items array
    items_data = data
    if isinstance(data, dict):
        for key in ['items', 'articles', 'results', 'data', 'posts']:
            if key in data and isinstance(data[key], list):
                items_data = data[key]
                break

    items = []
    for entry in (items_data if isinstance(items_data, list) else [])[:max_items]:
        title = entry.get('title') or entry.get('name') or entry.get('headline', '')
        link = entry.get('url') or entry.get('link') or entry.get('href', '')
        summary = entry.get('summary') or entry.get('description') or entry.get('excerpt', '')
        image = entry.get('image') or entry.get('thumbnail') or entry.get('image_url', '')
        if isinstance(image, dict):
            image = image.get('url', '')
        if title and link:
            items.append({
                "title": str(title).strip(),
                "link": str(link).strip(),
                "summary": str(summary)[:500] if summary else '',
                "image": str(image) if image else '',
            })
    return {"items": items, "error": None, "count": len(items)}


def _extract_rss_image(entry) -> str:
    # media:content
    if hasattr(entry, 'media_content') and entry.media_content:
        return entry.media_content[0].get('url', '')
    # media:thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url', '')
    # enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href', '')
    # img in summary
    summary = entry.get('summary', '') or entry.get('content', [{}])[0].get('value', '')
    if summary and '<img' in summary:
        soup = BeautifulSoup(summary, 'html.parser')
        img = soup.find('img')
        if img:
            return img.get('src', '')
    return ''


def _strip_html(html: str) -> str:
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text(separator=' ').strip()
