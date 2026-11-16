# `ratings.json` format

This is **the common ground** between the viewer (Mac or Android) and the Lightroom plugin.
They do not know each other: they only share this format. Change it only in a compatible way.

```json
{
  "100_1809/IMGP0004.DNG": {
    "rating": 5,
    "datetime": "2026-09-18T15:06:39",
    "source": "camera",
    "updated": "2026-11-15T14:52:38"
  }
}
```

| Field | Required | Description |
|---|---|---|
| key | yes | `<group>/<file path>`. Camera: `<card folder>/<file>`. Folder: `<folder name>/<relative path>`. |
| `rating` | yes | 1 to 5, or **0 = rating cleared** in the viewer (kept with its `updated` date, see below). |
| `datetime` | recommended | EXIF shooting date, `YYYY-MM-DDTHH:MM:SS`, camera local time, no time zone. |
| `source` | no | `camera` or `folder`. |
| `updated` | no | When the rating was last changed. |

## One photo, two names
The camera (and so the phone) names a photo `100_1511/IMGP0447.DNG`; the card in a reader gives
`DCIM/100_1511/IMGP0447.DNG`. The viewer and the merging of phone exports treat them as **the same photo** when the
file name and `datetime` are identical. File numbers repeat from one card to the next: the date is essential.

## How the Lightroom plugin finds a photo
1. It compares the **file name** (last part of the key, case-insensitive) with the name in Lightroom, then checks
   `datetime` when both dates are known.
2. Otherwise, if the photo was renamed at import, it looks it up by `datetime` alone, provided only one rating matches.

## Cleared ratings
Clearing a rating keeps the entry with `"rating": 0`. Without it, an older copy still holding the rating (for example
the next export of the phone, which contains all its ratings) would bring it back when files are merged: the most
recent `updated` wins, for a rating as for a clearing. The Lightroom plugin sets these photos back to no star.
Readers that only want rated photos skip entries whose rating is 0.

## Validation
Ratings received by the Mac server, the Android app or merged from an exported file are checked against this format:
`rating` must be an integer from 0 to 5 (0 = cleared), the key a printable string of at most
512 characters, `datetime` / `updated` real dates in the format above (an invalid `datetime` is dropped; an invalid
or future `updated` rejects the entry, otherwise it would win every later merge). Unknown fields are dropped.
Exported files larger than 20 MB, or that are not a JSON object, are ignored.
