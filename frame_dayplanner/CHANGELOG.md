# Changelog

## 0.8.0

- New themes with a real texture: `paper-texture` (paper grain, fibres, soft clouds), `kraft`
  (brown paper) and `chalkboard`. The original `paper` theme is unchanged
- Day colours that would vanish into the background (e.g. dark weekend colours on a dark theme)
  are lightened automatically

## 0.7.3

- Hidden figure follows the theme by default (drawn in the theme's ink colour); the old default red
  `#e0453a` is treated as "follow the theme", any other colour is kept

## 0.7.2

- Sums never use 0 (no more `9 + 0`), numbers are spread evenly, tables start at 2

## 0.7.1

- Events with `leave_before` also get the minute band (from the time to leave until the start) and a
  "…: vertrekken" label with countdown below the clock, like time slots
- Time-left colours run green -> yellow -> orange -> red (brighter in between)

## 0.7.0

- Events: `leave_before` (minutes). The clock shows a green -> orange -> red lead-in from the
  time to leave until the event starts, followed by the event's own arc

## 0.6.0

- Colour emoji as icons (~1900, e.g. 🐒 🦒 ⚽ 🎂) for events, slots, icon rules and the hidden figure
- Icon search covers Material Design Icons and emoji, in English and Dutch (aap, fiets, verjaardag …)

## 0.5.1

- An event / slot icon can be a letter, number or word (e.g. `A`, `3`, `GYM`); `mdi:...` stays an icon

## 0.5.0

- Settings tab on the Dayplanner page with pickers: icon search with previews (Dutch words work too),
  day toggles, time and date pickers, weather / calendar entity dropdowns, colour pickers
- Live preview of unsaved settings; saving stores them in the add-on configuration and applies them without restart
- Events can repeat weekly, monthly (`monthly 15`, `monthly 1st wed`, `monthly last fri`), yearly, or happen once
- Settings are validated with clear error messages

## 0.4.2

- Week strip always shows the upcoming days: tiles keep the monday / sunday order,
  each shows the next occurrence of that day (no past days)
- Hidden figure only hides in empty space, never on text, tiles, panels or the clock

## 0.4.1

- Slot on the minute scale runs green -> orange -> red to show the time left (`clock_slot_gradient`)
- Removed "nu!" from the slot label

## 0.4.0

- `week_start` setting for the week strip: monday (default), sunday or today on top

## 0.3.2

- Past weather periods use all history updates (temperature changes too), not only condition changes
- Hidden figure picks a calm spot instead of covering text
- Clock only reserves the outer ring when there are arcs to draw (bigger clock otherwise)
- Upload lead time adapts to the measured upload duration

## 0.3.1

- Weather periods that have already passed are filled from Home Assistant history
- Read the Supervisor token from the s6 container environment if it is not in the process environment
- Log a warning when there is no connection to Home Assistant

## 0.3.0

- Clock slots are drawn on the minute scale during their own hour; the dial
  only shows the current AM/PM half (24h based)
- Removed the minutes-left wedge (replaced by the minute band)
- Day name on the colour of the day (`day_background`), text colour picked for readability everywhere
- Week strip starts with today on top
- Weather: works with daily / twice-daily forecasts too; weather check on the Dayplanner page

## 0.2.0

- Clock time slots with label (e.g. *Naar school*), countdown and coloured minutes left
- Hidden figure: a new spot every day, icon or your own picture from /share

## 0.1.0

- First version: modules day, date, clock, time, weather, events, sum, word, week, spacer, line
- Schedule, test runs, restore previous art, cleanup of uploaded images
- Portrait and landscape layouts
- Holidays, HA calendars with word filters and icon rules
- Ingress UI with live preview, `frame_dayplanner` event and status sensor
