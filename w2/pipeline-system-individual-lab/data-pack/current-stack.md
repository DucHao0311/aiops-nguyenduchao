# Current observability stack — inventory and cost

Snapshot taken end of last month. Cost figures are list-price line items from each vendor's invoice. The team did not commit annual upfront on any contract because of past acquisitions.

## Monthly bill, by line item

| Line item                                | Vendor / tool          | Monthly cost | Unit driver                              | Current scale                              |
|------------------------------------------|------------------------|--------------|------------------------------------------|--------------------------------------------|
| APM hosts                                | Datadog Pro            | $11,800      | $40 / host / month                       | 295 host equivalents                       |
| Infrastructure metrics                   | Datadog Pro            | $5,400       | $18 / host / month                       | 300 hosts                                  |
| Custom metrics (cardinality overage)     | Datadog                | $2,200       | $5 per 100 active series above 100/host  | ~440K excess active series                 |
| Datadog logs (indexed)                   | Datadog Logs           | $1,800       | $1.70 per million events indexed         | ~1.05 B events / month                     |
| Log storage + search                     | Splunk Cloud           | $13,900      | Workload + ingest                        | ~52 GB / day indexed, 30-day retention     |
| Incident routing + paging                | PagerDuty Business     | $3,900       | $60 / user / month                       | 65 active users                            |
| Dashboards (read-only mirror)            | Grafana Cloud Pro      | $1,050       | Per active user                          | 12 viewers, 6 editors                      |
| Status page                              | Statuspage by Atlassian| $290         | Tiered subscription                      | Business tier                              |
| Synthetic checks                         | Datadog Synthetics     | $1,360       | $5 per API check / month                 | ~270 checks                                |
| Tracing premium tier                     | Datadog APM Pro        | $300         | Add-on                                   | -                                          |
| **Total**                                |                        | **~$42,000** |                                          |                                            |

## What each piece is used for

- **Datadog (Pro + APM + Logs + Synthetics + Custom Metrics)** — primary "single pane" for engineers. Hosts metrics, traces, container metrics, log search (Datadog Logs is the *hot* tier — last 15 days only). Synthetic checks run from US-East and AP-Southeast.
- **Splunk Cloud** — long-tail log search, 30-day hot retention, then to S3-archive at vendor's cost. Used by security + audit team. Compliance reports run here.
- **PagerDuty** — only paging surface. Receives webhooks from Datadog monitors, Splunk alerts, and a handful of custom scripts. Routing rules are hand-maintained.
- **Grafana Cloud** — used for two "exec dashboards" that pull from Datadog API. The team would prefer to consolidate but the dashboards depend on a custom Datadog query syntax not portable to Grafana queries.
- **Statuspage** — customer-facing status updates, manually edited during incidents.

## Contract clauses worth knowing

- Datadog: monthly billing, no commitment. Auto-renews monthly. Data export via API; no bulk export tool. Trace retention 15 days standard.
- Splunk Cloud: 12-month contract ending in 7 months. 90-day notice required to non-renew. Bulk export is available but contractually capped at 100 GB / day during a transition window.
- PagerDuty: monthly billing, 30-day exit. User-list export is one API call; integration history export requires support ticket.
- Grafana Cloud: monthly. Dashboards exportable as JSON.

## What is *not* in the bill

- Self-hosted Prometheus runs in the staging cluster for development metrics. ~$300 / month in compute. Not on the production critical path.
- A team-built bash script under `tools/oncall-runbook.sh` does ad-hoc log search via Splunk API for the on-call engineer. Not paid for; runs on the on-call laptop.
- Three engineers maintain the alert-routing rules manually as a side responsibility. No dedicated headcount.
