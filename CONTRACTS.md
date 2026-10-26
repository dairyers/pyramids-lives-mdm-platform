# Interface contracts

Copy of spec Section 6 (`action_items_execution_game_plan_v3.txt`), unchanged, followed by the
additions made after the spec. No Section 6 name or signature has been renamed or changed.

## Spec Section 6 (verbatim)

```
REQUEST RECORD (dict, stored in st.session_state.requests_db):
  request_id, domain, sap_table, record_name, payload, status, requester_email,
  current_approver_role, current_approver_email, created_at, pending_since,
  last_updated_at, routing_rule_id, dup_matches (with tiers), dup_requester_answer, dup_override_justification,
  sla_R48_done, sla_R96_done, sla_R192_done, history (list of {ts, actor, action, from, to, comment})
SESSION KEYS: current_user_email, requests_db, audit_log, sap_master, sim_now
AUDIT ENTRY: {ts, actor_email, actor_role, event, request_id, detail}

engine_mdm.py
  load_config(path) -> dict
  validate_against_config(domain, payload, config) -> (bool, list[str])
  build_dynamic_aliases(existing_names, ab_cfg) -> dict
  resolve_name(text, ab_cfg, dynamic_aliases=None) -> (list[list[str]], list[dict])
  check_fuzzy_duplicates(domain, payload, existing_records, config) -> list[dict]   (each match carries tier, corroboration)
  corroborate(payload, existing, alias_tokens, corroboration_cfg) -> dict   (signals, conflicts, agreeing_count, tier)
  evidence_for_role(corroboration, role, corroboration_cfg) -> list[dict]   (labels for all roles; "detail" only for detail_visible_roles)
  assign_tiers(matches, payload, pool_by_id, alias_hits, dedup_cfg) -> list[dict]
  dup_review_required(matches, config, domain) -> bool
  route_request(domain, payload, config, dup_review=False) -> dict
  create_request(domain, payload, user, config) -> dict
  transition(request, action, user, config, comment=None) -> dict   (raises PermissionError)
engine_security.py
  get_user(email, config) -> dict
  apply_field_security(record, domain, role, config, status=None, channel="APP") -> (dict, dict)
  can_view_request(user, request, config) -> bool
  visible_tabs(role, config) -> list[str]
  capability_summary(role, config) -> list[str]
  security_lens(record, domain, config, status=None) -> pandas.DataFrame
m365_integration.py
  build_adaptive_card(request, recipient_role, config) -> dict
  process_sla_escalations(requests_db, config, now=None) -> list[dict]
  render_email_digest(requests, role, config) -> str
engine_analytics.py
  clean_transaction_data(raw_df, config) -> DataFrame
  generate_demand_forecast(clean_df, entity, country, product_line, config, model=None, lookback=None, horizon=None) -> DataFrame
      (Section 4.3 revised: Holt-Winters was removed. model, lookback and horizon are optional and default to
       analytics.forecast; the signature is otherwise unchanged and the columns are the same)
  calculate_churn_radar(clean_df, config, as_of=None) -> DataFrame
  calculate_cross_sell_opportunities(clean_df, config, as_of=None) -> (DataFrame, dict)

```

## Additions: Regional business view (engine_analytics.py)

Parameters live in `config_rules.json` under `analytics.regional_view`.

```
engine_analytics.py
  summarize_regional_volumes(clean_df, config, as_of=None) -> (DataFrame, dict)
      rows: one per country x product line (U_Country, country, U_ProductLine, product, current_qty, prior_qty,
            growth_pct, new_account_qty, growth_excl_new_pct, current_usd, prior_usd, usd_growth_pct)
      dict: current_start, current_end, prior_start, prior_end (ISO dates), window_months,
            prior_offset_months, prior_complete, definition
      growth_pct = (current - prior) / prior * 100 on confirmed-PO quantity; None when prior is 0
  summarize_request_types(raw_df, config) -> DataFrame
      (category, label, used_in_forecast, statuses, rows, quantity, value_usd, share_of_rows_pct);
      categories from analytics.noise_filter: CONFIRMED_USED, CONFIRMED_NOT_READY, and each other RecordType
  backtest_forecast_vs_baseline(clean_df, config) -> (DataFrame, dict)
      rows: one per eligible country x product series summed across entities (train_months, method_used,
            holdout_qty, wape_model, wape_avg<w>m for each baseline window)
      dict: holdout_months, baseline_windows_months, series, skipped, holdout_start, holdout_end,
            overall_wape_model, overall_wape_baseline{col}, improvement_pct{col}, model_wins{col},
            model_not_better{col}, methods_used, model_id, model_label, lookback_months
      (the most accurate data-science model at the configured history length vs the traditional averages)
  build_recommended_actions(results, config) -> list[str]
      results keys (each optional): regional, request_types, backtest, churn, cross_sell
  forecast_series(s, config, horizon=None, model=None, lookback=None, season=None) -> DataFrame
      (entity-agnostic core of generate_demand_forecast). Models, all usable from 6 to 12 months of history:
      rolling_avg, linear_trend, damped_trend, seasonal_level, seasonal_trend, blend (average of blend_of)
  seasonal_index(clean_df, config, upto=None) -> {month: factor} or None   (pooled across all countries and products)
  backtest_models(clean_df, config, lookback=None) -> (DataFrame, dict)    every model scored on the same hidden months
  backtest_matrix(clean_df, config) -> (DataFrame, {lookback: dict})       model x history length, plus the traditional averages
  build_model_observations(matrix, runs, config) -> list[str]              plain-English findings, numbers only from the matrix
```

## Other helpers added outside Section 6 (not part of the spec contract)

```
engine_analytics.py   churn_windows, series_keys, aggregate_counts
m365_integration.py   role_filtered_facts, next_escalation, digest_queue; process_sla_escalations and
                      simulate_webhook_response also accept keyword-only audit_log= / now=
tabs/common.py        shared Streamlit helpers (secure_view, lens_for_viewer, render_persona_view, ...)
```
