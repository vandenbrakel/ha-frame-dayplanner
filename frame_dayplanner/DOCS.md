# Frame Dayplanner

Shows a kid-friendly day planner on a Samsung **The Frame** in art mode:
day, date, a big analog clock, weather for morning/afternoon/evening,
events (weekly, custom, holidays, calendars), a sum and a *klankzuiver* word.

Every minute a fresh image is rendered on your Home Assistant server and
uploaded to the TV. When the schedule (or a test run) ends, the art that was
showing before is restored and the uploaded planner images are deleted.

## Settings

The easiest way to configure everything is the **Settings** tab on the
Dayplanner page (sidebar). It has an icon search with previews (Dutch words such
as *fiets* or *zwemmen* work too), day buttons, time and date pickers, dropdowns
with your weather and calendar entities, and colour pickers. The preview updates
while you edit; **Save** stores the settings in the add-on configuration and
applies them right away. The *Configuration* tab and *Edit in YAML* keep working.

## First start

1. Give the TV a fixed IP address in your router.
2. Fill in **TV IP address** on the *Configuration* tab and save.
3. Open **Dayplanner** in the sidebar and click **Connect to TV**. The TV
   shows a prompt to allow *FrameDayplanner*. Accept it with the remote.
   The token is saved, so this happens only once.
4. Click **Start now** to test it for a few minutes. Fill in **As if it is**
   (or click *tomorrow 07:00*) to see how the planner will look at another
   time: the TV shows that day and time and the clock runs on from there. The
   preview follows the chosen time too, without starting a test.

## Schedule

```yaml
schedule:
  - days: mon-fri       # mon-fri, sat,sun, daily, weekdays, weekend, ma,wo,vr
    start: "07:00"
    end: "08:30"
```

**Also show around events** (`schedule_events`, on by default): every event
with a start time switches the planner on outside the schedule too, from
`schedule_event_minutes` (default 30) before until the event ends. With
`leave_before` the prep time counts from the time to leave: Zwemles 14:00,
leave 45 min before, prep 30 min → shown 12:45–15:00. Timed events from your
HA calendars count too (after the word filters); all-day events don't.

Outside these windows nothing happens. *Stop & restore* during a window ends
it until the next window.

## Waking the TV

With **Wake the TV** (`wake_tv`, on by default) the planner switches the TV on
when it should be shown (a schedule window, an event or a test run) and the TV
is off or in standby, for example in the morning:

1. Wake-on-LAN to the TV's MAC address. The MAC is learned automatically while
   the TV is on (see the Status tab), or fill in `tv_mac` yourself.
2. Still in standby: a press on the power button.
3. It came up with the normal TV picture: switch to art mode.

It tries at most 3 times per window, so a TV that someone switches off on
purpose stays off. A TV that is on and showing TV is never interrupted
(`only_in_art_mode`).

On the TV, enable **Settings > General > Network > Expert settings > Power on
with mobile** (called *Wake on LAN* on some models), otherwise a TV that is fully
off cannot be woken over the network. The app uses the host network for this
(Wake-on-LAN packets are broadcasts).

## Layout

Each orientation has its own list of columns. `width` is relative.
In `modules`:

* `,` starts a new row
* `|` puts modules side by side in one row
* `name:2.5` overrides the height weight of a module

| Module    | What it shows |
|-----------|---------------|
| `day`     | MAANDAG, on the colour of the day (`day_background`) |
| `date`    | 20 november |
| `clock`   | Analog (or digital) clock, with arcs for today's timed events |
| `time`    | Digital time only |
| `weather` | Morning / afternoon / evening icon and temperature |
| `events`  | Today's events, highlighted ones big and in capitals |
| `sum`     | A sum, e.g. `3 + 4 = ?` |
| `word`    | A klankzuiver word, e.g. `vis` |
| `week`    | The coming 7 days, one colour per day, with event icons. `week_start`: `monday` (default) or `sunday` first, every tile showing the next occurrence of that day; or `today` on top |
| `spacer`, `line` | Empty space or a thin line |

Portrait default:

```yaml
layout_portrait:
  - width: 80
    modules: "day, date, clock, weather, events, sum | word"
  - width: 20
    modules: "week"
```

## Countdown on the clock (time slots)

Mark important moments, such as when it's time to leave for school. This is an
ordinary event with **Show: countdown on the clock** (`show: countdown`):

```yaml
events:
  - title: Naar school
    when: mon-fri
    start: "08:00"
    end: "08:30"
    icon: mdi:school
    color: "#e0453a"
    show: countdown      # list, week, countdown; default: list,week
```

`show` decides where an event appears: `list` (the events module), `week` (an
icon on the week strip) and/or `countdown` (a label below the clock and a band
on the minute scale). Combine them as you like, e.g. `show: list,countdown`.
Without an end time a countdown counts down to its start.
The old `clock_slots` setting still works: existing slots are moved into Events
automatically (with `show: countdown`) and saved that way the next time you
save the settings.

The clock dial always shows the current half of the day, using the 24-hour
time: before 12:00 only morning items are drawn, after 12:00 only
afternoon/evening ones. So a countdown at 20:15 never shows up at 08:15.

* A countdown shows up **60 minutes before its end** (or from the full hour of its
  start, if that is earlier) and stays until it ends. "Naar school" 08:00–08:30
  appears at 07:30.
* It is drawn on the **minute scale**: a coloured band just inside the rim over
  its real range only, minute 0 to 30 for 08:00–08:30, with its icon at the end.
  When the minute hand reaches the icon it's time to go.
* Other timed events are arcs on the outer ring (hour scale). The outer ring only
  shows what starts within the next 60 minutes (or is running). A longer prep
  time only switches the planner on earlier (see Schedule).
* The colour shows the **minutes left** until the deadline (the end of a countdown,
  or the start of an event with `leave_before`), on every ring:

  | Minutes left | Colour | At school 8:30 |
  |---|---|---|
  | more than 15 | green | until 8:15 |
  | 15 – 10 | orange | 8:15 – 8:20 |
  | 10 – 5 | red | 8:20 – 8:25 |
  | last 5 | purple | 8:25 – 8:30 |

  The colours blend smoothly. `clock_slot_gradient: false` uses the event's own colour.
  The part that has passed fades out.
* The label below the clock shows *nog 13 min* in the last
  `clock_slot_countdown_minutes` before the start. During the slot it is
  filled with the colour of the current minute.

## Hidden figure

Every day a small figure is hidden in an empty spot on the planner (never on text, tiles or the clock) (a new spot each
day, or each hour/session with `puzzle_refresh`). By default it is one of the
`hidden_icons` (icons or emoji, e.g. `🐒`). To use your own picture, for example a drawing by your child, put a
PNG with a transparent background in `/share/dayplanner/` (via the Samba or
File editor add-on) and set `hidden_image: /share/dayplanner/figure.png`.
A folder works too; a different picture is picked every day.
Make it harder with a smaller `hidden_size`, or hide several with `hidden_count`.

## Events

```yaml
events:
  - title: Zwemdag
    when: mon                # weekly
    icon: mdi:swim           # any Material Design Icon
    start: "14:00"           # optional: shows an arc on the clock
    end: "17:00"
    leave_before: 15         # optional: time to leave, see below
    highlight: true          # big, in capitals
  - title: Spelen met Fedde
    when: 2026-11-20         # once
  - title: Bibliotheek
    when: monthly 1st wed    # every month: 'monthly 15', 'monthly 2nd tue', 'monthly last fri'
    icon: mdi:book-open-variant
  - title: Verjaardag oma
    when: 03-14              # every year (MM-DD)
    icon: mdi:cake-variant
  - title: Kerstvakantie
    when: 2026-12-19..2027-01-03
```

**Icons:** `icon` is a Material Design Icon (`mdi:swim`), a colour emoji
(`🐒`, `⚽`, `🎂`; about 1900 available) or a letter, number or short word that
is shown in the bubble instead (`A`, `3`, `GYM`). The icon search on the
Settings tab finds both icons and emoji, in English and Dutch (*aap*, *fiets*). A lower-case word
that is also an icon name is an icon; write `text:swim` to show the word.
This works for events and icon rules.

**Time to leave:** with `start` and `leave_before` (minutes) the clock shows a
green → yellow → orange → red lead-in from the time to leave until the start,
before the event's own arc. In the hour of leaving it also works like a time
slot: the minute band and a label *Zwemles: vertrekken (nog 10 min)* below the
clock.

Multiple rules in one `when` can be separated with `;`. `monthly 31` means the
last day of every month. The **Settings** tab on the Dayplanner page has a picker
for all of these.

**Calendars:** add calendar entities (e.g. a Google or CalDAV calendar).
Use include / exclude words to filter, and icon rules (`zwem=mdi:swim`)
to pick icons.

**Holidays:** public holidays from the `holidays` library for the chosen
country, plus (for NL/BE) Sinterklaas, Sint-Maarten, Moederdag, Vaderdag,
Dierendag and Valentijnsdag.

## Sums and words

* `sum_operations`: `+`, `+,-`, `x`, `x,:` …
* `sum_max`: the highest number (for `+`/`-`) or the highest table (for `x`/`:`)
* `sum_terms`: 2 to 4 numbers
* `sum_missing`: `random` sometimes asks `3 + ? = 7`
* `word_patterns`: `mkm` (vis), `mk,km` (zee, ik), `mmkm` (slak), `mkmm` (kast), `mmkmm` (plant)
* `word_max_letters` and `word_digraphs` (aa, oo, oe, ui, ij …) control the difficulty
* `word_custom_list` replaces the built-in list with your own words

The sum and word stay the same all day (or hour / session, see `puzzle_refresh`).

## Automations

The add-on listens to the event `frame_dayplanner` with data
`action: test | stop | push`, `minutes` (for `test`) and optionally `at`
(`"07:00"` = the next 07:00, or `"2026-11-16T07:00"`) to pretend another time. In a script or
automation:

```yaml
- event: frame_dayplanner
  event_data:
    action: test
    minutes: 15
```

A dashboard button example:

```yaml
type: button
name: Dagplanner 15 min
icon: mdi:calendar-clock
tap_action:
  action: call-service
  service: script.dagplanner_test
```

with script:

```yaml
dagplanner_test:
  sequence:
    - event: frame_dayplanner
      event_data:
        action: test
        minutes: 15
```

Status is published as `sensor.frame_dayplanner` (`idle`, `schedule`, `test`).

## Themes

| Theme | Look |
|-------|------|
| `paper` | Warm, flat paper colour |
| `paper-texture` | The same colours with a real paper look: grain, fibres, soft clouds, darker edges |
| `kraft` | Brown kraft paper, coarser fibres |
| `chalkboard` | Dark green board with chalk-white writing and wipe smudges |
| `white` | Clean white |
| `dark` | Dark grey |

The textures are generated by the add-on (no image files). Textured frames are
about 1 MB instead of ~400 kB, so uploading takes a little longer; the upload
lead time adapts automatically. Day colours that would almost disappear into
the background are lightened automatically.

## Weather

Set `weather_entity` to a weather entity that exists in your Home Assistant.
The **Weather check** on the Dayplanner page lists all your weather entities
and which forecast each one supports.

Recommended: **Meteorologisk institutt (Met.no)**. It's free, needs no account,
is built into Home Assistant and gives an *hourly* forecast, so morning,
afternoon and evening each get their own weather. Add it via
*Settings → Devices & services → Add integration → Meteorologisk institutt*;
the entity is usually `weather.forecast_home`.

Alternatives: *OpenWeatherMap* (hourly, free API key) or *Buienradar*
(daily forecast only, so all three periods show the same day forecast;
the current period uses the actual conditions).

Periods that have already passed show the forecast that was shown earlier
that day; right after a restart they show *–*.

## Notes

* Uploading a 4K image takes a few seconds; `lead_seconds` starts early so
  the minute changes on time. Use `1080p` if your TV is slow.
* With **Only in art mode** on, nothing is pushed while someone is watching TV.
* Portrait: the TV must be physically in portrait (rotating mount); the image
  is rendered portrait (2160 × 3840).
