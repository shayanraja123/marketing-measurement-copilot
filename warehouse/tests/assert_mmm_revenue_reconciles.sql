-- Reconciliation test: the MMM table must contain exactly the same total revenue as the orders fact.

with mart as (
    select sum(revenue) as total_revenue
    from {{ ref('mart_mmm_daily') }}
),

fact as (
    select sum(revenue) as total_revenue
    from {{ ref('fct_orders') }}
)

select
    mart.total_revenue as mart_total_revenue,
    fact.total_revenue as fact_total_revenue
from mart, fact
where abs(mart.total_revenue - fact.total_revenue) > 0.01
