# Wowaudit API Reference

Internal documentation for the wowaudit REST API as used by Raid Ledger.
No official public docs exist — this was reverse-engineered from the
interactive API console at `https://wowaudit.com/{region}/{realm}/{guild}/{team}/api`.

## Authentication

Enable the API in your team's settings page. A private key is generated on first enable.

Two methods:

```
Authorization: Bearer <WOWAUDIT_API_KEY>
```

or query parameter:

```
GET /v1/characters?api_key=<WOWAUDIT_API_KEY>
```

The key grants access to the entire team environment — keep it secret.

## Base URL

```
https://wowaudit.com
```

All endpoints are prefixed with `/v1/`.

## Endpoints

### GET /v1/characters

Returns the full roster for the team.

**Response:**
```json
[
  {
    "id": 4779912,
    "name": "Deemle",
    "realm": "Tichondrius",
    "class": "Warlock",
    "role": "Ranged",
    "rank": "Main",
    "status": "tracking",
    "note": null,
    "blizzard_id": 242903093,
    "tracking_since": "2026-03-24T02:19:06.000Z"
  }
]
```

**Field notes:**
- `role`: One of `Tank`, `Heal`, `Melee`, `Ranged`
- `rank`: Observed values: `Main` (others TBD)
- `status`: Observed values: `tracking`, `pending`

### GET /v1/period

Returns the current period number and season metadata.

**Response:**
```json
{
  "current_period": 1055,
  "current_season": {
    "id": 1,
    "name": "Season 1",
    "start_date": "2026-03-10",
    "end_date": "2026-10-01",
    "kind": "live",
    "expansion": "Midnight",
    "keystone_season_id": 13,
    "pvp_season_id": 38,
    "first_period_id": 1053
  }
}
```

### GET /v1/historical_data

Returns all characters' activity for the **current period** (no period parameter).

**Response:**
```json
{
  "period": 1055,
  "characters": [
    {
      "id": 4779898,
      "name": "Fxhp",
      "realm": "Area 52",
      "data": {
        "dungeons_done": [
          {"level": 21, "dungeon": 402},
          {"level": 14, "dungeon": 402}
        ],
        "world_quests_done": 45,
        "regular_mythic_dungeons_done": 8,
        "vault_options": {
          "raids":    {"option_1": 259, "option_2": 246, "option_3": null},
          "dungeons": {"option_1": 256, "option_2": 256, "option_3": null},
          "world":    {"option_1": 259, "option_2": 259, "option_3": 259}
        }
      }
    }
  ]
}
```

**Field notes:**
- `dungeons_done`: Keystones only (with key level). Empty array = no keystones timed.
  `regular_mythic_dungeons_done` is a separate integer count of mythic-0 completions.
- `vault_options`: Values are **item level numbers**. Non-null = slot earned.
  Three categories: `raids`, `dungeons`, `world`. Each has `option_1` through `option_3`.
- `data`: Can be `null` if no activity has been tracked for that character yet.

### GET /v1/historical_data/{id}

Returns a single character's full activity history and current best gear.

**Response:**
```json
{
  "character": {
    "id": 4779911,
    "name": "Voxle",
    "realm": "Tichondrius"
  },
  "history": [
    {
      "dungeons_done": [],
      "world_quests_done": 27,
      "regular_mythic_dungeons_done": 0,
      "vault_options": {
        "raids":    {"option_1": null, "option_2": null, "option_3": null},
        "dungeons": {"option_1": null, "option_2": null, "option_3": null},
        "world":    {"option_1": null, "option_2": null, "option_3": null}
      }
    }
  ],
  "best_gear": {
    "main_hand": {"ilvl": 263, "id": 193707, "name": "Final Grade", ...},
    "head":      {"ilvl": 263, "id": 266429, "name": "Silvermoon Sunspire", ...},
    ...
  }
}
```

**Field notes:**
- `history`: Ordered array of period entries. **No period numbers or dates** on entries —
  must correlate with `/v1/period` metadata by position (most recent first, presumably).
- `best_gear`: Full gear per slot with ilvl, enchants, sockets, upgrade track.
  Only available on this endpoint, **not on the batch endpoint**.

### GET /v1/applications

Returns recruitment applications. Not used by Raid Ledger.

## Known Limitations

- **No documented rate limits.** No `X-RateLimit` headers observed.
- **Batch endpoint has no period parameter** — always returns current period data.
- **History entries are undated** — no period number or timestamp per entry.
- **Gear data only on single-character endpoint** — cannot get ilvl in batch.
- **Tracking delay** — wowaudit can only report activity for periods after
  `tracking_since`. It does not backfill historical data.

## Role Mapping (wowaudit → raid-ledger)

| Wowaudit | Raid Ledger |
|----------|-------------|
| Tank     | tank        |
| Heal     | healer      |
| Melee    | dps         |
| Ranged   | dps         |
