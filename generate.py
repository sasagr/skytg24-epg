#!/usr/bin/env python3
"""Generate an XMLTV EPG for the Italian news channel "Sky TG24".

Sky publishes no XMLTV feed, and unlike the sister repos this channel cannot use Tivù la Guida:
that is the free-to-air DTT guide and it lists 70 channels, none of them Sky. So the source is
Sky Italia's own guide API — the one behind guidatv.sky.it — which answers without a key.

## Why Sky's own guide, and not guidatv.org (the source until 2 Nov 2026)

guidatv.org was the first source and it cost the recordings their pictures. It carries a cover for
about a third of programmes, and none at all for the bulletins that fill most of the day (Edicola,
Preview, Buongiorno, Ore 13, Ore 20 …), so most of the DVR's Sky TG24 recordings fell back to the
channel logo. Sky's guide names an image for EVERY programme. Measured 2 Nov: every strand's
`pd-image/<uuid>/16-9/1024` answers 200.

It also reaches two weeks ahead, where guidatv.org stopped at the day after tomorrow.

## Times are Italian, and stay Italian

The channel is broadcast from Italy (Europe/Rome) and watched from Cyprus (Europe/Nicosia), an hour
ahead for most of the year. Sky's API answers in UTC; every time is converted to Rome and written
with Rome's offset — +0100 in winter, +0200 in summer — and the player converts to wherever it is.
That keeps the file in the same shape as before and as the sister repos.

## What the source gives, and what it does not

The API refuses any date before today ("Dates validation has failed"), so the hours already aired
exist only in the file committed by earlier runs. Each run therefore merges: the committed guide is
kept for everything BEFORE the first programme the API returned, and the API is the authority from
there on — so a programme Sky reschedules does not linger as a ghost beside its replacement. This
matters because this is the channel the DVR records round the clock, and a recording is named from
the guide covering it. Anything that finished more than RETAIN_DAYS ago is dropped.

Stdlib only — no pip install, runs on a stock GitHub Actions runner.
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

# --- config -------------------------------------------------------------------------------------
CHANNEL_ID = "SkyTG24.it"             # XMLTV id → paste this exact string as the app's tvg-id
CHANNEL_NAME = "Sky TG24"
CHANNEL_LOGO = ("https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/"
                "sky-tg24-it.png")
ROME = ZoneInfo("Europe/Rome")
API = "https://apid.sky.it/gtv/v1/events"
SKY_CHANNEL = 9117                    # Sky TG24 in /gtv/v1/channels?env=DTH (number 500, sk 521)
IMAGE_BASE = "https://it.imageservice.sky.com/pd-image"
DAYS_AHEAD = 8                        # the sister repos carry a week; Sky publishes ~two
PAGE_SIZE = 999
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
OUT = "skytg24.xml"
RETAIN_DAYS = 3                       # how much already-broadcast guide to keep when merging


class Unreachable(Exception):
    """The source did not answer, or answered with something that is not the schedule."""


def fetch() -> list[tuple]:
    """Every programme from the start of today (UTC) to DAYS_AHEAD out, as rows.

    Raises Unreachable rather than returning junk: a non-200, a body that is not JSON, or JSON
    without the events list all mean we did not get the schedule. An empty list from a healthy
    answer is returned as empty, and the caller treats that as a failure to read, not an outage.
    """
    now = datetime.now(timezone.utc)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    until = since + timedelta(days=DAYS_AHEAD)
    events, page = [], 0
    while True:
        query = urllib.parse.urlencode({
            "from": f"{since:%Y-%m-%dT%H:%M:%SZ}", "to": f"{until:%Y-%m-%dT%H:%M:%SZ}",
            "pageSize": PAGE_SIZE, "pageNum": page, "env": "DTH", "channels": SKY_CHANNEL,
        })
        req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.load(resp)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise Unreachable(f"Sky guide: {exc}") from exc
        batch = body.get("events") if isinstance(body, dict) else None
        if not isinstance(batch, list):
            raise Unreachable(f"Sky guide answered without a schedule: {str(body)[:200]}")
        events += batch
        # Paged, though a fortnight of one channel (~60 a day) fits in one page as configured.
        if not batch or len(events) >= int(body.get("total") or 0):
            break
        page += 1
    return [row for row in map(to_row, events) if row]


def to_row(event: dict) -> tuple | None:
    """(start, stop, title, desc, category, icon), times in Rome, or None if unusable."""
    try:
        start = datetime.strptime(event["starttime"], "%Y-%m-%dT%H:%M:%SZ")
        stop = datetime.strptime(event["endtime"], "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, TypeError, ValueError):
        return None
    start = start.replace(tzinfo=timezone.utc).astimezone(ROME)
    stop = stop.replace(tzinfo=timezone.utc).astimezone(ROME)
    content = event.get("content") or {}
    # `eventTitle` is the strand ("Stefania Andreoli"); `epgEventTitle` is sometimes the episode
    # ("S7 Ep21 - Stories"), which reads worse as a recording's name.
    title = (event.get("eventTitle") or content.get("contentTitle") or "").strip()
    if not title or stop <= start:
        return None
    desc = (event.get("eventSynopsis") or "").strip()
    category = ((content.get("subgenre") or {}).get("name")
                or (content.get("genre") or {}).get("name") or "").strip()
    # The content uuid IS the pd-image id. 16-9/1024 is the crop the apps and the recorder ask
    # for (SkyImage.preferredCrop / preferred_crop); `cover` is missing for many ids.
    uuid = (content.get("uuid") or "").strip()
    icon = f"{IMAGE_BASE}/{uuid}/16-9/1024?territory=IT" if uuid else ""
    return (start, stop, title, desc, category, icon)


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
    # Start from what is already committed so the hours already aired are not lost — Sky will not
    # answer for them. From the first programme Sky returns onwards, Sky's answer replaces ours.
    programmes = read_committed()
    try:
        fresh = fetch()
    except Unreachable as exc:
        # A scheduled job should not mail a failure every few hours for someone else's outage, so
        # the committed guide stands — until it is nearly exhausted, at which point silence IS
        # the problem.
        ends = committed_guide_ends()
        if ends and ends - datetime.now(ROME) > timedelta(hours=24):
            print(f"{exc}\nKeeping the committed guide, good until {ends:%Y-%m-%d %H:%M}.",
                  file=sys.stderr)
            return 0
        print(f"{exc}\nAND the committed guide is nearly spent.", file=sys.stderr)
        return 1
    if not fresh:
        # Sky answered with a well-formed but empty schedule for a 24-hour channel. That is
        # not an outage to wait out; something about the request or the channel id moved.
        print("  ! Sky answered with no programmes for Sky TG24 — has the channel id changed?",
              file=sys.stderr)
        return 1
    first = min(row[0] for row in fresh)
    # A strand keeps one picture, so what has already aired takes Sky's picture for the same
    # title. That repairs the days written from guidatv.org, which had none for most bulletins
    # and, now and then, another programme's.
    pictures = {row[2]: row[5] for row in fresh if row[5]}
    programmes = {k: r[:5] + (pictures.get(r[2], r[5]),)
                  for k, r in programmes.items() if r[0] < first}
    carried = len(programmes)
    for row in fresh:
        programmes[(row[0], row[2])] = row
    print(f"  Sky guide: {len(fresh)} programme(s), {first:%d %b %H:%M} to "
          f"{max(r[1] for r in fresh):%d %b %H:%M}")

    cutoff = datetime.now(ROME) - timedelta(days=RETAIN_DAYS)
    rows = sorted((r for r in programmes.values() if r[1] > cutoff), key=lambda r: r[0])

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
        '<tv generator-info-name="skytg24-epg" source-info-name="Sky Italia guide">',
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
