-- One row per product, with its current price.

select
    product_id,
    product_name,
    category,
    {{ to_money('current_price') }} as current_price
from {{ source('raw', 'shop_products') }}
