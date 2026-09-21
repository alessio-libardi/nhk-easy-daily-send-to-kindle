# まいにち — NHK Easy reading, listening & Kindle

## Website and podcast

The **Daily reading room & podcast** workflow publishes a mobile-friendly GitHub Pages website, a full-text reading RSS feed, and an audio podcast. It starts every day at **06:00 Europe/Rome**, automatically following Italian daylight-saving time. GitHub schedules and builds can run late; publication is not guaranteed exactly at 06:00.

Only the **latest available Japanese publication day**, with at most **five stories**, is hosted. A new edition replaces all older pages, images and recordings. There is no archive. At 06:00 in Italy, NHK generally has not published that Japanese evening's stories yet, so the website usually shows the preceding publication day. Weekends and holidays keep the latest edition. Source publication dates remain visible; old articles are never relabeled as today's.

Each story has its full text, image, furigana toggle, text-size controls, an audio player with playback speed, and a link to NHK. Read/unread marks and reading preferences are stored only in that browser. They do not sync across devices. JavaScript is optional for reading and standard audio playback.

- **Website:** `https://alessio-libardi.github.io/nhk-easy-daily-send-to-kindle/`
- **Reading RSS:** append `feed.xml` to the website URL.
- **Podcast RSS:** append `podcast.xml` to the website URL.
- **Apple Podcasts:** Library → more (•••) → Follow a Show by URL → paste the podcast feed URL. The site's **Listen** page includes copy buttons and instructions. No submission to Apple's public directory is needed.

The podcast uses NHK's article recordings, converted from its public HLS player stream to mono 64 kbps MP3. It follows the official player's anonymous overseas session and temporary playback-token exchange. No NHK account or saved credentials are required, and playback tokens are never included in the website or logs. Each article is one episode. Episode GUIDs use the original NHK URL and never change on a rebuild or repository rename. Enclosures have unique URLs, MIME types and exact file sizes. If NHK has no recording, the article is available to read but is omitted from the podcast. Unexpected download or conversion failures stop deployment and preserve the previous complete site.

**Daily replacement also removes the previous edition's hosted MP3s.** Download episodes before they leave the feed if you want to keep them. Removing them here does not delete files already downloaded to your phone. Apple Podcasts controls automatic downloads and deletion of played episodes separately.

### Pages setup and maintenance

1. In **Settings → Pages → Build and deployment**, set **Source** to **GitHub Actions**.
2. Run **Actions → Daily reading room & podcast → Run workflow**. Source-code changes also trigger deployment; pull requests run offline tests only.
3. Open the deployment URL shown in the workflow's `github-pages` environment.

No new credentials or secrets are required. The Pages workflow has no access to Gmail/Kindle secrets and does not send email. It builds into a fresh `_site` directory, so older editions cannot accumulate in the published site or Git history. The temporary Pages upload expires after one day; that does **not** expire the live website.

When renaming the repository, rerun the Pages workflow. It gets the current base URL from GitHub Pages automatically; page links and feed URLs are regenerated. Subscribers must update the feed address if the site's URL changes. Existing episode GUIDs remain stable.

To build locally, install Python dependencies as below and install `ffmpeg` (which includes `ffprobe`), then run:

```bash
python build_site.py --base-url https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/
python -m http.server 8000 --directory _site
```

Use an empty output directory for every build (`--output` can select a different directory). The generator deliberately refuses to reuse one containing an older edition.

The website is a public, independent study project, not an official NHK site. Text, images and audio belong to NHK and their respective rights holders.

## Kindle delivery

Every day, GitHub Actions fetches up to **five articles published that day in Japan** from NHK NEWS WEB EASY, creates one Japanese EPUB, saves it as a downloadable Actions artifact, and sends it to your Kindle through Gmail.

Example: **`NHKやさしいニュース - 2026年9月10日.epub`**.

The EPUB has a Japanese title page and table of contents. Each article is a separate chapter with its title, lead image, full text, publication date, and original source link. Furigana are preserved. Images are embedded, resized, and compressed for offline reading. No AI service or API key is needed.

## One-time setup

### 1. Configure Gmail and your Kindle

1. Enable [Google 2-Step Verification](https://myaccount.google.com/security).
2. Create a dedicated [Google app password](https://myaccount.google.com/apppasswords), for example named `NHK Easy Kindle`. Use this app password, not your normal Google password. See [Google's instructions](https://support.google.com/accounts/answer/185833) if the option is unavailable on your account.
3. In Amazon **Manage Your Content and Devices → Preferences → Personal Document Settings**, find the Kindle's `@kindle.com` address.
4. In the same Amazon settings, add your Gmail address to **Approved Personal Document E-mail List**. Amazon may ask you to verify an individual submission by email.

### 2. Add these three repository secrets

Open [Settings → Secrets and variables → Actions](https://github.com/alessio-libardi/nhk-easy-daily-send-to-kindle/settings/secrets/actions), then **New repository secret** for each:

| Name | Value |
| --- | --- |
| `GMAIL_ADDRESS` | Your full Gmail address, approved in Amazon's settings |
| `GMAIL_APP_PASSWORD` | The Google app password created above |
| `KINDLE_EMAIL` | Your Kindle's delivery address, ending in `@kindle.com` |

The workflow receives its GitHub token automatically. Do not add a personal GitHub token. Credentials are supplied only to the delivery step and are not put in the EPUB or delivery history.

### 3. Run a first edition

Open [Actions → NHK Easy to Kindle](https://github.com/alessio-libardi/nhk-easy-daily-send-to-kindle/actions/workflows/daily.yml) → **Run workflow**:

- Branch: `main`.
- Date: **`latest`** for the latest available publication day, including at weekends. Alternatively use `today` or an exact date such as `2026-09-10`.
- Enable **Email this edition to the Kindle** to send it. Leave this unchecked to preview an artifact without sending.

When the run finishes, its **Artifacts** section contains `nhk-easy-YYYY-MM-DD`. Download the ZIP and open the EPUB inside. Keep the Kindle connected to Wi-Fi for Amazon to deliver it. A successful email step means **Gmail accepted the message**; Amazon's conversion and arrival on the device happen afterward.

## Daily behavior

- Runs at **13:45 UTC / 22:45 Japan time**, currently 15:45 in Italy during summer and 14:45 during winter. GitHub schedules can run late and are not an exact-time delivery guarantee.
- Uses NHK's publication date in Japan, not the runner's UTC date.
- Includes **at most five** published, visible articles, in NHK's priority order. If there are four, the EPUB contains four. It never pads an edition with yesterday's articles.
- Days without new articles, such as many weekends and holidays, finish successfully without generating or sending anything.
- If an article or advertised image cannot be fetched, the build fails instead of silently delivering an incomplete edition. If NHK supplies no image for an article, that chapter contains its title and text.
- Artifacts expire after **three days**. Downloaded EPUBs are not committed to Git. This repository is public, so its downloadable artifacts should not contain personal data. Use a private repository if you want the generated files to be private too.
- Each successful Gmail submission adds a small receipt to [`.state/deliveries.json`](.state/deliveries.json), created automatically on the first send. This records dates, article IDs, and file hashes, with no email addresses or credentials.
- A publication date is sent **once**, even if you rerun the workflow or ask for it manually. Later edits or additions to that day's news do not trigger another send.
- Code pushes run tests and build a preview of the latest edition, **without emailing it**. Pull requests run offline tests only. Delivery only runs from `main` on a schedule or explicit manual send.

GitHub can disable schedules in public repositories after 60 days without repository activity. Check the Actions page if daily runs stop. Successful delivery-receipt commits normally keep this repository active, but this is not a substitute for checking failures. See [GitHub's scheduling documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## NHK access

The generator uses NHK's current `https://news.web.nhk/news/easy/news-list.json` index, then retrieves each article's full HTML. If the index returns 401, it establishes NHK's normal anonymous **overseas visitor** session. This configuration is intended for use from outside Japan. It does not require an NHK account or a saved browser cookie. If NHK changes its access flow or article structure, the workflow fails with an error rather than substituting a truncated preview or unrelated source.

Articles and images remain the property of NHK and their respective rights holders. This tool is intended for your personal reading. Generated editions include attribution and source links.

## Delivery limits and troubleshooting

- **Missing settings:** add the three email secrets above; the EPUB artifact is still available even when the delivery step fails.
- **Gmail login failed:** check the app password and 2-Step Verification. Google can revoke app passwords when the account password changes.
- **Gmail accepted but nothing arrives:** check Amazon's approved sender list, the recipient address, verification emails, and Kindle connectivity.
- **GitHub refuses the receipt commit:** the delivery job needs `contents: write`. Repository or organization rules that prohibit direct writes to `main` can block it. Resolve those rules before relying on daily delivery.
- **Email succeeded but the receipt failed, or SMTP disconnected ambiguously:** check Gmail Sent before rerunning. SMTP and a GitHub commit cannot be one atomic operation, so a crash between them can cause a duplicate on retry. If Gmail accepted it, add that date to `.state/deliveries.json` before retrying, preserving all existing dates. The minimum marker is `"2026-09-10": {"manually_confirmed_sent": true}` inside the JSON object.
- To deliberately resend an edition, remove only its date entry from the receipt file and manually run that date again with email enabled.
- Send to Kindle does **not** replace older editions, create collections, place files in a chosen collection, or delete files already on your Kindle. Artifact expiry only removes the GitHub download. Earlier Kindle editions still need to be removed manually.

## Run locally

Requires Python 3.13 (the Actions workflow installs it automatically).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python nhk_easy.py --date latest
```

The EPUB and `edition.json` appear in `dist/`. For a publication date still present in NHK's index:

```bash
python nhk_easy.py --date 2026-09-10
```

Email delivery is designed to run through Actions, where the credentials and repository token are supplied securely. Change the cron expression in [the workflow](.github/workflows/daily.yml) if you prefer another UTC schedule.
