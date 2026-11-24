-- TikTok Ads: one row per day, campaign and region.
-- Fix: spend arrives as currency text ("$1,234.56"); strip "$" and "," before casting.

with source as (
    select * from {{ source('raw', 'tiktok_ads_daily') }}
),

renamed as (
    select
        cast(stat_time_day as date)                                   as date_day,
        'tiktok'                                                      as channel,
        campaign_id,
        trim(campaign_name)                                           as campaign_name_reported,
        region,
        {{ to_money("replace(replace(spend, '$', ''), ',', '')") }}   as spend,
        {{ try_int('impressions') }}                                  as impressions,
        cast(clicks as integer)                                       as clicks,
        cast(complete_payment as integer)                             as platform_conversions,
        {{ to_money('complete_payment_value') }}                      as platform_conversion_value
    from source
)

select
    {{ make_key(['date_day', 'campaign_id', 'region']) }} as ad_spend_id,
    *
from renamed
