-- Display DSP: one row per day, campaign and region.
-- Fix: the export gives the END of the brand's local day as a UTC timestamp
-- (local Oct 1 ends at 2024-10-02T06:59:59Z). Casting that straight to a date would shift every
-- row one day later. utc_to_phoenix_date() converts it to the brand's local date
-- (America/Phoenix; see macros/cross_db.sql for how each database does it).
-- Known gap: a few days are missing from this export. Missing is not the same as zero spend,
-- so nothing is filled in here; the gap is flagged in mart_mmm_daily.

with source as (
    select * from {{ source('raw', 'dsp_display_daily') }}
),

renamed as (
    select
        {{ utc_to_phoenix_date('timestamp_utc') }}     as date_day,
        'display'                                      as channel,
        campaign_id,
        trim(campaign_name)                            as campaign_name_reported,
        geo                                            as region,
        {{ to_money('media_cost') }}                   as spend,
        {{ try_int('impressions') }}                   as impressions,
        cast(clicks as integer)                        as clicks,
        cast(conversions as integer)                   as platform_conversions,
        {{ to_money('conversion_revenue') }}           as platform_conversion_value
    from source
)

select
    {{ make_key(['date_day', 'campaign_id', 'region']) }} as ad_spend_id,
    *
from renamed
