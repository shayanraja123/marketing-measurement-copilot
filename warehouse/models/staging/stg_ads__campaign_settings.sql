-- One row per change to a campaign's name, targeting or daily budget.
-- This change log becomes the slowly changing campaign dimension (SCD Type 2) in the marts.

select
    {{ make_key(['campaign_id', 'updated_at']) }}  as campaign_change_id,
    campaign_id,
    channel,
    campaign_name,
    targeting,
    {{ to_money('daily_budget') }}                 as daily_budget,
    cast(updated_at as timestamp)                  as updated_at
from {{ source('raw', 'campaign_settings_log') }}
