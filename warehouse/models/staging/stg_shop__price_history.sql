-- One row per product price and the date it took effect.

select
    concat_ws('|', product_id, valid_from) as price_change_id,
    product_id,
    cast(price as decimal(10, 2))          as price,
    cast(valid_from as date)               as valid_from
from {{ source('raw', 'shop_price_history') }}
