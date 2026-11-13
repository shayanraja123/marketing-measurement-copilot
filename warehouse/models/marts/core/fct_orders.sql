-- Fact table: one row per store order. This is what the brand actually sold,
-- as opposed to what ad platforms claim.

with orders as (
    select * from {{ ref('stg_shop__orders') }}
),

customers as (
    select * from {{ ref('stg_shop__customers') }}
)

select
    o.order_id,
    o.order_ts,
    o.order_date,
    o.customer_id,
    o.region,
    o.product_id,
    o.quantity,
    o.unit_price,
    o.revenue,
    o.is_late_arriving,
    o.order_ts = c.first_order_ts as is_first_order
from orders as o
left join customers as c
    on o.customer_id = c.customer_id
