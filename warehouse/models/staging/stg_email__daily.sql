-- Email platform: one row per day, campaign and region.
-- "emails_sent" is used as impressions so all channels share one set of columns.

with source as (
    select * from {{ source('raw', 'esp_email_daily') }}
),

renamed as (
    select
        cast(send_date as date)                    as date_day,
        'email'                                    as channel,
        campaign_id,
        trim(campaign_name)                        as campaign_name_reported,
        region,
        {{ to_money('cost') }}                     as spend,
        {{ try_int('emails_sent') }}               as impressions,
        cast(clicks as integer)                    as clicks,
        cast(attributed_orders as integer)         as platform_conversions,
        {{ to_money('attributed_revenue') }}       as platform_conversion_value
    from source
)

select
    {{ make_key(['date_day', 'campaign_id', 'region']) }} as ad_spend_id,
    *
from renamed
