# Postmortem: <outage name> (<date>)

> Blameless wording — no "<person name> did X". See §2.1.

## Summary
*2-3 sentence executive summary. What broke, what was affected, how long.*

## Impact
- **Users affected:** <count or %>
- **Services affected:** <list>
- **Revenue/SLA impact:** <if known>
- **Duration:** <UTC start → UTC end, total hours/minutes>

## Timeline (UTC)
Minimum 8 events. Pull from `timeline.json`.

| UTC | Event |
|-----|-------|
| YYYY-MM-DD HH:MM | <event> |

## Root cause
*Single statement. Should be a system property, not a person.*

## Contributing factors
*Conditions that turned a small fault into a big outage.*
1. ...
2. ...

## Detection
- **How was it detected?** <pipeline alert | user report | manual | external probe>
- **MTTD:** <seconds from cause to detection>
- **Pipeline gaps observed during reproduction:**
  - Gap 1: ...
  - Gap 2: ...

## Response
- **First responder action:** ...
- **Time to mitigate:** ...
- **Time to fully resolve:** ...

## Action items
| # | Action | Owner | Type | ETA |
|---|--------|-------|------|-----|
| 1 | ... | <role> | preventive \| detective \| mitigation | <date> |
