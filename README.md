# skytg24-epg

An XMLTV guide for the Italian news channel **Sky TG24**, rebuilt every four hours by a GitHub Action.

Unlike the sister repos this one cannot use **Tivù la Guida**: that is the free-to-air DTT guide and
it lists 70 channels, none of them Sky. The source here is **Sky Italia's own guide API** — the one
behind [guidatv.sky.it](https://guidatv.sky.it) — which answers without a key and carries what these
repos exist for: a per-programme picture, a category and a description.

## Use it

| | |
|---|---|
| **EPG URL** | `https://raw.githubusercontent.com/sasagr/skytg24-epg/main/skytg24.xml` |
| **tvg-id** | `SkyTG24.it` |
| **Logo** | `https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-tg24-it.png` |

The tvg-id must match exactly: XMLTV consumers tie a programme to a channel by the `channel=`
attribute, so a mismatch shows the channel with an empty guide rather than an error.

## Why Sky's guide, and not guidatv.org

guidatv.org was the source until 2 Nov 2026. It has a cover for only about a third of programmes and
none for the bulletins that fill most of the day (Edicola, Preview, Buongiorno, Ore 13, Ore 20 …), so
most recordings of this channel showed the channel logo instead of a picture. Sky's guide names an
image for **every** programme — measured 2 Nov, every strand's image answers 200 — and it reaches
much further ahead (see below).

## Times are Italian, and stay Italian

The channel is broadcast from Italy (`Europe/Rome`) and watched from Cyprus (`Europe/Nicosia`), an
hour ahead for most of the year. **That conversion is deliberately not done here.** Sky answers in
UTC; every time is converted to Rome and written with Rome's offset — `+0100` in winter, `+0200` in
summer — and the player converts to wherever it is.

Writing Cyprus times instead would be wrong twice over: it would be wrong for anyone else reading
the file, and it would be wrong in both countries at each of the four moments a year when the two
zones change clocks on different dates. `ZoneInfo` resolves the offset, so this stays correct across
a DST boundary with no table of dates to maintain.

## How far ahead it reaches

`DAYS_AHEAD` = 8 days — a week, like the sister repos, plus a day of margin. Sky publishes about two
weeks, so this is a setting, not the ceiling of the source.

## Why it merges instead of replacing

Sky **refuses any date before today** ("Dates validation has failed"), so hours that have already
aired exist only in the file committed by earlier runs. Each run therefore merges: the committed
guide is kept for everything **before** the first programme Sky returned, and Sky is the authority
from there on — so a programme Sky reschedules does not linger beside its replacement. Anything that
finished more than `RETAIN_DAYS` ago is dropped so the file does not grow without end.

Carried-over programmes also take Sky's picture for the same title, since a strand keeps one image.
That repaired the days written from guidatv.org.

That matters for this channel in particular — it is the one recorded round the clock on the DVR,
and a recording is named from the guide covering the moment it was made. A hole in the last few
hours is a recording that cannot be named.

## How it works

`generate.py` asks `https://apid.sky.it/gtv/v1/events` for Sky TG24 (channel id `9117` in
`/gtv/v1/channels?env=DTH`) from the start of today to `DAYS_AHEAD` out, paging if needed. Each event
gives `starttime`/`endtime` (UTC), `eventTitle`, `eventSynopsis`, a genre/subgenre, and a content
`uuid`. The uuid is the image id: the picture is written as
`https://it.imageservice.sky.com/pd-image/<uuid>/16-9/1024?territory=IT` — the 16:9 crop, because
`cover` does not exist for many ids.

`eventTitle` is used rather than `epgEventTitle`, which is sometimes an episode label
("S7 Ep21 - Stories") that reads worse as a recording's name. Stdlib only, so the Action needs no
`pip install`.

## When it fails, and how loudly

Borrowed from `bbcnews-epg`, which learned both of these the hard way:

- **It does not trust the status code.** A 200 is not enough; the answer must be JSON with an
  `events` list.
- **It tells an outage apart from a bug.** If Sky does not answer, the committed guide stands and
  the run exits 0 — a scheduled job should not mail a failure every four hours for someone else's
  outage. That amnesty expires once the committed guide has under 24 hours left in it. But if Sky
  answers with an *empty* schedule for a 24-hour channel, the request or the channel id has moved
  and the run fails immediately: that one is ours.

## If the guide goes empty

Almost always one of two things:

- **Sky's channel id changed.** The run will have failed with "Sky answered with no programmes".
  Look Sky TG24 up again in `https://apid.sky.it/gtv/v1/channels?env=DTH` and update `SKY_CHANNEL`.
- **The app's tvg-id no longer matches `CHANNEL_ID`.** They have to be the same string.
