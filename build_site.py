"""Publish the latest NHK Easy daily edition and its podcast episodes."""

from __future__ import annotations

import argparse
from datetime import datetime
from email.utils import format_datetime
from html import escape
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlencode, urljoin, urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from nhk_easy import JST, NHKClient, clean_html, japanese_date, select_articles

ROOT = Path(__file__).resolve().parent
ROME = ZoneInfo("Europe/Rome")
LOG = logging.getLogger(__name__)
TITLE = "まいにち | Japanese reading & listening"
ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
CONTENT = "http://purl.org/rss/1.0/modules/content/"
ATOM = "http://www.w3.org/2005/Atom"
for prefix, namespace in [("itunes", ITUNES), ("content", CONTENT), ("atom", ATOM)]:
    ET.register_namespace(prefix, namespace)


def plain_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    for reading in soup.select("rt, rp"):
        reading.decompose()
    return soup.get_text("", strip=True)


def latest_rows(index: dict, now: datetime) -> list[dict]:
    """Use one latest available publication date, never mix in an older edition."""
    for day in sorted(index, reverse=True):
        if day <= now.astimezone(JST).date().isoformat():
            rows = select_articles(index, datetime.fromisoformat(day).date(), now)
            if rows:
                return rows
    raise ValueError("No published articles available; keep the previous deployment")


def audio_source(row: dict) -> str | None:
    voice = row.get("news_easy_voice_uri")
    if not voice:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.m4a", voice):
        raise ValueError("NHK audio filename format changed")
    # NHK's current player/audio-v6.html maps this ID to its public HLS stream.
    return f"https://media.vd.st.nhk/news/easy_audio/{voice[:-4]}/index.m3u8"


def encode_audio(row: dict, directory: Path, playback_token: str = "") -> dict | None:
    source = audio_source(row)
    if source is None:
        return None
    if playback_token:
        source += "?" + urlencode({"hdnts": playback_token})
    name = f"{row['news_id']}.mp3"
    destination = directory / name
    directory.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-protocol_whitelist", "https,tls,tcp,crypto", "-rw_timeout", "30000000",
               "-i", source, "-vn", "-map_metadata", "-1", "-ac", "1", "-ar", "44100",
               "-c:a", "libmp3lame", "-b:a", "64k", str(destination)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "json", str(destination)], check=True,
                               capture_output=True, text=True, timeout=15)
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        size = destination.stat().st_size
        if not 1 <= duration <= 1800 or not 1000 <= size <= 20 * 1024 * 1024:
            raise ValueError("Audio is empty or unexpectedly large")
    except subprocess.SubprocessError:
        destination.unlink(missing_ok=True)
        # Subprocess exceptions include the command; never log the temporary token.
        raise RuntimeError(f"Audio conversion failed for {row['news_id']}") from None
    except (ValueError, KeyError):
        destination.unlink(missing_ok=True)
        raise
    return {"path": f"audio/{name}", "bytes": size, "duration": round(duration)}


def shell(title: str, body: str, prefix: str = "", canonical: str = "") -> str:
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><meta name="theme-color" content="#f5f3ed">
<meta name="description" content="A little Japanese, every day. Read NHK Easy stories with furigana and listen to their audio.">
<link rel="icon" href="{prefix}assets/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="{prefix}assets/touch-icon.png">
<link rel="stylesheet" href="{prefix}assets/style.css"><script defer src="{prefix}assets/app.js"></script>
<link rel="alternate" type="application/rss+xml" title="Reading feed" href="{prefix}feed.xml">
<link rel="alternate" type="application/rss+xml" title="Audio podcast" href="{prefix}podcast.xml">
{f'<link rel="canonical" href="{escape(canonical, quote=True)}">' if canonical else ''}
</head><body><a class="skip" href="#main">Skip to content</a>
<header class="masthead"><a class="brand" href="{prefix}index.html" aria-label="まいにち home"><span class="brand-icon" aria-hidden="true">日</span><span>まいにち<small>JAPANESE, EVERY DAY</small></span></a>
<nav aria-label="Main navigation"><a href="{prefix}index.html">Read <span lang="ja">読む</span></a><a href="{prefix}subscribe.html">Listen <span lang="ja">聞く</span></a></nav></header>
{body}
<footer><a class="footer-brand" href="{prefix}index.html">まいにち</a><p>Small stories. A daily practice.</p><p class="credit">An independent personal study project, not affiliated with NHK.<br>Articles, images and audio belong to NHK and their respective rights holders. Each story links to its original.</p><div><a href="{prefix}feed.xml">Reading RSS ↗</a><a href="{prefix}podcast.xml">Podcast RSS ↗</a><a href="https://news.web.nhk/news/easy/">NHK Easy ↗</a></div></footer>
</body></html>'''


def duration_label(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02}"


def card(story: dict, number: int, featured: bool = False) -> str:
    image = (f'<img src="{story["image"]}" alt="" loading="{"eager" if featured else "lazy"}">'
             if story["image"] else '<div class="no-image" aria-hidden="true">読む</div>')
    audio = f'<span>◉ {duration_label(story["audio"]["duration"])} audio</span>' if story["audio"] else '<span>Reading</span>'
    return f'''<article class="story-card {'featured' if featured else ''}" data-story="{story['id']}">
<a class="card-image" href="stories/{story['id']}.html" tabindex="-1" aria-hidden="true">{image}</a>
<div class="card-copy"><div class="card-meta"><span>STORY {number:02}</span>{audio}<span class="read-badge" hidden>Read ✓</span></div>
<h3><a href="stories/{story['id']}.html">{story['title_html']}</a></h3>
<p class="excerpt">{escape(story['excerpt'])}</p><a class="text-link" href="stories/{story['id']}.html">Read story <span aria-hidden="true">↗</span></a></div></article>'''


def write_home(stories: list[dict], output: Path, now: datetime, base: str):
    local = now.astimezone(ROME)
    groups: dict[str, list] = {}
    for story in stories:
        groups.setdefault(story["date"], []).append(story)
    sections = []
    for i, (day, items) in enumerate(groups.items()):
        label = japanese_date(datetime.fromisoformat(day).date())
        cards = "".join(card(s, n, i == 0 and n == 1) for n, s in enumerate(items, 1))
        sections.append(f'<section class="edition" aria-label="{label}"><div class="section-heading"><h2>{label}</h2><span>{"LATEST EDITION · " if i == 0 else ""}{len(items)} STORIES</span></div><div class="story-grid">{cards}</div></section>')
    content = "".join(sections) or '<section class="empty"><h2>A quiet week.</h2><p>No published stories are available. Check back after the next daily update.</p></section>'
    body = f'''<main id="main" class="home"><section class="intro"><div><p class="eyebrow"><span class="status-dot"></span> YOUR DAILY JAPANESE PRACTICE</p>
<h1>毎日、<br>少しずつ<span class="accent">。</span></h1><p class="intro-copy">A little Japanese, every day.<br>Read a story. Listen closely. Keep going.</p></div>
<aside class="intro-note"><span class="note-mark" aria-hidden="true">あ</span><p>Read it.<br>Then hear it.</p><span>Short news stories with furigana<br>and original NHK audio.</span><a class="pill" href="subscribe.html">Get the podcast <span aria-hidden="true">↗</span></a></aside></section>
<div class="library-bar"><div><strong>The reading room</strong><span>Latest daily edition · Up to 5 stories</span></div><span class="update">Updated {local:%d %b, %H:%M} Italy</span></div>
<div class="library-tools" hidden><div role="group" aria-label="Filter stories"><button class="active" data-filter="all" aria-pressed="true">All stories</button><button data-filter="unread" aria-pressed="false">Unread</button></div><button data-ruby-toggle aria-pressed="true">Furigana on</button></div>
<p id="filter-empty" class="empty" hidden>All caught up. A little practice goes a long way.</p>{content}
<aside class="end-note"><span aria-hidden="true">毎</span><div><h2>A small habit, every morning.</h2><p>Refreshes daily around 6:00 AM in Italy. Publication dates are shown as in Japan.<br>A new edition replaces the previous one. On days without new articles, the latest edition stays available.</p></div></aside></main>'''
    (output / "index.html").write_text(shell(TITLE, body, canonical=base), encoding="utf-8")


def write_story(story: dict, output: Path, base: str):
    audio = story["audio"]
    player = f'''<section class="player" aria-label="Listen to the article"><div class="player-heading"><strong>Listen to this story</strong><span>{duration_label(audio['duration'])} · NHK audio</span></div>
<audio controls preload="none" src="../{audio['path']}">Your browser does not support audio. <a href="../{audio['path']}">Download the MP3</a>.</audio>
<div class="player-options"><label>Speed <select id="audio-speed"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option></select></label><a href="../{audio['path']}" download>Download audio ↓</a></div></section>''' if audio else '<p class="audio-unavailable">NHK has not supplied audio for this story.</p>'
    image = f'<figure class="reader-image"><img src="../{story["image"]}" alt="{escape(story["title"], quote=True)}"><figcaption>Image: NHK / original source</figcaption></figure>' if story["image"] else ''
    body = f'''<main id="main" class="reader" data-reader-id="{story['id']}"><a class="back" href="../index.html">← All stories</a>
<div class="reader-meta"><span>NHK EASY</span><time datetime="{story['published']}">{japanese_date(datetime.fromisoformat(story['date']).date())}</time></div>
<h1>{story['title_html']}</h1>{player}
<div class="reader-tools" hidden><button data-ruby-toggle aria-pressed="true">Furigana on</button><div role="group" aria-label="Text size"><button data-font="smaller" aria-label="Smaller text">A−</button><button data-font="larger" aria-label="Larger text">A+</button></div></div>
{image}<div class="article-body">{story['body_html']}</div><div class="reader-end"><button class="pill" id="mark-read" hidden>Mark as read ✓</button><a class="text-link" href="{escape(story['url'], quote=True)}" target="_blank" rel="noopener">Read the original on NHK ↗</a></div>
<p class="source-note">出典：NHK NEWS WEB EASY · Publication date in Japan.<br>Text, image and audio are provided here for Japanese study.</p></main>'''
    (output / "stories" / f"{story['id']}.html").write_text(
        shell(story["title"] + " | まいにち", body, "../", urljoin(base, f"stories/{story['id']}.html")), encoding="utf-8")


def write_subscribe(output: Path, base: str):
    podcast = urljoin(base, "podcast.xml")
    reading = urljoin(base, "feed.xml")
    body = f'''<main id="main" class="subscribe"><p class="eyebrow">TAKE YOUR PRACTICE WITH YOU</p><h1>読む。聞く。<br><span class="accent">繰り返す。</span></h1>
<p class="lead">Read. Listen. Repeat.<br>The same stories, wherever your day takes you.</p>
<section class="subscription-card"><span class="section-number">01 / LISTEN</span><h2>Your daily listening, in Apple Podcasts.</h2><p>One short episode per article. Subscribe once to receive new recordings as the feed updates.</p>
<ol><li>Copy the podcast feed address below.</li><li>Open Apple Podcasts → Library → the more (•••) menu.</li><li>Choose <strong>Follow a Show by URL</strong>, paste the address, and follow.</li></ol>
<label class="feed-label" for="podcast-url">Podcast feed URL</label><div class="copy-field"><input id="podcast-url" value="{escape(podcast, quote=True)}" readonly spellcheck="false"><button data-copy="podcast-url" hidden>Copy</button></div><a class="text-link" href="podcast.xml">Open podcast RSS ↗</a>
<p class="fine-print">The feed holds only the latest daily edition. When a new edition arrives, older audio is removed from the website. Your app controls downloads and deletion of played audio. Episodes already downloaded may remain after leaving the feed.</p></section>
<section class="subscription-card"><span class="section-number">02 / READ</span><h2>A story in your RSS reader.</h2><p>Add this address to an RSS app such as NetNewsWire. Each entry includes the full Japanese text, furigana and image.</p>
<label class="feed-label" for="reading-url">Reading feed URL</label><div class="copy-field"><input id="reading-url" value="{escape(reading, quote=True)}" readonly spellcheck="false"><button data-copy="reading-url" hidden>Copy</button></div><a class="text-link" href="feed.xml">Open reading RSS ↗</a></section>
<p class="fine-print">Feeds refresh daily around 6:00 AM Europe/Rome, including daylight-saving changes. Your app may fetch updates later.</p><p id="copy-status" role="status"></p></main>'''
    (output / "subscribe.html").write_text(shell("Read & listen | まいにち", body, canonical=urljoin(base, "subscribe.html")), encoding="utf-8")


def xml_text(parent, tag: str, value: str, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    node.text = value
    return node


def write_feed(stories: list[dict], output: Path, base: str, now: datetime, podcast: bool):
    filename = "podcast.xml" if podcast else "feed.xml"
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    xml_text(channel, "title", "まいにち — Japanese listening" if podcast else "まいにち — Japanese reading")
    xml_text(channel, "link", base)
    xml_text(channel, "description", "NHK Easy stories for a daily Japanese study habit. Independent study feed, not affiliated with NHK. Latest daily edition only; updates at 06:00 Europe/Rome.")
    xml_text(channel, "language", "ja")
    xml_text(channel, "copyright", "Articles, images and audio: NHK and respective rights holders.")
    xml_text(channel, "lastBuildDate", format_datetime(now))
    ET.SubElement(channel, f"{{{ATOM}}}link", {"href": urljoin(base, filename), "rel": "self", "type": "application/rss+xml"})
    if podcast:
        xml_text(channel, f"{{{ITUNES}}}author", "まいにち · independent Japanese study")
        xml_text(channel, f"{{{ITUNES}}}explicit", "false")
        xml_text(channel, f"{{{ITUNES}}}type", "episodic")
        ET.SubElement(channel, f"{{{ITUNES}}}image", {"href": urljoin(base, "assets/podcast-cover.png")})
        cat = ET.SubElement(channel, f"{{{ITUNES}}}category", {"text": "Education"})
        ET.SubElement(cat, f"{{{ITUNES}}}category", {"text": "Language Learning"})
    for story in stories:
        audio = story["audio"]
        if podcast and not audio:
            continue
        item = ET.SubElement(channel, "item")
        xml_text(item, "title", story["title"])
        link = urljoin(base, f"stories/{story['id']}.html")
        xml_text(item, "link", link)
        # Source URL is stable even if this repository/site is renamed.
        xml_text(item, "guid", story["url"], isPermaLink="false")
        xml_text(item, "pubDate", format_datetime(datetime.fromisoformat(story["published"])))
        html = f'<p><a href="{escape(link, quote=True)}">Read and listen →</a></p>'
        if story["image"]:
            html += f'<p><img src="{escape(urljoin(base, story["image"]), quote=True)}" alt="{escape(story["title"], quote=True)}"></p>'
        html += story["body_html"] + f'<p>出典：<a href="{escape(story["url"], quote=True)}">NHK NEWS WEB EASY</a></p>'
        xml_text(item, "description", html)
        xml_text(item, f"{{{CONTENT}}}encoded", html)
        if podcast:
            ET.SubElement(item, "enclosure", {"url": urljoin(base, audio["path"]), "length": str(audio["bytes"]), "type": "audio/mpeg"})
            xml_text(item, f"{{{ITUNES}}}duration", str(audio["duration"]))
            xml_text(item, f"{{{ITUNES}}}explicit", "false")
            xml_text(item, f"{{{ITUNES}}}episodeType", "full")
    ET.indent(root)
    ET.ElementTree(root).write(output / filename, encoding="utf-8", xml_declaration=True)


def make_artwork(output: Path):
    image = Image.new("RGB", (1400, 1400), "#f5f3ed")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((110, 110, 330, 330), 45, fill="#244f3d")
    # A simple book glyph, shared with the site's favicon, without an NHK logo.
    draw.line([(160, 170), (220, 185), (280, 170), (280, 270), (220, 285), (160, 270), (160, 170)], fill="#f5f3ed", width=9)
    draw.line((220, 185, 220, 285), fill="#f5f3ed", width=7)
    for text, size, xy, color in [("MAINICHI", 130, (110, 455), "#212722"),
                                 ("READ.", 170, (100, 675), "#212722"),
                                 ("LISTEN.", 170, (100, 860), "#244f3d"),
                                 ("JAPANESE, EVERY DAY", 46, (110, 1220), "#244f3d")]:
        draw.text(xy, text, fill=color, font=ImageFont.load_default(size=size))
    image.save(output / "podcast-cover.png")
    image.crop((90, 90, 350, 350)).resize((180, 180)).save(output / "touch-icon.png")


def build_site(output: Path, base: str, now: datetime | None = None, client=None, audio_encoder=None):
    if urlparse(base).scheme != "https" or not urlparse(base).netloc or urlparse(base).query or urlparse(base).fragment:
        raise ValueError("The site's base URL must be an absolute HTTPS URL without query or fragment")
    base = base.rstrip("/") + "/"
    now = now or datetime.now(JST)
    client = client or NHKClient()
    rows = latest_rows(client.index(), now)
    if audio_encoder is None:
        token = client.playback_token() if any(audio_source(row) for row in rows) else ""
        audio_encoder = lambda row, directory: encode_audio(row, directory, token)
    # Require a clean output directory so expired stories/audio can never leak into a deployment.
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Output directory must be empty; build into a fresh directory")
    (output / "stories").mkdir()
    (output / "images").mkdir()
    shutil.copytree(ROOT / "web", output / "assets")
    make_artwork(output / "assets")
    stories = []
    for row in rows:
        LOG.info("Preparing %s", row["news_id"])
        article = client.article(row)
        picture = f"images/{article.news_id}.jpg" if article.image else None
        if picture:
            (output / picture).write_bytes(article.image)
        # Any unexpected fetch/conversion failure fails the build: Pages keeps the previous
        # complete deployment, rather than silently dropping episodes from subscribers.
        audio = audio_encoder(row, output / "audio")
        if audio is None:
            LOG.warning("NHK supplies no audio for %s; publishing text only", article.news_id)
        body = clean_html(article.body_html)
        excerpt = plain_text(body)
        story = {"id": article.news_id, "title": article.title,
                 "title_html": clean_html(article.title_html), "published": article.published.isoformat(),
                 "date": article.published.date().isoformat(), "url": article.url,
                 "body_html": body, "excerpt": excerpt[:105] + ("…" if len(excerpt) > 105 else ""),
                 "image": picture, "audio": audio}
        stories.append(story)
        write_story(story, output, base)
    write_home(stories, output, now, base)
    write_subscribe(output, base)
    write_feed(stories, output, base, now, podcast=False)
    write_feed(stories, output, base, now, podcast=True)
    (output / ".nojekyll").touch()
    (output / "build.json").write_text(json.dumps({"updated": now.isoformat(), "stories": len(stories),
        "episodes": sum(bool(s["audio"]) for s in stories), "retention": "latest publication day only",
        "schedule": "06:00 Europe/Rome"}, indent=2) + "\n")
    total = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    if total > 900 * 1024 * 1024:
        raise ValueError("Site exceeds the safe GitHub Pages size limit")
    LOG.info("Built %s stories, %s episodes, %.1f MiB", len(stories), sum(bool(s["audio"]) for s in stories), total / 1024**2)
    return stories


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("_site"))
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    build_site(args.output, args.base_url)
