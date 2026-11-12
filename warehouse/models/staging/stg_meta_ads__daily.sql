-- Meta Ads (paid social): one row per day, campaign and region.
-- Fixes: duplicate rows from the export (keep one row per day, campaign and region),
-- blank impression counts become NULL instead of failing the cast.

with source as (
    select * from {{ source('raw', 'meta_ads_daily') }}
),

deduplicated as (
    select *
    from source
    qualify row_number() over (partition by date_start, campaign_id, region order by date_start) = 1
),

renamed as (
    select
        cast(date_start as date)                  as date_day,
        'paid_social'                             as channel,
        campaign_id,
        trim(campaign_name)                       as campaign_name_reported,
        region,
        cast(spend as decimal(12, 2))             as spend,
        try_cast(impressions as integer)          as impressions,
        cast(clicks as integer)                   as clicks,
        cast(purchases as integer)                as platform_conversions,
        cast(purchase_value as decimal(14, 2))    as platform_conversion_value
    from deduplicated
)

select
    concat_ws('|', cast(date_day as varchar), campaign_id, region) as ad_spend_id,
    *
from renamed
