# SPEC: <platform name>

## 1. Platform overview
*One paragraph. What this AIOps platform does, its scope, its non-scope.*

## 2. SLO definition (from W3-D1)
- Target SLO: <99.X%>
- SLI: <description>
- Error budget: <count / time>
- Burn-rate alert tiers: <list>

## 3. Detection + Correlation + RCA stack (from W1+W2)
- **Detector:** <algorithm>, <input source>, <output schema>
- **Correlator:** <algorithm>, <window>, <output cluster spec>
- **RCA:** <approach>, <graph source>, <output schema>

## 4. Reliability validation (from W3-D2)
- Chaos run cadence: <weekly | monthly>
- Detected/total ratio target: <%>
- Steady-state signal: <synthetic probe | internal metric | both>

## 5. Operational pattern (from W3-D3)
- Postmortem template: <link>
- On-call rotation: <model>
- ADR repository: <link>

## 6. Cost model (from W3-D3)
- Monthly cost: <USD>
- Break-even avoided incidents/month: <count>
- See `cost_model.py`

## 7. Open risks
- Risk 1: ...
- Risk 2: ...
