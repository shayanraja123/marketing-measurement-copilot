-- One row per calendar day in the data, with calendar attributes and the brand's promo events.
-- The promo dates come from the seed file seeds/promo_calendar.csv: the brand knows its own
-- sales calendar, and the media mix model needs it to avoid crediting ads for promo-driven sales.

with bounds as (
    select
        min(order_date) as start_date,
        max(order_date) as end_date
    from {{ ref('stg_shop__orders') }}
),

spine as (
    select unnest(generate_series(
        cast(start_date as timestamp),
        cast(end_date as timestamp),
        interval 1 day
    )) as day_ts
    from bounds
),

days as (
    select cast(day_ts as date) as date_day
    from spine
),

promos as (
    select * from {{ ref('promo_calendar') }}
)

select
    d.date_day,
    year(d.date_day)                        as calendar_year,
    quarter(d.date_day)                     as calendar_quarter,
    month(d.date_day)                       as calendar_month,
    isodow(d.date_day)                      as iso_day_of_week,   -- 1 = Monday, 7 = Sunday
    dayname(d.date_day)                     as day_name,
    cast(date_trunc('week', d.date_day) as date) as week_start,
    isodow(d.date_day) in (6, 7)            as is_weekend,
    p.event_name                            as promo_event,
    p.event_name is not null                as is_promo_day
from days as d
left join promos as p
    on d.date_day between p.start_date and p.end_date
