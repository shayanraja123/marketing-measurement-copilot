-- Google Ads (paid search): one row per day, campaign and region.
-- Fixes: column names with spaces, text-to-number casts, stray spaces in campaign names.
-- Casing variants ("SEARCH - BRAND") can't be repaired reliably here, so the reported name is kept
-- and the canonical campaign name comes from the campaign settings log in the marts.

with source as (
    select * from {{ source('raw', 'google_ads_daily') }}
),

renamed as (
    select
        cast("Day" as date)                    as date_day,
        'paid_search'                          as channel,
        "Campaign ID"                          as campaign_id,
        trim("Campaign")                       as campaign_name_reported,
        "Region"                               as region,
        cast("Cost" as decimal(12, 2))         as spend,
        try_cast("Impressions" as integer)     as impressions,
        cast("Clicks" as integer)              as clicks,
        cast("Conversions" as integer)         as platform_conversions,
        cast("Conv. value" as decimal(14, 2))  as platform_conversion_value
    from source
)

select
    concat_ws('|', cast(date_day as varchar), campaign_id, region) as ad_spend_id,
    *
from renamed
