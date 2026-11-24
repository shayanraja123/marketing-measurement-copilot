-- SCD Type 2 integrity: every campaign must have exactly one current version.
-- Returns the campaigns that break the rule (zero rows = pass).

select
    campaign_id,
    sum(case when is_current then 1 else 0 end) as current_versions
from {{ ref('dim_campaigns') }}
group by campaign_id
having sum(case when is_current then 1 else 0 end) != 1
