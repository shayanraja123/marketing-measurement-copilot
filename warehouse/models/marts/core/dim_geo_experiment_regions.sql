-- One row per region that was a treatment region in a geo holdout test.
-- The raw log stores regions as "midwest;texas;pacific_nw"; this splits them into rows
-- so tests and models can join on region.

with experiments as (
    select * from {{ ref('stg_geo_experiments') }}
),

exploded as (
    select
        e.experiment_id,
        e.channel,
        region,
        e.start_date,
        e.end_date
    from experiments as e
    {{ explode('e.treatment_regions', ';', 'region') }}
)

select
    {{ make_key(['experiment_id', 'region']) }} as experiment_region_id,
    *
from exploded
