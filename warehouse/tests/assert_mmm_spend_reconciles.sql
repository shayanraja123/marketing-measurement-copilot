-- Reconciliation test: the MMM table must contain exactly the same total spend as the fact table.
-- If a join drops or duplicates rows anywhere, the totals differ and this returns a row (fails).

with mart as (
    select sum(spend_paid_search + spend_paid_social + spend_tiktok + spend_display + spend_email) as total_spend
    from {{ ref('mart_mmm_daily') }}
),

fact as (
    select sum(spend) as total_spend
    from {{ ref('fct_ad_spend_daily') }}
)

select
    mart.total_spend as mart_total_spend,
    fact.total_spend as fact_total_spend
from mart, fact
where abs(mart.total_spend - fact.total_spend) > 0.01
