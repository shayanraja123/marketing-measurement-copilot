-- One row per customer.

select
    customer_id,
    region,
    cast(first_order_ts as timestamp) as first_order_ts
from {{ source('raw', 'shop_customers') }}
