-- Google Ads (paid search): one row per day, campaign and region.
-- Fixes: column names with spaces, text-to-number casts, stray spaces in campaign names.
-- Casing variants ("SEARCH - BRAND") can't be repaired reliably here, so the reported name is kept
-- and the canonical campaign name comes from the campaign settings log in the marts.
-- raw_col() writes each header the way the current database stores it (see macros/cross_db.sql).

with source as (
    select * from {{ source('raw', 'google_ads_daily') }}
),

renamed as (
    select
        cast({{ raw_col('Day') }} as date)                   as date_day,
        'paid_search'                                        as channel,
        {{ raw_col('Campaign ID') }}                         as campaign_id,
        trim({{ raw_col('Campaign') }})                      as campaign_name_reported,
        {{ raw_col('Region') }}                              as region,
        {{ to_money(raw_col('Cost')) }}                      as spend,
        {{ try_int(raw_col('Impressions')) }}                as impressions,
        cast({{ raw_col('Clicks') }} as integer)             as clicks,
        cast({{ raw_col('Conversions') }} as integer)        as platform_conversions,
        {{ to_money(raw_col('Conv. value')) }}               as platform_conversion_value
    from source
)

select
    {{ make_key(['date_day', 'campaign_id', 'region']) }} as ad_spend_id,
    *
from renamed
