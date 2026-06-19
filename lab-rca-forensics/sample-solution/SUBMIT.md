# Submission Reflection

**Hardest incident to diagnose.** I-5 (mTLS cert clock skew) took the
most time. The error message — "certificate not yet valid" — is
unusual; my first instinct was to chase the 05:30 payment-svc deploy
because it was the closest event in time. The signal that broke me out
was actually reading the `extra` fields on the TLS error log: the
log line carried both `not_before` and `current_time`. The numeric
delta between those two timestamps (-27 seconds) is the entire
incident, but you have to look at the structured fields to see it. The
human-readable message alone implies a cert problem; only the fields
reveal it is a clock-vs-cert problem, not a cert problem.

**Trap I fell into.** On I-3 I was halfway through building a
retry-storm narrative because the alert name `PaymentConnPoolSaturated`
matched I-1's signature. What pulled me out was checking
`fx_api_5xx_per_min.csv` for the I-3 window — it was flat at zero.
Two incidents can produce the same downstream symptom (pool drained)
via two unrelated upstream mechanisms. Pattern-matching by alert name
across incidents is dangerous; verifying the upstream signal is
mandatory.

**Most useful file.** `deploy_log.json`. It is the only artifact that
explicitly attributes a configuration change to a person or pipeline
with a timestamp. For I-3 and I-4 the deploy log was the smoking gun:
the flag-flip entry at 11:15 nailed I-3 once paired with the new
`loyalty_client` component appearing in payment-svc.jsonl at the same
instant; the vendor announcement 7 days before I-4 explained the AZ-c
behavior without any guesswork. For I-1 and I-2 the deploy log was the
opposite kind of useful — it allowed me to dismiss the
closest-in-time deploys with confidence rather than chasing them.

**Blind spot in the data pack.** Per-AZ breakdowns on more metrics
would have shortened I-4. The data pack exposes
`payment_az_c_error_rate.csv` as a single metric — I would have
preferred `payment_error_rate{az=a,b,c}` as three series, because the
divergence pattern across AZs is the diagnostic feature. Similarly,
NTP drift per host would have made I-5 a one-look incident; with only
trace-embedded `validator_clock_skew_seconds` you have to know where
to look. A `node_time_drift_seconds` time series would have made the
skew obvious from metrics alone.

**Side note.** The correlator's `WINDOW = 90 min` was tuned for I-2's
slow-burn alert spread. A 10-minute window splits I-2 into four chains
and obscures the connection between memory growth and the eventual
checkout error rate. Wider windows risk over-merging unrelated daily
events, but since the data pack has only one incident per day, this
particular risk does not materialize. In a denser environment, an
incident-detection step would precede chain grouping.
