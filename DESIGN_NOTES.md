# Design notes: stock planning layer

Where the spec left a choice, the more conservative option was taken and is listed here. Each one is a setting in
`config_rules.json` under `analytics.inventory`.

| Choice | Why | Setting |
|---|---|---|
| Orders waiting on master-data setup (`CONFIRMED_NOT_READY`) are excluded from demand under both bases and reported separately | Acceptance rule: setup rows never enter demand | `demand_basis.setup_category` |
| Default demand basis is purchase requests (confirmed plus draft orders) | Requests are logged before stock limits what ships | `demand_basis.default`, `sets` |
| Enquiries and cancelled orders are never demand | They are not purchase requests | `demand_basis.sets` |
| Errors come from a smoothed bootstrap of out-of-sample backtest ratios (actual / forecast over the protection interval), not from drawing regression parameters and then adding residuals | Out-of-sample errors already contain parameter uncertainty, so drawing parameters as well would count it twice; it is also cheap (no refit per path) | `backtest.bootstrap_draws` |
| Lead-time demand is `forecast over the interval x simulated ratio`, simulated in log space | Right-skewed, never negative, and the exponentiated forecast is not mistaken for the mean | n/a |
| Fewer than 6 own backtest windows: borrow from series of the same product, then from all series, spread widened by 1.25, and flagged | Few windows give a noisy tail | `backtest.min_windows`, `borrow_widen` |
| Fewer than 6 windows anywhere: normal approximation with a default coefficient of variation, labelled "normal fallback" | Last resort | `backtest.fallback_cv` |
| Coverage is checked in time order (each origin uses only windows already finished) and the spread is inflated by the smallest factor that restores coverage to within 3 points of the target, capped at 2.0 | Overconfident bands are the costly failure | `backtest.coverage_tolerance`, `max_inflation` |
| Default review period 1 month, lead time 1 month, service level 95%, holding 20% a year, ordering cost 100 USD | Data is monthly; the rest are not in the data, so each is tagged `default` and flagged on screen | `defaults` |
| Unit cost is estimated as median price per unit less the assumed margin | Gives costs in USD; the margin itself is an assumption, so the tag says so | `analytics.margin` |
| Service level is derived as Cu/(Cu+Co) only when a salvage value is entered | Spec: derive when price, cost and salvage are given | n/a |
| EOQ uses the average forecast over the order cycle, iterated at most 5 times; orders per year is not rounded | Spec | `policy.eoq_iterations` |
| A series with no unit cost gets safety stock and reorder point in units only | Never invent a cost | n/a |
| Perishable items, shelf life and newsvendor ordering are not built | No such data in the sample; deferred | n/a |

## Upload (built)
- `engine_ingest.py` reads CSV or XLSX (the Data sheet), suggests a column mapping from names and synonyms, lets the user fix it, and maps record
  types (purchase request, customer order, MDM setup, other). Unmapped record types are rejected with that reason, never guessed.
- Every row stays in the table with a reason when it is not used. The reconciliation always balances. Exact duplicates are flagged and counted
  unless the user ticks "Exclude exact duplicate rows" (conservative: nothing is removed silently).
- Gaps, short history, outliers and possible mixed units are reported and never repaired.
- Costs, price, salvage, ordering cost, pack size and MOQ columns become `estimated` assumptions (median of the file); receipt date less order date
  becomes the lead time. The spread of lead times is shown but not used yet.
- `on_hand`, `stockout_flag` and `received_qty` are read and reported as present but not used.
- Uploaded results are labelled with the file name, and the sample-data warning is replaced by an upload warning.

## Not built yet
- Stockout flagging and confirmation, and the efficacy replay with baselines.
- Assumption levels for country alone and product alone (global and per country-product are built).

## Limits to keep in view
- 24 months of history: at most 11 backtest windows for a 2-month protection interval, and 1 window above 7 months.
- Sample data was generated to match the forecast model, so results on it say nothing about real-world accuracy.
- Product-line level only; EOQ at that level is illustrative.
