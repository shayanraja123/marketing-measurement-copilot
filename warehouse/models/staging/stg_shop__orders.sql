-- Store orders: one row per order.
-- Fix: some orders arrive in a later export file. Both files are combined, and if an order ever
-- appears in both, the most recently exported copy wins.

with main_export as (
    select
        order_id, order_ts, customer_id, region, product_id, quantity, unit_price, revenue,
        null as exported_at
    from {{ source('raw', 'shop_orders') }}
),

late_export as (
    select
        order_id, order_ts, customer_id, region, product_id, quantity, unit_price, revenue,
        exported_at
    from {{ source('raw', 'shop_orders_late') }}
),

combined as (
    select * from main_export
    union all
    select * from late_export
),

deduplicated as (
    select *
    from combined
    qualify row_number() over (partition by order_id order by exported_at desc nulls last) = 1
)

select
    order_id,
    cast(order_ts as timestamp)            as order_ts,
    cast(cast(order_ts as timestamp) as date) as order_date,
    customer_id,
    region,
    product_id,
    cast(quantity as integer)              as quantity,
    cast(unit_price as decimal(10, 2))     as unit_price,
    cast(revenue as decimal(12, 2))        as revenue,
    exported_at is not null                as is_late_arriving
from deduplicated
