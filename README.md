# skytg24-epg

An XMLTV guide for the Italian news channel **Sky TG24**, rebuilt every four hours by a GitHub Action.

Unlike the sister repos this one cannot use **Tivù la Guida**: that is the free-to-air DTT guide and
it lists 70 channels, none of them Sky. The source here is the HTML schedule at
[guidatv.org](https://guidatv.org/canali/sky-tg24), which carries what these repos exist for — a
per-programme cover, a category and a description.

## Use it

| | |
|---|---|
| **EPG URL** | `https://raw.githubusercontent.com/sasagr/skytg24-epg/main/skytg24.xml` |
| **tvg-id** | `SkyTG24.it` |
| **Logo** | `https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-tg24-it.png` |

The tvg-id must match exactly: XMLTV consumers tie a programme to a channel by the `channel=`
attribute, so a mismatch shows the channel with an empty guide rather than an error.

## Times are Italian, and stay Italian

The channel is broadcast from Italy (`Europe/Rome`) and watched from Cyprus (`Europe/Nicosia`), an
hour ahead for most of the year. **That conversion is deliberately not done here.** XMLTV timestamps
carry their own UTC offset, so every time is written with Rome's — `+0100` in winter, `+0200` in
summer — and the player converts to wherever it is.

Writing Cyprus times instead would be wrong twice over: it would be wrong for anyone else reading
the file, and it would be wrong in both countries at each of the four moments a year when the two
zones change clocks on different dates. `ZoneInfo` resolves the offset from the wall time, so this
stays correct across a DST boundary with no table of dates to maintain.

## How far ahead it reaches

Two days, and no further. guidatv.org addresses days by name — `ieri`, today, `domani`,
`dopodomani` — and has no paging parameter. That is the ceiling of the source, not a setting; the
sister repos ask Tivù for seven.

## Why it merges instead of replacing

Today's page **trims what has already finished**: fetched at 11:00 it begins at 11:00, leaving the
morning behind it that no later fetch will fill. So each run merges into the file already committed
and the day accumulates; anything that finished more than `RETAIN_DAYS` ago is dropped so the file
does not grow without end.

That matters for this channel in particular — it is the one recorded round the clock on the DVR,
and a recording is named from the guide covering the moment it was made. A hole in the last few
hours is a recording that cannot be named.

## How it works

`generate.py` fetches the four day pages, splits each on its `<h3 class="hour">` headings (one per
programme card, measured 47 of each on a sample day, pairing exactly), and reads the title,
duration, category, description and cover out of each card. Covers go through Next.js's image proxy,
so the real `img-guidatv.org` URL is decoded out of the `url=` parameter — the proxy form is useless
to anyone but guidatv.org. Roughly half the programmes have a cover; the rest simply have none at
the source.

A listing runs past midnight, so when a time goes backwards the date is rolled forward. It is
stdlib-only, so the Action needs no `pip install`.

## When it fails, and how loudly

Borrowed from `bbcnews-epg`, which learned both of these the hard way:

- **It does not trust the status code.** A site that redirects to a friendly error page answers 200
  from the wrong URL. The check is that we are still on a `/canali/sky-tg24` path and that the page
  contains programme cards at all.
- **It tells an outage apart from a bug.** If no page answers, the committed guide stands and the
  run exits 0 — a scheduled job should not mail a failure every four hours for someone else's
  outage. That amnesty expires once the committed guide has under 24 hours left in it. But if a
  page answers *with* cards and nothing parses out of them, the markup has moved and the run fails
  immediately: that one is ours.

## If the guide goes empty

Almost always one of two things:

- **The page markup moved.** The run will have failed with "page had cards but nothing parsed".
  Check `HOUR`, `TITLE` and friends in `generate.py` against the live HTML.
- **The app's tvg-id no longer matches `CHANNEL_ID`.** They have to be the same string.
