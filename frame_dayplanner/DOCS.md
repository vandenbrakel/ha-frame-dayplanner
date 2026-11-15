# Frame Dayplanner

Shows a kid-friendly day planner on a Samsung **The Frame** in art mode:
day, date, a big analog clock, weather for morning/afternoon/evening,
events (weekly, custom, holidays, calendars), a sum and a *klankzuiver* word.

Every minute a fresh image is rendered on your Home Assistant server and
uploaded to the TV. When the schedule (or a test run) ends, the art that was
showing before is restored and the uploaded planner images are deleted.

## First start

1. Give the TV a fixed IP address in your router.
2. Fill in **TV IP address** on the *Configuration* tab and save.
3. Open **Dayplanner** in the sidebar and click **Connect to TV**. The TV
   shows a prompt to allow *FrameDayplanner*. Accept it with the remote.
   The token is saved, so this happens only once.
4. Click **Start now** to test it for a few minutes.

## Schedule

```yaml
schedule:
  - days: mon-fri       # mon-fri, sat,sun, daily, weekdays, weekend, ma,wo,vr
    start: "07:00"
    end: "08:30"
```

Outside the schedule nothing happens. *Stop & restore* during a scheduled
window ends it until the next window.

## Layout

Each orientation has its own list of columns. `width` is relative.
In `modules`:

* `,` starts a new row
* `|` puts modules side by side in one row
* `name:2.5` overrides the height weight of a module

| Module    | What it shows |
|-----------|---------------|
| `day`     | MAANDAG |
| `date`    | 20 november |
| `clock`   | Analog (or digital) clock, with arcs for today's timed events |
| `time`    | Digital time only |
| `weather` | Morning / afternoon / evening icon and temperature |
| `events`  | Today's events, highlighted ones big and in capitals |
| `sum`     | A sum, e.g. `3 + 4 = ?` |
| `word`    | A klankzuiver word, e.g. `vis` |
| `week`    | Week strip, one colour per day, with event icons |
| `spacer`, `line` | Empty space or a thin line |

Portrait default:

```yaml
layout_portrait:
  - width: 80
    modules: "day, date, clock, weather, events, sum | word"
  - width: 20
    modules: "week"
```

## Time slots on the clock

Mark important moments, such as when it's time to leave for school:

```yaml
clock_slots:
  - label: Naar school
    days: mon-fri
    start: "08:15"
    end: "08:25"
    icon: mdi:school
    color: "#e0453a"
```

The slot shows as a coloured arc on the clock, with a label below the clock.
In the hour before it starts (`clock_slot_countdown_minutes`) the label adds
*nog 13 min*, and the minutes that are left are coloured on the clock face:
when the minute hand reaches the end of the colour, it's time to go. During
the slot the label turns solid with *nu!*.

## Hidden figure

Every day a small figure is hidden somewhere on the planner (a new spot each
day, or each hour/session with `puzzle_refresh`). By default it is one of the
`hidden_icons`. To use your own picture, for example a drawing by your child, put a
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
    highlight: true          # big, in capitals
  - title: Spelen met Fedde
    when: 2026-11-20         # once
  - title: Verjaardag oma
    when: 03-14              # every year (MM-DD)
    icon: mdi:cake-variant
  - title: Kerstvakantie
    when: 2026-12-19..2027-01-03
```

Multiple rules in one `when` can be separated with `;`.

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
`action: test | stop | push` and `minutes` (for `test`). In a script or
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

## Notes

* Uploading a 4K image takes a few seconds; `lead_seconds` starts early so
  the minute changes on time. Use `1080p` if your TV is slow.
* With **Only in art mode** on, nothing is pushed while someone is watching TV.
* Portrait: the TV must be physically in portrait (rotating mount); the image
  is rendered portrait (2160 × 3840).
