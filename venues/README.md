# Adding a venue

A venue is one YAML file validated against `rfq_mm.venue.config.VenueConfig`
(see `src/rfq_mm/venue/config.py`). No code changes are needed to add one —
if a rule can't be expressed in this schema, the schema is wrong; fix it
there, not with a one-off hack in the venue file.

Minimal skeleton (~20 lines, trim the exemptions list to what your venue
actually offers):

```yaml
venue:
  name: "My Venue"
  model: rfq_with_reference_market
  operator: "Operator GmbH"
  source: "Citation for the rulebook this file encodes"

session:
  timezone: Europe/Berlin
  open: "08:00"
  close: "22:00"
  post_trading_minutes: 5
  days: [mon, tue, wed, thu, fri]

reference_market:
  default: XETR
  hours: { open: "09:00", close: "17:30" }

quote:
  acceptance_window_seconds: 10
  withdrawal_window_seconds: 25
  firm_size_breach_halt_seconds: 60

obligations:
  min_response_ratio: 0.95
  exemptions:
    - { type: reference_indicative_only }

spread:
  rule: not_outside_reference_spread
  include_costs: [trading, settlement, fx]
  depth_aware: true

guaranteed_volumes_eur:
  # all 14 Segment values are required — see rfq_mm.core.types.Segment
  DAX: { ref_open: 100000, ref_closed: 50000 }
  # ... one row per segment ...
  leveraged_scaling: 0.5
  discretionary_below_price_eur: 5.0

surveillance:
  max_rfq_per_participant_per_minute: 60
  max_cancel_ratio: 0.9

throttling:
  levels: [none, slow_low_priority, reject_low_priority, reject_all, manual_reconnect]
```

Validate it with:

```
mm validate venues/my_venue.yaml
```

A missing or misnamed field fails loudly with a field-level error — Pydantic
`extra="forbid"` is on throughout, so typos are caught rather than silently
ignored.
