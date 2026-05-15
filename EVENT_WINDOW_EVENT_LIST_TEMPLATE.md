# Event-Window Differential V1 — Event List Template

**Signal family:** `cross_asset_event_window_differential_v1`

## Purpose

This document describes the event-list artifact that **must** exist before any
event-window capture is performed.  The event list is a frozen, pre-committed
record of which scheduled macro events are included in (or excluded from) the
capture corpus, and why.

The event list is **not** an active collection schedule.  It is an audit
artifact that prevents post-hoc cherry-picking of events based on observed
price action.

## Rules

1. **Pre-commitment:** Every event in the list must be recorded before its
   first window opens (i.e., before `event_window_start_utc`).
2. **No retroactive addition:** No event may be added to the list after its
   window has passed, regardless of how interesting the price action was.
3. **Exclusion is valid:** An event may be excluded before its window opens
   (e.g. cancelled, rescheduled, or a higher-priority conflict).  Record the
   `exclusion_reason` — do not remove the row.
4. **Baseline overlap quarantine:** If another high-impact scheduled macro
   event has its event window overlapping this event's baseline window, the
   paired capture is quarantined.  Record this in `exclusion_reason`.
5. **No future population:** This template is not populated with specific
   future events unless explicitly requested.

## Both Fields Always Present, Exactly One Non-Null

Every event-list row **must** contain both `inclusion_reason` and
`exclusion_reason` fields.  Exactly one of them is a non-empty string; the
other is `null`.

| Row type | `inclusion_reason` | `exclusion_reason` |
|----------|-------------------|-------------------|
| Included | Non-empty string  | `null`            |
| Excluded | `null`            | Non-empty string  |

This convention ensures every event has an auditable status.  An event cannot
be in an ambiguous state (both null, both non-null, or absent reasons).
The machine-readable schema (`event_window_event_list.schema.json`) enforces
this via `oneOf` validation.

## Schema Fields

Each event in the list is a JSON object with the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_id` | string | yes | Unique identifier, e.g. `ew_FOMC_2026_06_18` |
| `event_name` | string | yes | Human-readable label, e.g. "FOMC Rate Decision 2026-06-18" |
| `scheduled_event_utc` | string (ISO 8601) | yes | The scheduled release time per the macro calendar, e.g. `"2026-06-18T18:00:00Z"` |
| `calendar_source` | string | yes | Where the event was sourced from, e.g. "Forex Factory", "Investing.com" |
| `calendar_source_snapshot_utc` | string (ISO 8601) | yes | When the source was last checked, e.g. `"2026-06-17T12:00:00Z"` |
| `event_window_start_utc` | string (ISO 8601) | yes | Computed as `scheduled_event_utc - 5 min` |
| `event_window_end_utc` | string (ISO 8601) | yes | Computed as `scheduled_event_utc + 15 min` |
| `baseline_window_start_utc` | string (ISO 8601) | yes | Computed as `scheduled_event_utc + 90 min` |
| `baseline_window_end_utc` | string (ISO 8601) | yes | Computed as `scheduled_event_utc + 110 min` |
| `inclusion_reason` | string or null | yes | Non-empty string for included rows; `null` for excluded rows |
| `exclusion_reason` | string or null | yes | Non-empty string for excluded rows; `null` for included rows |

## Baseline Overlap Check

For each event in the list, check whether any other high-impact macro event's
event window overlaps `[baseline_window_start_utc, baseline_window_end_utc]`.
If an overlap exists, set `exclusion_reason` to:

```
"Baseline overlap with {other_event_name} ({other_event_id}) at {overlap_window}"
```

The paired capture is then quarantined.

## Example Included Entry

```json
{
  "event_id": "ew_FOMC_2026_06_18",
  "event_name": "FOMC Rate Decision 2026-06-18",
  "scheduled_event_utc": "2026-06-18T18:00:00Z",
  "calendar_source": "Forex Factory",
  "calendar_source_snapshot_utc": "2026-06-17T12:00:00Z",
  "event_window_start_utc": "2026-06-18T17:55:00Z",
  "event_window_end_utc": "2026-06-18T18:15:00Z",
  "baseline_window_start_utc": "2026-06-18T19:30:00Z",
  "baseline_window_end_utc": "2026-06-18T19:50:00Z",
  "inclusion_reason": "FOMC rate decision — highest-impact scheduled macro event for crypto markets",
  "exclusion_reason": null
}
```

## Example Excluded Entry

```json
{
  "event_id": "ew_NFP_2026_07_03",
  "event_name": "US NFP July 2026",
  "scheduled_event_utc": "2026-07-03T12:30:00Z",
  "calendar_source": "Investing.com",
  "calendar_source_snapshot_utc": "2026-07-02T10:00:00Z",
  "event_window_start_utc": "2026-07-03T12:25:00Z",
  "event_window_end_utc": "2026-07-03T12:45:00Z",
  "baseline_window_start_utc": "2026-07-03T14:00:00Z",
  "baseline_window_end_utc": "2026-07-03T14:20:00Z",
  "inclusion_reason": null,
  "exclusion_reason": "Baseline overlap with FOMC Minutes 2026-07-03 (ew_FOMC_MINUTES_2026_07_03) at 14:00-14:20"
}
```

## Machine-Readable Schema

See `event_window_event_list.schema.json` for the JSON Schema defining the
event list format.

---

*This is a template.  No events have been populated.  The event list is not
an active schedule — it is an audit artifact.*
