-- Campaign dimension, SCD Type 2: one row per VERSION of a campaign.
-- Each time a campaign's name, targeting or daily budget changed, a new version starts and the
-- previous one ends the day before. Joining spend on (campaign_id, date between valid_from and
-- valid_to) gives the settings the campaign actually had on that day.
--
-- Why not a dbt snapshot? Snapshots build history by checking a table that only shows the
-- CURRENT state, run after run. Here the platform already gives us the full change log,
-- so the history can be built directly from it.

with changes as (
    select * from {{ ref('stg_ads__campaign_settings') }}
),

versions as (
    select
        campaign_change_id                                                  as campaign_version_id,
        campaign_id,
        channel,
        campaign_name,
        targeting,
        daily_budget,
        row_number() over (partition by campaign_id order by updated_at)     as version_number,
        cast(updated_at as date)                                            as valid_from,
        {{ add_days('lead(updated_at) over (partition by campaign_id order by updated_at)', -1) }}
                                                                            as valid_to
    from changes
)

select
    *,
    valid_to is null as is_current
from versions
