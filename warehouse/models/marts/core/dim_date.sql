-- One row per calendar day in the data, with calendar attributes and the brand's promo events.
-- The promo dates come from the seed file seeds/promo_calendar.csv: the brand knows its own
-- sales calendar, and the media mix model needs it to avoid crediting ads for promo-driven sales.

with bounds as (
    select
        min(order_date) as start_date,
        max(order_date) as end_date
    from {{ ref('stg_shop__orders') }}
),

days as (
    {{ date_spine('bounds') }}
),

promos as (
    select * from {{ ref('promo_calendar') }}
)

select
    d.date_day,
    extract(year from d.date_day)          as calendar_year,
    extract(quarter from d.date_day)       as calendar_quarter,
    extract(month from d.date_day)         as calendar_month,
    {{ iso_day_of_week('d.date_day') }}    as iso_day_of_week,   -- 1 = Monday, 7 = Sunday
    {{ day_name('d.date_day') }}           as day_name,
    {{ week_start('d.date_day') }}         as week_start,
    {{ iso_day_of_week('d.date_day') }} in (6, 7) as is_weekend,
    p.event_name                           as promo_event,
    p.event_name is not null               as is_promo_day
from days as d
left join promos as p
    on d.date_day between p.start_date and p.end_date
