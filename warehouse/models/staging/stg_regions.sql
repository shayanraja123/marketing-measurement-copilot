-- One row per sales region.

select
    region,
    region_name,
    timezone
from {{ source('raw', 'regions') }}
