-- One row per sales region.

select
    region,
    region_name,
    timezone
from {{ ref('stg_regions') }}
