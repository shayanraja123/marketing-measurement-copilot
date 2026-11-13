-- One row per product.

select
    product_id,
    product_name,
    category,
    current_price
from {{ ref('stg_shop__products') }}
