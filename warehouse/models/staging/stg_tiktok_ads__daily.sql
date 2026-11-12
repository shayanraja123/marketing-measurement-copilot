-- TikTok Ads: one row per day, campaign and region.
-- Fix: spend arrives as currency text ("$1,234.56"); strip "$" and "," before casting.

with source as (
    select * from {{ source('raw', 'tiktok_ads_daily') }}
),

renamed as (
    select
        cast(stat_time_day as date)                                    as date_day,
        'tiktok'                                                       as channel,
        campaign_id,
        trim(campaign_name)                                            as campaign_name_reported,
        region,
        cast(replace(replace(spend, '$', ''), ',', '') as decimal(12, 2)) as spend,
        try_cast(impressions as integer)                               as impressions,
        cast(clicks as integer)                                        as clicks,
        cast(complete_payment as integer)                              as platform_conversions,
        cast(complete_payment_value as decimal(14, 2))                 as platform_conversion_value
    from source
)

select
    concat_ws('|', cast(date_day as varchar), campaign_id, region) as ad_spend_id,
    *
from renamed
