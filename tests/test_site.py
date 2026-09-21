from datetime import datetime
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import pytest
import requests
import yaml

from build_site import (CONTENT, ITUNES, ROME, audio_source, build_site,
                        encode_audio, latest_rows, plain_text)
from nhk_easy import JST, NHKClient
from test_pipeline import article, row

BASE = 'https://reader.github.io/japanese/'
NOW = datetime(2026, 9, 11, 6, tzinfo=ROME)


def test_latest_edition_only_with_weekend_and_future_publication():
    index = {'2026-09-10': [row(i) for i in range(1, 8)],
             '2026-09-09': [row(99, news_prearranged_time='2026-09-09 20:00:00')],
             '2026-09-11': [row(9, news_prearranged_time='2026-09-11 20:00:00',
                                  news_publication_time='2026-09-11 20:15:00')]}
    assert [r['news_id'] for r in latest_rows(index, NOW)] == [row(i)['news_id'] for i in range(1, 6)]
    # The next morning only the newer edition is retained.
    assert [r['news_id'] for r in latest_rows(index, datetime(2026, 9, 12, 6, tzinfo=ROME))] == [row(9)['news_id']]


def test_no_available_articles_is_failure_not_empty_replacement():
    with pytest.raises(ValueError, match='No published'):
        latest_rows({'2026-09-10': []}, NOW)


def test_audio_source_uses_official_player_mapping_and_rejects_paths():
    assert audio_source({'news_easy_voice_uri': 'abc_123.m4a'}) == 'https://media.vd.st.nhk/news/easy_audio/abc_123/index.m3u8'
    assert audio_source({}) is None
    with pytest.raises(ValueError):
        audio_source({'news_easy_voice_uri': '../other.m4a'})


def test_stale_image_uses_current_original_article_image():
    client = NHKClient()
    article_response = MagicMock(text='<div id="js-article-body"><p>' + '記事です。' * 20 + '</p></div>')
    original_response = MagicMock(text='<meta property="og:image" content="https://imgu.web.nhk/replacement.jpg">')
    missing = requests.HTTPError(response=MagicMock(status_code=404))
    with patch.object(client.session, 'get', side_effect=[article_response, original_response]):
        with patch.object(client, 'image', side_effect=[missing, b'new-image']) as images:
            result = client.article(row(news_web_image_uri='https://news.web.nhk/old.jpg',
                                        news_web_url='https://news.web.nhk/newsweb/na/article'))
            assert result.image == b'new-image'
            assert images.call_args.args[0] == 'https://imgu.web.nhk/replacement.jpg'


def test_image_server_failure_is_not_treated_as_stale_url():
    client = NHKClient()
    response = MagicMock(text='<div id="js-article-body"><p>' + '記事です。' * 20 + '</p></div>')
    with patch.object(client.session, 'get', return_value=response) as get:
        with patch.object(client, 'image', side_effect=requests.HTTPError(response=MagicMock(status_code=503))):
            with pytest.raises(requests.HTTPError):
                client.article(row(news_web_image_uri='https://news.web.nhk/old.jpg',
                                   news_web_url='https://news.web.nhk/newsweb/na/article'))
            assert get.call_count == 1


def test_bad_audio_cannot_leave_partial_mp3(tmp_path):
    path = tmp_path / (row()['news_id'] + '.mp3')
    path.write_bytes(b'partial')
    with patch('build_site.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'ffmpeg')):
        with pytest.raises(subprocess.CalledProcessError):
            encode_audio(row(news_easy_voice_uri='abc.m4a'), tmp_path)
    assert not path.exists()


def fake_audio(row, directory):
    directory.mkdir(parents=True, exist_ok=True)
    name = row['news_id'] + '.mp3'
    (directory / name).write_bytes(b'audio-fixture')
    return {'path': 'audio/' + name, 'duration': 85, 'bytes': 13}


def client_fixture():
    client = MagicMock()
    client.index.return_value = {'2026-09-10': [row(1), row(2)]}
    a1, a2 = article(1), article(2)
    a1.news_id, a2.news_id = row(1)['news_id'], row(2)['news_id']
    a1.title = '日本 & 世界 <ニュース>'
    client.article.side_effect = [a1, a2]
    return client


def test_site_feeds_have_matching_audio_sizes_sources_and_valid_links(tmp_path):
    stories = build_site(tmp_path, BASE, NOW, client_fixture(), fake_audio)
    assert len(stories) == 2
    podcast = ET.parse(tmp_path / 'podcast.xml')
    items = podcast.findall('./channel/item')
    assert len(items) == 2
    assert items[0].findtext('title') == '日本 & 世界 <ニュース>'
    assert len({i.findtext('guid') for i in items}) == 2
    for item in items:
        enclosure = item.find('enclosure').attrib
        audio_path = tmp_path / enclosure['url'].removeprefix(BASE)
        assert audio_path.stat().st_size == int(enclosure['length'])
        assert enclosure['type'] == 'audio/mpeg'
        assert item.findtext(f'{{{ITUNES}}}duration') == '85'
        assert '<ruby>' in item.findtext(f'{{{CONTENT}}}encoded')
        assert item.findtext('link').startswith(BASE + 'stories/')
        assert item.find('guid').attrib['isPermaLink'] == 'false'
    reading = ET.parse(tmp_path / 'feed.xml')
    assert len(reading.findall('./channel/item')) == 2
    assert not reading.findall('.//enclosure')
    assert (tmp_path / 'assets/podcast-cover.png').is_file()
    # Check every internal page/asset link, including relative links from article pages.
    for html in tmp_path.rglob('*.html'):
        soup = BeautifulSoup(html.read_text(), 'html.parser')
        for element in soup.select('[src], [href]'):
            target = element.get('src') or element.get('href')
            if target and not target.startswith(('https:', '#')):
                assert (html.parent / target).is_file(), (html, target)


def test_missing_audio_is_honest_and_excluded_from_podcast(tmp_path):
    build_site(tmp_path, BASE, NOW, client_fixture(), lambda *_: None)
    assert not ET.parse(tmp_path / 'podcast.xml').findall('.//item')
    assert len(ET.parse(tmp_path / 'feed.xml').findall('.//item')) == 2
    page = next((tmp_path / 'stories').glob('*.html')).read_text()
    assert 'NHK has not supplied audio' in page
    assert '<audio' not in page


def test_network_failure_aborts_build_instead_of_silently_removing_episode(tmp_path):
    with pytest.raises(RuntimeError, match='fetch failed'):
        build_site(tmp_path, BASE, NOW, client_fixture(), MagicMock(side_effect=RuntimeError('fetch failed')))
    assert not (tmp_path / 'podcast.xml').exists()


def test_old_output_must_not_be_reused_and_guid_survives_repo_rename(tmp_path):
    first, second = tmp_path / 'one', tmp_path / 'two'
    build_site(first, BASE, NOW, client_fixture(), fake_audio)
    with pytest.raises(ValueError, match='empty'):
        build_site(first, BASE, NOW, client_fixture(), fake_audio)
    build_site(second, 'https://reader.github.io/new-name/', NOW, client_fixture(), fake_audio)
    before = ET.parse(first / 'podcast.xml').find('./channel/item')
    after = ET.parse(second / 'podcast.xml').find('./channel/item')
    assert before.findtext('guid') == after.findtext('guid')
    assert before.findtext('link') != after.findtext('link')


def test_excerpt_omits_duplicate_ruby_readings():
    assert plain_text('<p><ruby>日本<rt>にほん</rt></ruby>のニュース</p>') == '日本のニュース'


def test_daily_schedule_is_six_in_italy_and_secrets_are_not_in_pages_job():
    # BaseLoader prevents YAML 1.1 interpreting GitHub's "on" key as a boolean.
    workflow = yaml.load(Path('.github/workflows/pages.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['schedule'] == [{'cron': '0 6 * * *', 'timezone': 'Europe/Rome'}]
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['jobs']['deploy']['permissions'] == {'pages': 'write', 'id-token': 'write'}
    assert 'secrets.' not in Path('.github/workflows/pages.yml').read_text()
    # Italy's UTC offset changes; the configured wall-clock time does not.
    assert datetime(2026, 1, 12, 6, tzinfo=ROME).utcoffset().total_seconds() == 3600
    assert datetime(2026, 7, 12, 6, tzinfo=ROME).utcoffset().total_seconds() == 7200
