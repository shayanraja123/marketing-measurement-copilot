-- All five ad platforms stacked into one table with the same columns.
-- The Jinja loop writes one SELECT per platform and joins them with UNION ALL;
-- adding a sixth platform means adding one name to this list.

{% set platform_models = [
    'stg_google_ads__daily',
    'stg_meta_ads__daily',
    'stg_tiktok_ads__daily',
    'stg_display__daily',
    'stg_email__daily',
] %}

{% for model_name in platform_models %}
select
    ad_spend_id,
    date_day,
    channel,
    campaign_id,
    campaign_name_reported,
    region,
    spend,
    impressions,
    clicks,
    platform_conversions,
    platform_conversion_value
from {{ ref(model_name) }}
{% if not loop.last %}union all{% endif %}
{% endfor %}
