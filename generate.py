#!/usr/bin/env python3
"""Generate an XMLTV EPG for the Italian news channel "Sky TG24".

Sky publishes no XMLTV feed, and unlike the sister repos this channel cannot use Tivù la Guida:
that is the free-to-air DTT guide and it lists 70 channels, none of them Sky. So the source here is
the HTML schedule at guidatv.org, which carries what these repos exist for — a per-programme cover
image, a category and a description — and which the IPTV provider's own feed does not supply
usefully for Sky strands.

## Times are Italian, and stay Italian

The channel is broadcast from Italy (Europe/Rome) and watched from Cyprus (Europe/Nicosia), an hour
ahead for most of the year. The conversion is deliberately NOT done here. XMLTV timestamps carry
their own UTC offset, so every time is parsed as Rome local and written with Rome's offset —
+0100 in winter, +0200 in summer — and the player converts to wherever it is. Writing Cyprus times
instead would be wrong twice over: it would break for anyone else, and it would break at each of
the four moments a year when the two zones change clocks on different dates.

ZoneInfo resolves the offset from the wall time, so this is correct across a DST boundary without
a table of dates to maintain.

## What the source gives, and what it does not

guidatv.org addresses days by name — ieri, today, domani, dopodomani — so the guide reaches two
days ahead and no further. The sister repos ask for seven. There is no paging parameter to raise
it; this is the ceiling of the source, not a setting.

Today's page also TRIMS what has already finished: fetched at 11:00 it began at 11:00, leaving a
five-hour hole behind it that no later fetch will ever fill. That matters for this channel in
particular — it is the one recorded round the clock on the DVR, and a recording is named from the
guide covering the moment it was made. So each run merges into the file already committed rather
than replacing it, and the day accumulates. Anything that finished more than RETAIN_DAYS ago is
dropped, so the file does not grow without end.

Stdlib only — no pip install, runs on a stock GitHub Actions runner.
"""

import html
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

# --- config -------------------------------------------------------------------------------------
CHANNEL_ID = "SkyTG24.it"             # XMLTV id → paste this exact string as the app's tvg-id
CHANNEL_NAME = "Sky TG24"
CHANNEL_LOGO = ("https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/"
                "sky-tg24-it.png")
ROME = ZoneInfo("Europe/Rome")
BASE = "https://guidatv.org/canali/sky-tg24"
# The only days the site exposes. Yesterday is included so a programme that began before midnight
# and runs into today is not lost — its card lives on yesterday's page, not today's.
PAGES = [("ieri", -1), ("", 0), ("domani", 1), ("dopodomani", 2)]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
OUT = "skytg24.xml"
DEFAULT_MINUTES = 30                  # when the card names no duration and nothing follows it
RETAIN_DAYS = 3                       # how much already-broadcast guide to keep when merging

# --- the page's shape ---------------------------------------------------------------------------
# One <h3 class="hour"> per programme, each followed by its card. Measured 47 of each on a sample
# day, pairing exactly, which is what makes splitting on the headings safe.
HOUR = re.compile(r'<h3[^>]*class="[^"]*\bhour\b[^"]*"[^>]*>\s*(\d{1,2})[:.](\d{2})\s*</h3>')
TITLE = re.compile(r'<h2[^>]*class="[^"]*\bcard-title\b[^"]*"[^>]*>(.*?)</h2>', re.S)
MINUTES = re.compile(r'<p[^>]*class="[^"]*\bsubtitle\b[^"]*"[^>]*>\s*(\d+)\s*min', re.S)
CATEGORY = re.compile(r'<span[^>]*class="[^"]*\btext-primary\b[^"]*"[^>]*>([^<]{2,40})</span>')
DESC = re.compile(r'<p[^>]*class="[^"]*\bprogram-description\b[^"]*"[^>]*>(.*?)</p>', re.S)
# Covers go through Next.js's image proxy; the real file is the url= parameter and it is a plain,
# non-expiring URL. The proxy form is useless to anyone but guidatv.org, so it is decoded here.
IMAGE = re.compile(r'src="/_next/image\?url=([^&"]+)')


class Unreachable(Exception):
    """The source did not answer, or answered with something that is not the schedule."""


def fetch(path: str) -> str:
    """One day's HTML.

    Raises Unreachable rather than returning junk. The check is that we are still ON the page we
    asked for and that it contains this channel's schedule markup — not that the status was 200.
    A site that redirects to a friendly error page answers 200 from the wrong URL, and a
    status-code check waves that straight through.
    """
    url = f"{BASE}/{path}" if path else BASE
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "it-IT,it;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            page = r.read().decode("utf-8", errors="replace")
            landed = r.geturl()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Unreachable(f"{url}: {exc}") from exc

    if "/canali/sky-tg24" not in urllib.parse.urlparse(landed).path:
        raise Unreachable(f"{url}: diverted to {landed}")
    if 'data-testid="channel-program-card"' not in page:
        raise Unreachable(f"{url}: answered without any programme card")
    return page


def text(raw: str) -> str:
    """Markup fragment → plain text."""
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).replace("\xa0", " ").strip()


def parse_day(page: str, day: datetime) -> list:
    """One page → [(start, stop, title, desc, category, icon)], times in Europe/Rome.

    `day` is the date the page is FOR. A listing runs past midnight, so when a time goes backwards
    the date is rolled forward; without that an overnight bulletin lands eighteen hours before the
    programme in front of it and the whole evening sorts wrong.
    """
    marks = list(HOUR.finditer(page))
    out = []
    for i, mark in enumerate(marks):
        chunk = page[mark.end():marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        title_match = TITLE.search(chunk)
        if not title_match:
            continue
        title = text(title_match.group(1))
        if not title:
            continue

        hour, minute = int(mark.group(1)), int(mark.group(2))
        start = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if out and start <= out[-1][0]:
            start += timedelta(days=1)

        minutes = MINUTES.search(chunk)
        stop = start + timedelta(minutes=int(minutes.group(1))) if minutes else None

        desc_match = DESC.search(chunk)
        desc = text(desc_match.group(1)) if desc_match else ""
        cat_match = CATEGORY.search(chunk)
        category = text(cat_match.group(1)) if cat_match else ""
        # "Live" is a badge on whatever is on air at fetch time, not a genre — and it would be
        # baked into a file read hours later.
        if category.lower() in {"live", "on air"}:
            category = ""

        icon = ""
        for candidate in IMAGE.findall(chunk):
            url = html.unescape(urllib.parse.unquote(candidate))
            if "img-guidatv.org" in url and "/loghi/" not in url:
                icon = url
                break

        out.append([start, stop, title, desc, category, icon])

    # Fill missing stops from the next start, so the guide has no holes for a player to render as
    # dead air. The last programme of a page keeps whatever it has.
    for i, row in enumerate(out):
        if row[1] is None:
            row[1] = out[i + 1][0] if i + 1 < len(out) else row[0] + timedelta(minutes=DEFAULT_MINUTES)
        if row[1] <= row[0]:
            row[1] = row[0] + timedelta(minutes=DEFAULT_MINUTES)
    return [tuple(r) for r in out]


def read_committed() -> dict:
    """The programmes already in the repo, keyed as the fresh ones are.

    Not an optimisation — it is the only way the earlier part of today survives, because the
    source stops listing a programme once it has finished. See the note at the top.
    """
    try:
        root = ET.parse(OUT).getroot()
    except (OSError, ET.ParseError):
        return {}
    out = {}
    for pr in root.findall("programme"):
        try:
            start = datetime.strptime(pr.get("start", ""), "%Y%m%d%H%M%S %z")
            stop = datetime.strptime(pr.get("stop", ""), "%Y%m%d%H%M%S %z")
        except ValueError:
            continue
        title = (pr.findtext("title") or "").strip()
        if not title:
            continue
        icon = pr.find("icon")
        out[(start, title)] = (start, stop, title,
                               (pr.findtext("desc") or "").strip(),
                               (pr.findtext("category") or "").strip(),
                               icon.get("src", "") if icon is not None else "")
    return out


def committed_guide_ends() -> datetime | None:
    """The last stop time in the file already in the repo, so we know how long it can carry us."""
    try:
        with open(OUT, encoding="utf-8") as fh:
            stamps = re.findall(r'stop="(\d{14}) ([+-]\d{4})"', fh.read())
    except OSError:
        return None
    if not stamps:
        return None
    return max(datetime.strptime(f"{s} {z}", "%Y%m%d%H%M%S %z") for s, z in stamps)


def main() -> int:
    today = datetime.now(ROME).replace(hour=0, minute=0, second=0, microsecond=0)

    # Start from what is already committed so the morning of today is not lost; a fresh fetch
    # overwrites any programme it also carries, since the source is the authority on itself.
    programmes = read_committed()
    carried = len(programmes)
    failures = []
    for path, offset in PAGES:
        day = today + timedelta(days=offset)
        try:
            page = fetch(path)
        except Unreachable as exc:
            failures.append(str(exc))
            print(f"  ! {exc}", file=sys.stderr)
            continue
        rows = parse_day(page, day)
        if not rows:
            # The page answered and looked like a schedule, so this is OUR failure to read it,
            # not the site being down. Loud, and never silently amnestied below.
            print(f"  ! {path or 'today'}: page had cards but nothing parsed — the markup moved",
                  file=sys.stderr)
            return 1
        for row in rows:
            programmes[(row[0], row[2])] = row     # pages overlap at the edges
        print(f"  {path or 'today':11} {day:%Y-%m-%d}  {len(rows)} programme(s)")

    if not programmes:
        # Nothing answered. A scheduled job should not mail a failure every few hours for someone
        # else's outage, so the committed guide stands — until it is nearly exhausted, at which
        # point silence IS the problem.
        ends = committed_guide_ends()
        if ends and ends - datetime.now(ROME) > timedelta(hours=24):
            print(f"Source unreachable; keeping the committed guide, good until {ends:%Y-%m-%d %H:%M}.",
                  file=sys.stderr)
            return 0
        print("Source unreachable AND the committed guide is nearly spent:\n  "
              + "\n  ".join(failures), file=sys.stderr)
        return 1

    cutoff = datetime.now(ROME) - timedelta(days=RETAIN_DAYS)
    rows = sorted((r for r in programmes.values() if r[1] > cutoff), key=lambda r: r[0])

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
        '<tv generator-info-name="skytg24-epg" source-info-name="guidatv.org">',
        f'  <channel id="{CHANNEL_ID}">',
        f'    <display-name>{escape(CHANNEL_NAME)}</display-name>',
        f'    <icon src="{escape(CHANNEL_LOGO)}" />',
        '  </channel>',
    ]
    for start, stop, title, desc, category, icon in rows:
        lines.append(f'  <programme start="{start:%Y%m%d%H%M%S %z}" '
                     f'stop="{stop:%Y%m%d%H%M%S %z}" channel="{CHANNEL_ID}">')
        lines.append(f'    <title lang="it">{escape(title)}</title>')
        if desc:
            lines.append(f'    <desc lang="it">{escape(desc)}</desc>')
        if category:
            lines.append(f'    <category lang="it">{escape(category)}</category>')
        if icon:
            lines.append(f'    <icon src="{escape(icon)}" />')
        lines.append('  </programme>')
    lines.append('</tv>')

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    covered = max(r[1] for r in rows)
    print(f"Wrote {OUT}: {len(rows)} programmes for {CHANNEL_NAME}, "
          f"{rows[0][0]:%d %b %H:%M} to {covered:%d %b %H:%M %Z} "
          f"({carried} carried over from the committed guide).")
    if failures:
        print(f"({len(failures)} page(s) did not answer; the rest were written.)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
