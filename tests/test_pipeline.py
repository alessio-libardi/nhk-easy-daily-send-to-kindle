import base64
from datetime import date, datetime
from email import policy
from email.parser import BytesParser
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image
import pytest
import yaml

from nhk_easy import (Article, JST, NHKClient, build_epub, clean_html, japanese_date,
                      main, parse_index, select_articles)
from send_to_kindle import GitHubLedger, create_message, read_edition, send_edition

DAY = date(2026, 9, 10)
NOW = datetime(2026, 9, 10, 23, tzinfo=JST)


def row(number=1, **overrides):
    return {
        'news_id': f'20260910test{number}', 'title': f'ニュース {number}',
        'news_prearranged_time': '2026-09-10 20:00:00',
        'news_publication_time': '2026-09-10 20:15:00',
        'top_priority_number': number, 'news_display_flag': True,
        'news_publication_status': True, **overrides,
    }


def picture():
    file = BytesIO()
    Image.new('RGB', (20, 10), 'white').save(file, 'JPEG')
    return file.getvalue()


def article(number=1):
    return Article(str(number), f'記事{number}', '<ruby>日本<rt>にほん</rt></ruby>のニュース',
                   NOW, f'https://news.web.nhk/news/easy/{number}/{number}.html',
                   '<p>最初の段落です。</p><p><ruby>次<rt>つぎ</rt></ruby>の段落です。</p>', picture())


def edition(tmp_path):
    path = build_epub([article()], DAY, tmp_path)
    manifest = {'date': DAY.isoformat(), 'title': path.stem, 'file': path.name,
                'articles': [{'id': '1'}]}
    (tmp_path / 'edition.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest, path


def test_feed_day_limit_order_duplicates_and_future():
    rows = [row(i) for i in range(8, 0, -1)] + [row(1)]
    rows += [row(0, news_publication_time='2026-09-11 00:00:00')]
    index = parse_index([{DAY.isoformat(): rows, '2026-09-09': [row(99)]}])
    selected = select_articles(index, DAY, NOW)
    assert [r['news_id'] for r in selected] == [row(i)['news_id'] for i in range(1, 6)]
    assert select_articles(index, date(2026, 9, 12), NOW) == []


def test_hidden_articles_are_not_sent():
    index = {DAY.isoformat(): [row(1, news_display_flag=False),
                              row(2, news_publication_status=False), row(3)]}
    assert [r['news_id'] for r in select_articles(index, DAY, NOW)] == [row(3)['news_id']]


@pytest.mark.parametrize('payload', [[], {}, {'error': 'missing_token'}, [{'bad-date': []}]])
def test_feed_format_changes_fail_loudly(payload):
    with pytest.raises(ValueError):
        parse_index(payload)


def test_japan_day_boundary_and_filename():
    utc = datetime.fromisoformat('2026-09-10T15:01:00+00:00')
    assert utc.astimezone(JST).date() == date(2026, 9, 11)
    assert japanese_date(DAY) == '2026年9月10日'


def test_body_sanitization_preserves_readings_and_paragraphs():
    output = clean_html('<p class="color4" onclick="bad()">'
                        '<a href="javascript:x">日本</a><ruby>語<rt>ご</rt></ruby>'
                        '<script>bad()</script><img src="external.jpg"/></p><p>次です。</p>')
    assert output == '<p>日本<ruby>語<rt>ご</rt></ruby></p><p>次です。</p>'


def test_truncated_or_changed_article_is_rejected():
    client = NHKClient()
    response = MagicMock(text='<html><p>Preview text only</p></html>')
    with patch.object(client.session, 'get', return_value=response):
        with pytest.raises(ValueError, match='Full article body missing'):
            client.article(row())


def test_anonymous_session_is_only_initialized_on_401():
    client = NHKClient()
    response = MagicMock(status_code=200)
    response.json.return_value = [{DAY.isoformat(): [row()]}]
    with patch.object(client.session, 'get', side_effect=[MagicMock(status_code=401), MagicMock(), response]) as get:
        assert DAY.isoformat() in client.index()
        assert get.call_args_list[1].kwargs['params']['profileType'] == 'abroad'
        assert get.call_count == 3


def test_all_chapters_images_toc_spine_and_xml_are_valid(tmp_path):
    path = build_epub([article(i) for i in range(1, 6)], DAY, tmp_path)
    assert path.name == 'NHKやさしいニュース - 2026年9月10日.epub'
    with ZipFile(path) as book:
        assert book.namelist()[0] == 'mimetype'
        assert book.getinfo('mimetype').compress_type == 0
        assert book.read('mimetype') == b'application/epub+zip'
        for name in book.namelist():
            if name.endswith(('.xml', '.xhtml', '.opf', '.ncx')):
                ET.fromstring(book.read(name))
        names = book.namelist()
        assert len([n for n in names if n.endswith('.jpg')]) == 5
        assert len([n for n in names if '/story-' in n and n.endswith('.xhtml')]) == 5
        ns = {'x': 'http://www.w3.org/1999/xhtml', 'o': 'http://www.idpf.org/2007/opf',
              'd': 'http://purl.org/dc/elements/1.1/'}
        nav = ET.fromstring(book.read('EPUB/nav.xhtml'))
        assert len(nav.findall('.//x:nav/x:ol/x:li', ns)) == 5
        package = ET.fromstring(book.read('EPUB/content.opf'))
        assert package.find('.//d:language', ns).text == 'ja'
        assert len(package.findall('.//o:spine/o:itemref', ns)) == 7
        for i in range(1, 6):
            chapter = ET.fromstring(book.read(f'EPUB/story-{i}.xhtml'))
            assert chapter.find('.//x:ruby/x:rt', ns) is not None
            assert chapter.find('.//x:img', ns).attrib['src'] == f'images/story-{i}.jpg'


@pytest.mark.parametrize('count', [0, 6])
def test_empty_or_oversized_editions_rejected(tmp_path, count):
    with pytest.raises(ValueError):
        build_epub([article()] * count, DAY, tmp_path)


def test_no_news_creates_no_epub_or_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['nhk_easy.py', '--date', '2026-09-12', '--output', str(tmp_path)])
    with patch('nhk_easy.NHKClient.index', return_value={DAY.isoformat(): [row()]}):
        main()
    assert list(tmp_path.iterdir()) == []


def test_email_attachment_preserves_japanese_filename(tmp_path):
    manifest, path = edition(tmp_path)
    message = create_message(manifest, path, 'sender@gmail.com', 'reader@kindle.com')
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    attachment = list(parsed.iter_attachments())[0]
    assert attachment.get_filename() == path.name
    assert attachment.get_content_type() == 'application/epub+zip'
    assert attachment.get_payload(decode=True) == path.read_bytes()
    assert str(parsed['Subject']) == path.stem


def test_manifest_cannot_escape_artifact_directory(tmp_path):
    manifest, _ = edition(tmp_path)
    manifest['file'] = '../elsewhere.epub'
    (tmp_path / 'edition.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        read_edition(tmp_path)


def test_successful_delivery_records_receipt_without_personal_data(tmp_path):
    manifest, path = edition(tmp_path)
    ledger = MagicMock()
    ledger.load.return_value = {}
    with patch('send_to_kindle.smtplib.SMTP_SSL') as smtp:
        smtp.return_value.send_message.return_value = {}
        assert send_edition(manifest, path, 'sender@gmail.com', 'abcd efgh', 'reader@kindle.com', ledger)
        smtp.return_value.login.assert_called_once_with('sender@gmail.com', 'abcdefgh')
        smtp.return_value.send_message.assert_called_once()
        receipt = ledger.save.call_args.args[0]
        assert receipt[DAY.isoformat()]['article_ids'] == ['1']
        assert 'sender@gmail.com' not in json.dumps(receipt)
        assert 'reader@kindle.com' not in json.dumps(receipt)


def test_rerun_does_not_send_twice(tmp_path):
    manifest, path = edition(tmp_path)
    ledger = MagicMock()
    ledger.load.return_value = {DAY.isoformat(): {'article_ids': ['1']}}
    with patch('send_to_kindle.smtplib.SMTP_SSL') as smtp:
        assert not send_edition(manifest, path, 'sender@gmail.com', 'password', 'reader@kindle.com', ledger)
        smtp.assert_not_called()
        ledger.save.assert_not_called()


def test_failed_smtp_does_not_mark_edition_sent(tmp_path):
    manifest, path = edition(tmp_path)
    ledger = MagicMock()
    ledger.load.return_value = {}
    with patch('send_to_kindle.smtplib.SMTP_SSL') as smtp:
        smtp.return_value.send_message.side_effect = OSError('connection failed')
        with pytest.raises(OSError):
            send_edition(manifest, path, 'sender@gmail.com', 'password', 'reader@kindle.com', ledger)
        ledger.save.assert_not_called()


def test_receipt_failure_explains_email_was_already_accepted(tmp_path):
    manifest, path = edition(tmp_path)
    ledger = MagicMock()
    ledger.load.return_value = {}
    ledger.save.side_effect = RuntimeError('GitHub unavailable')
    with patch('send_to_kindle.smtplib.SMTP_SSL') as smtp:
        smtp.return_value.send_message.return_value = {}
        with pytest.raises(RuntimeError, match='Gmail accepted the email'):
            send_edition(manifest, path, 'sender@gmail.com', 'password', 'reader@kindle.com', ledger)


def test_github_ledger_uses_current_sha_and_never_overwrites_blindly():
    ledger = GitHubLedger('owner/repo', 'token', 'main')
    old = {'2026-09-09': {'article_ids': ['previous']}}
    with patch.object(ledger, 'request', return_value={'sha': 'old-sha', 'content': base64.b64encode(json.dumps(old).encode()).decode()}):
        assert ledger.load() == old
    with patch.object(ledger, 'request', return_value={'content': {'sha': 'new-sha'}}) as call:
        ledger.save(old)
        assert call.call_args.args[1]['sha'] == 'old-sha'
        assert ledger.sha == 'new-sha'


def test_ledger_auth_failure_is_not_treated_as_empty_history():
    ledger = GitHubLedger('owner/repo', 'token', 'main')
    with patch.object(ledger, 'request', side_effect=HTTPError('https://example.test', 403, 'Forbidden', {}, None)):
        with pytest.raises(RuntimeError, match='403'):
            ledger.load()


def test_workflow_sends_only_on_schedule_or_explicit_manual_request():
    workflow = yaml.safe_load(Path('.github/workflows/daily.yml').read_text())
    jobs = workflow['jobs']
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'inputs.send' in jobs['deliver']['if']
    assert "github.ref == 'refs/heads/main'" in jobs['deliver']['if']
    assert jobs['deliver']['permissions']['contents'] == 'write'
    assert jobs['test'].get('permissions', {}).get('contents') != 'write'
