-- SCD Type 2 integrity: every campaign must have exactly one current version.
-- Returns the campaigns that break the rule (zero rows = pass).

select
    campaign_id,
    count(*) filter (where is_current) as current_versions
from {{ ref('dim_campaigns') }}
group by campaign_id
having count(*) filter (where is_current) != 1
