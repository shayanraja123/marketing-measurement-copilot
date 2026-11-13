-- The input table for the media mix model: one row per day and region, with actual store revenue,
-- spend for each channel side by side, and the flags the model needs.
-- Every day-region pair appears, even with no spend, because "no spend" is information too.

with dates as (
    select date_day, promo_event, is_promo_day
    from {{ ref('dim_date') }}
),

regions as (
    select region from {{ ref('dim_regions') }}
),

grid as (
    select d.date_day, r.region, d.promo_event, d.is_promo_day
    from dates as d
    cross join regions as r
),

sales as (
    select
        order_date   as date_day,
        region,
        count(*)     as orders,
        sum(revenue) as revenue
    from {{ ref('fct_orders') }}
    group by 1, 2
),

spend as (
    select
        date_day,
        region,
        sum(case when channel = 'paid_search' then spend end) as spend_paid_search,
        sum(case when channel = 'paid_social' then spend end) as spend_paid_social,
        sum(case when channel = 'tiktok'      then spend end) as spend_tiktok,
        sum(case when channel = 'display'     then spend end) as spend_display,
        sum(case when channel = 'email'       then spend end) as spend_email
    from {{ ref('fct_ad_spend_daily') }}
    group by 1, 2
),

display_days as (
    select distinct date_day
    from {{ ref('fct_ad_spend_daily') }}
    where channel = 'display'
),

-- A single day with no display rows, while display ran the day before and the day after,
-- is an export gap, not a decision to stop spending. (The planned three-week pause is a run
-- of many days, so it is not caught here.) The model will fill these days instead of
-- treating them as zero spend.
display_export_gaps as (
    select d.date_day
    from dates as d
    where d.date_day not in (select date_day from display_days)
      and d.date_day - 1 in (select date_day from display_days)
      and d.date_day + 1 in (select date_day from display_days)
),

holdout_days as (
    select distinct g.region, d.date_day
    from {{ ref('dim_geo_experiment_regions') }} as g
    join dates as d
        on d.date_day between g.start_date and g.end_date
)

select
    concat_ws('|', cast(g.date_day as varchar), g.region) as mmm_row_id,
    g.date_day,
    g.region,
    coalesce(s.revenue, 0)            as revenue,
    coalesce(s.orders, 0)             as orders,
    coalesce(sp.spend_paid_search, 0) as spend_paid_search,
    coalesce(sp.spend_paid_social, 0) as spend_paid_social,
    coalesce(sp.spend_tiktok, 0)      as spend_tiktok,
    coalesce(sp.spend_display, 0)     as spend_display,
    coalesce(sp.spend_email, 0)       as spend_email,
    g.promo_event,
    g.is_promo_day,
    gap.date_day is not null          as display_export_missing,
    h.date_day is not null            as in_geo_holdout
from grid as g
left join sales as s
    on s.date_day = g.date_day and s.region = g.region
left join spend as sp
    on sp.date_day = g.date_day and sp.region = g.region
left join display_export_gaps as gap
    on gap.date_day = g.date_day
left join holdout_days as h
    on h.date_day = g.date_day and h.region = g.region
