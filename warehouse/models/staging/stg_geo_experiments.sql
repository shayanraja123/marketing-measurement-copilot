-- One row per geo holdout test. Treatment regions stay as a ";"-separated list here
-- and are split into one row per region in the marts.

select
    experiment_id,
    channel,
    treatment_regions,
    cast(start_date as date) as start_date,
    cast(end_date as date)   as end_date,
    design
from {{ source('raw', 'geo_experiments') }}
