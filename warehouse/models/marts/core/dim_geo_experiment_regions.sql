-- One row per region that was a treatment region in a geo holdout test.
-- The raw log stores regions as "midwest;texas;pacific_nw"; this splits them into rows
-- so tests and models can join on region.

with experiments as (
    select * from {{ ref('stg_geo_experiments') }}
),

exploded as (
    select
        experiment_id,
        channel,
        unnest(string_split(treatment_regions, ';')) as region,
        start_date,
        end_date
    from experiments
)

select
    concat_ws('|', experiment_id, region) as experiment_region_id,
    *
from exploded
