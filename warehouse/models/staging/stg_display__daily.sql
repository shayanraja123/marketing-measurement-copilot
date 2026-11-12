-- Display DSP: one row per day, campaign and region.
-- Fix: the export gives the END of the brand's local day as a UTC timestamp
-- (local Oct 1 ends at 2024-10-02T06:59:59Z). Casting that straight to a date would shift every
-- row one day later. The brand reports in America/Phoenix, which is UTC-7 all year (Arizona has
-- no daylight saving time), so subtracting 7 hours gives the correct local date.
-- Known gap: a few days are missing from this export. Missing is not the same as zero spend,
-- so nothing is filled in here; the gap is handled when daily data is modeled.

with source as (
    select * from {{ source('raw', 'dsp_display_daily') }}
),

renamed as (
    select
        cast(strptime(timestamp_utc, '%Y-%m-%dT%H:%M:%SZ') - interval 7 hour as date) as date_day,
        'display'                                       as channel,
        campaign_id,
        trim(campaign_name)                             as campaign_name_reported,
        geo                                             as region,
        cast(media_cost as decimal(12, 2))              as spend,
        try_cast(impressions as integer)                as impressions,
        cast(clicks as integer)                         as clicks,
        cast(conversions as integer)                    as platform_conversions,
        cast(conversion_revenue as decimal(14, 2))      as platform_conversion_value
    from source
)

select
    concat_ws('|', cast(date_day as varchar), campaign_id, region) as ad_spend_id,
    *
from renamed
