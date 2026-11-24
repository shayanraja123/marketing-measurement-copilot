-- One row per product price and the date it took effect.

select
    {{ make_key(['product_id', 'valid_from']) }} as price_change_id,
    product_id,
    {{ to_money('price') }}                      as price,
    cast(valid_from as date)                     as valid_from
from {{ source('raw', 'shop_price_history') }}
