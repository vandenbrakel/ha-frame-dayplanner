# Changelog

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
