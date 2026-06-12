# Operational pain points — what the team has logged this quarter

Ten items, ordered by how often they surfaced in retrospectives. Each is real; each has at least one historical incident it contributed to. Use these as input to your redesign — every claim about a capability gap in your architecture should map to one or more of these.

1. **Log search latency exceeds 25 seconds when the query window crosses 7 days.** Splunk Cloud's free-tier indexer falls behind during the daily 14:00 traffic peak. On-call engineers learned to narrow window first, search second — costs an extra 30–90 seconds per incident.

2. **Distributed tracing samples at 1%.** Tail latency root causes are invisible. When a slow query affects 0.3% of requests, only ~10 traces in a 10-minute window will exist, and most will be sampled out. Two incidents in the last quarter were diagnosed by reading logs because traces were unavailable.

3. **No service-graph-based alert correlation.** On-call opens four dashboards (Datadog APM, Datadog Logs, Splunk, PagerDuty) during every multi-service incident to manually correlate. Median time from "page received" to "first hypothesis" is 8 minutes.

4. **Custom-metric cardinality explosions.** A new team tagged metrics with `customer_id` last quarter; the Datadog custom-metric line jumped from $400 to $2,200 / month before anyone noticed. There is no cardinality-cost feedback in the dev flow.

5. **Alerts arrive in PagerDuty in clusters with no automatic grouping by fingerprint.** During a cascade involving four services, on-call received 47 separate PagerDuty incidents within 90 seconds. The team manually closed 43 of them after triaging the first 4.

6. **Splunk index rotation breaks dashboards mid-incident.** Once a quarter the indexer rotates its hot tier and saved searches return empty for 5–15 minutes. Has caused an on-call engineer to escalate to a non-existent secondary issue.

7. **No audit trail of incident decisions.** Postmortems are reconstructed from Slack scrolls. The team cannot answer "in the last 90 days, how many times did we restart payment-svc?" without a half-day manual count.

8. **Engineer onboarding to the observability stack takes 2–3 weeks.** Each of Datadog, Splunk, PagerDuty has its own query language, alert format, and concept model. New hires shadow on-call for 6 weeks before going solo, and report that "I never know which tool to open first" is the dominant friction.

9. **There is no way to ask "show me all incidents on this service in the last 90 days with action=rollback".** Postmortem patterns are invisible at scale. The team suspects 30% of restart actions are repeated mistakes but has no data to confirm.

10. **Vendor lock-in: two of three contracts auto-renew with notice windows the team has missed before.** The Splunk renewal last year happened because the calendar reminder fired the day after the cancellation window closed. The team escaped via a relationship favour from the account manager; that path will not work twice.

## What is *not* on this list

The team has decent metrics dashboards, decent alerting fidelity at the per-service level, and reasonable runbooks. These are working. The pain points above are about correlation, cost, and cognitive load — not basic observability.
