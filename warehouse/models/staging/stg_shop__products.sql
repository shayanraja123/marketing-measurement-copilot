-- One row per product, with its current price.

select
    product_id,
    product_name,
    category,
    cast(current_price as decimal(10, 2)) as current_price
from {{ source('raw', 'shop_products') }}
