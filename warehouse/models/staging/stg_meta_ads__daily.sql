-- Meta Ads (paid social): one row per day, campaign and region.
-- Fixes: duplicate rows from the export (keep one row per day, campaign and region),
-- blank impression counts become NULL instead of failing the cast.
-- Some duplicate pairs differ: one copy has impressions, the other is blank. Deduplicating on the
-- grain (not with DISTINCT) catches those too, and the ORDER BY keeps the more complete copy,
-- which also makes the result the same on every run.

with source as (
    select * from {{ source('raw', 'meta_ads_daily') }}
),

deduplicated as (
    select *
    from source
    where true  -- BigQuery requires a WHERE (or GROUP BY) clause alongside QUALIFY
    qualify row_number() over (
        partition by date_start, campaign_id, region
        order by {{ try_int('impressions') }} is null, date_start
    ) = 1
),

renamed as (
    select
        cast(date_start as date)                  as date_day,
        'paid_social'                             as channel,
        campaign_id,
        trim(campaign_name)                       as campaign_name_reported,
        region,
        {{ to_money('spend') }}                   as spend,
        {{ try_int('impressions') }}              as impressions,
        cast(clicks as integer)                   as clicks,
        cast(purchases as integer)                as platform_conversions,
        {{ to_money('purchase_value') }}          as platform_conversion_value
    from deduplicated
)

select
    {{ make_key(['date_day', 'campaign_id', 'region']) }} as ad_spend_id,
    *
from renamed
