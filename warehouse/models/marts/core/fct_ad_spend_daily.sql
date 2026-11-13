-- Fact table: ad spend and platform-reported results, one row per day, campaign and region.
-- Each row points to the campaign VERSION that was live that day (see dim_campaigns),
-- so names and settings always come from the official campaign log, joined on ID.

with spend as (
    select * from {{ ref('int_ad_spend__unioned') }}
),

campaign_versions as (
    select * from {{ ref('dim_campaigns') }}
)

select
    s.ad_spend_id,
    s.date_day,
    s.channel,
    s.campaign_id,
    v.campaign_version_id,
    s.region,
    s.spend,
    s.impressions,
    s.clicks,
    s.platform_conversions,
    s.platform_conversion_value
from spend as s
left join campaign_versions as v
    on  s.campaign_id = v.campaign_id
    and s.date_day >= v.valid_from
    and s.date_day <= coalesce(v.valid_to, date '9999-12-31')
