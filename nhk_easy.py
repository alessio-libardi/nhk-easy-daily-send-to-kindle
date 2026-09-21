"""Build a Japanese EPUB from one publication day of NHK NEWS WEB EASY."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse
import uuid
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Comment
from ebooklib import epub
from PIL import Image, ImageOps
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

JST = ZoneInfo("Asia/Tokyo")
BASE_URL = "https://news.web.nhk/news/easy/"
LOG = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
CSS = """body { font-family: serif; line-height: 1.9; margin: 5%; }
h1 { font-size: 1.55em; line-height: 1.65; margin: 0.6em 0 1em; }
h2 { font-size: 1.25em; line-height: 1.7; }
p { margin: 0 0 1em; text-align: left; }
rt { font-size: 0.55em; }
.title-page { text-align: center; padding-top: 15%; }
.title-page p { text-align: center; }
.eyebrow, .date, .source { font-size: 0.8em; color: #555; }
.hero { display: block; width: 100%; height: auto; margin: 1em auto 1.5em; }
.source { border-top: 1px solid #bbb; padding-top: 1em; margin-top: 2em; }
a { color: inherit; }
"""


@dataclass
class Article:
    news_id: str
    title: str
    title_html: str
    published: datetime
    url: str
    body_html: str
    image: bytes | None


def japanese_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def clean_html(fragment: str) -> str:
    """Preserve paragraphs and ruby readings, never site scripts or styling."""
    soup = BeautifulSoup(fragment, "html.parser")
    for node in soup.find_all(string=lambda value: isinstance(value, Comment)):
        node.extract()
    for node in soup.select("script, style, iframe, object, form, audio, video"):
        node.decompose()
    allowed = {"p", "ruby", "rb", "rt", "rp", "br", "strong", "em", "ul", "ol", "li"}
    for node in list(soup.find_all(True)):
        if node.name in allowed:
            node.attrs = {}
        else:
            node.unwrap()
    return str(soup)


def publication_time(row: dict) -> datetime:
    # The arranged time determines NHK's editorial day; actual publication time
    # is used below to exclude articles that have not yet been released.
    value = row.get("news_prearranged_time")
    if not isinstance(value, str):
        raise ValueError("NHK index entry is missing its publication time")
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)


def parse_index(payload: object) -> dict[str, list[dict]]:
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    if not isinstance(payload, dict) or not payload:
        raise ValueError("NHK index format changed: expected publication dates")
    result = {}
    for key, rows in payload.items():
        date.fromisoformat(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("NHK index format changed: expected article lists")
        result[key] = rows
    return result


def select_articles(index: dict, day: date, now: datetime, limit: int = 5) -> list[dict]:
    if not 1 <= limit <= 5:
        raise ValueError("The article limit must be between 1 and 5")
    selected = {}
    for row in index.get(day.isoformat(), []):
        if row.get("news_display_flag") is False or row.get("news_publication_status") is False:
            continue
        news_id = str(row.get("news_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", news_id) or not row.get("title"):
            raise ValueError("NHK index contains an invalid article ID or title")
        published = publication_time(row)
        release = row.get("news_publication_time")
        released = datetime.strptime(release, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST) if release else published
        if published.date() == day and max(published, released) <= now:
            selected.setdefault(news_id, row)
    return sorted(selected.values(), key=lambda row: (
        int(row.get("top_priority_number") or 999), publication_time(row), row["news_id"]
    ))[:limit]


class NHKClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "nhk-easy-daily-send-to-kindle/1.0", "Accept-Language": "ja"})
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )))

    def index(self) -> dict:
        response = self.session.get(urljoin(BASE_URL, "news-list.json"), timeout=30)
        if response.status_code == 401:
            # NHK's ordinary anonymous overseas-visitor flow. No NHK account,
            # fabricated Japanese address, stored cookie, or paid access required.
            auth = self.session.get("https://news.web.nhk/tix/build_authorize", params={
                "idp": "a-alaz", "profileType": "abroad", "redirect_uri": BASE_URL, "entity": "none",
            }, timeout=30)
            auth.raise_for_status()
            response = self.session.get(urljoin(BASE_URL, "news-list.json"), timeout=30)
        response.raise_for_status()
        return parse_index(response.json())

    def image(self, url: str) -> bytes:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not (
            host.endswith(".nhk") or host.endswith(".nhk.or.jp") or host.endswith(".nhk.jp")
        ):
            raise ValueError("Unexpected NHK image URL")
        with self.session.get(url, timeout=30, stream=True) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("NHK image exceeds the download limit")
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1280, 1280))
            result = BytesIO()
            image.save(result, format="JPEG", quality=82, optimize=True)
            return result.getvalue()

    def article(self, row: dict) -> Article:
        news_id = row["news_id"]
        url = urljoin(BASE_URL, f"{news_id}/{news_id}.html")
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        body = soup.select_one("#js-article-body")
        if body is None or not body.select("p") or len(body.get_text(strip=True)) < 40:
            raise ValueError(f"Full article body missing for {news_id}; refusing to send a preview")
        image_url = row.get("news_easy_image_uri") or row.get("news_web_image_uri")
        if not image_url:
            hero = soup.select_one(".article-main__figure img, .article-main__image img")
            image_url = hero.get("src") if hero else None
        image = None
        if image_url:
            try:
                image = self.image(urljoin(url, image_url))
            except requests.HTTPError as error:
                # NHK sometimes replaces a photo on the original article without
                # updating Easy's index. Use that article's current official image.
                source_url = row.get("news_web_url", "")
                if (error.response.status_code not in (404, 410)
                        or urlparse(source_url).scheme != "https"
                        or urlparse(source_url).hostname != "news.web.nhk"):
                    raise
                source = self.session.get(source_url, timeout=30)
                source.raise_for_status()
                source.encoding = "utf-8"
                meta = BeautifulSoup(source.text, "html.parser").find("meta", property="og:image")
                if not meta or not meta.get("content"):
                    raise ValueError(f"Replacement image missing for {news_id}") from error
                image = self.image(urljoin(source_url, meta["content"]))
        if image is None:
            LOG.warning("NHK supplies no lead image for %s", news_id)
        return Article(
            news_id=news_id, title=row["title"],
            title_html=clean_html(row.get("title_with_ruby") or escape(row["title"])),
            published=publication_time(row), url=url,
            body_html=clean_html(str(body)), image=image,
        )


def build_epub(articles: list[Article], day: date, output_dir: Path) -> Path:
    if not 1 <= len(articles) <= 5:
        raise ValueError("An edition must contain 1 to 5 articles")
    title = f"NHKやさしいニュース - {japanese_date(day)}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{title}.epub"
    book = epub.EpubBook()
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, BASE_URL + day.isoformat()))
    book.set_identifier(identifier)
    book.set_title(title)
    book.set_language("ja")
    book.add_author("NHK")
    book.add_metadata("DC", "date", day.isoformat())
    book.add_metadata("DC", "source", BASE_URL)
    book.add_metadata("DC", "rights", "記事・画像の著作権はNHKなどの権利者に帰属します。")
    style = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=CSS.encode())
    book.add_item(style)
    front = epub.EpubHtml(title="表紙", file_name="title.xhtml", lang="ja")
    front.content = f"""<div class="title-page"><p class="eyebrow">NEWS WEB EASY</p>
      <h1>NHKやさしいニュース</h1><p>{japanese_date(day)}</p><p>全{len(articles)}記事</p>
      <p class="source">やさしい日本語で読む、今日のニュース</p></div>"""
    front.add_item(style)
    book.add_item(front)
    chapters = []
    for i, article in enumerate(articles, 1):
        image_html = ""
        if article.image:
            image_name = f"images/story-{i}.jpg"
            book.add_item(epub.EpubItem(uid=f"image-{i}", file_name=image_name,
                                      media_type="image/jpeg", content=article.image))
            image_html = f'<img class="hero" src="{image_name}" alt="{escape(article.title, quote=True)}"/>'
        chapter = epub.EpubHtml(title=article.title, file_name=f"story-{i}.xhtml", lang="ja")
        chapter.content = f"""<div class="story"><p class="eyebrow">第{i}話 ／ 全{len(articles)}話</p>
          <h1>{article.title_html}</h1><p class="date">{japanese_date(article.published.date())}</p>
          {image_html}{article.body_html}
          <p class="source">出典：<a href="{escape(article.url, quote=True)}">NHK NEWS WEB EASY</a></p></div>"""
        chapter.add_item(style)
        book.add_item(chapter)
        chapters.append(chapter)
    book.toc = chapters
    nav = epub.EpubNav(title="目次")
    nav.add_item(style)
    book.add_item(nav)
    book.add_item(epub.EpubNcx())
    book.spine = [front, nav, *chapters]
    epub.write_epub(str(path), book, {"raise_exceptions": True})
    return path


def report(message: str, **outputs):
    LOG.info(message)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
            for key, value in outputs.items():
                file.write(f"{key}={value}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(message + "\n\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="today", help="today (JST), latest, or YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    now = datetime.now(JST)
    client = NHKClient()
    index = client.index()
    if args.date == "latest":
        available = [date.fromisoformat(day) for day in index if date.fromisoformat(day) <= now.date()
                     and select_articles(index, date.fromisoformat(day), now)]
        if not available:
            raise ValueError("No published NHK articles are available")
        day = max(available)
    else:
        day = now.date() if args.date == "today" else date.fromisoformat(args.date)
    rows = select_articles(index, day, now)
    if not rows:
        report(f"No new articles for {day} (Japan time). No EPUB created or email sent.", created="false")
        return
    articles = []
    for row in rows:
        LOG.info("Fetching article %s", row["news_id"])
        articles.append(client.article(row))
    path = build_epub(articles, day, args.output)
    manifest = {
        "date": day.isoformat(), "title": path.stem, "file": path.name,
        "articles": [{"id": a.news_id, "title": a.title, "url": a.url} for a in articles],
    }
    (args.output / "edition.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report(f"Created **{path.name}** with {len(articles)} articles ({path.stat().st_size // 1024} KiB).",
           created="true", date=day.isoformat(), count=len(articles))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
