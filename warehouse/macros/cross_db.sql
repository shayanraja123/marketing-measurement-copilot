{#
  Cross-database macros: the same model SQL runs on DuckDB (local, target "dev") and BigQuery
  (cloud, target "prod"). Each macro below hides one place where the two dialects differ.

  How it works: adapter.dispatch() picks the version for the current database.
  "default__" is the DuckDB version; "bigquery__" is used when running on BigQuery.
#}


{# ---------- types ---------- #}

{% macro string_type() %}{{ return(adapter.dispatch('string_type')()) }}{% endmacro %}
{% macro default__string_type() %}varchar{% endmacro %}
{% macro bigquery__string_type() %}string{% endmacro %}

{# Money: exact decimal with 2 places in DuckDB; NUMERIC (exact) in BigQuery. #}
{% macro to_money(expr) %}{{ return(adapter.dispatch('to_money')(expr)) }}{% endmacro %}
{% macro default__to_money(expr) %}cast({{ expr }} as decimal(14, 2)){% endmacro %}
{% macro bigquery__to_money(expr) %}cast({{ expr }} as numeric){% endmacro %}

{# Integer that is NULL instead of an error when the text is blank or not a number. #}
{% macro try_int(expr) %}{{ return(adapter.dispatch('try_int')(expr)) }}{% endmacro %}
{% macro default__try_int(expr) %}try_cast({{ expr }} as integer){% endmacro %}
{% macro bigquery__try_int(expr) %}safe_cast({{ expr }} as int64){% endmacro %}


{# ---------- keys ---------- #}

{# Joins several columns into one text key, e.g. 2025-01-01|META-001|texas #}
{% macro make_key(columns) -%}
    concat(
    {%- for col in columns -%}
        cast({{ col }} as {{ string_type() }}){% if not loop.last %}, '|', {% endif %}
    {%- endfor -%}
    )
{%- endmacro %}


{# ---------- raw column names ---------- #}

{#
  Google Ads headers contain spaces and dots ("Campaign ID", "Conv. value").
  DuckDB reads the file directly and keeps them, so they need double quotes.
  BigQuery column names can't contain those characters, so the loader renamed them to
  snake_case (campaign_id, conv_value). This macro writes the right name for each database.
#}
{% macro raw_col(name) -%}
    {%- if target.type == 'bigquery' -%}
        {{ modules.re.sub('[^0-9a-zA-Z]+', '_', name).strip('_').lower() }}
    {%- else -%}
        "{{ name }}"
    {%- endif -%}
{%- endmacro %}


{# ---------- dates ---------- #}

{# Date arithmetic: add (or subtract, with a negative n) whole days to a date. #}
{% macro add_days(expr, n) %}{{ return(adapter.dispatch('add_days')(expr, n)) }}{% endmacro %}
{% macro default__add_days(expr, n) %}(cast({{ expr }} as date) + ({{ n }})){% endmacro %}
{% macro bigquery__add_days(expr, n) %}date_add(cast({{ expr }} as date), interval {{ n }} day){% endmacro %}

{#
  The display export gives the END of the brand's local day as a UTC timestamp,
  e.g. local Oct 1 ends at 2024-10-02T06:59:59Z. This returns the local date (Oct 1).
  DuckDB: Arizona is UTC-7 all year (no daylight saving), so subtract 7 hours.
  BigQuery: convert with the time-zone database directly.
#}
{% macro utc_to_phoenix_date(expr) %}{{ return(adapter.dispatch('utc_to_phoenix_date')(expr)) }}{% endmacro %}
{% macro default__utc_to_phoenix_date(expr) -%}
    cast(strptime({{ expr }}, '%Y-%m-%dT%H:%M:%SZ') - interval 7 hour as date)
{%- endmacro %}
{% macro bigquery__utc_to_phoenix_date(expr) -%}
    date(parse_timestamp('%Y-%m-%dT%H:%M:%SZ', {{ expr }}), 'America/Phoenix')
{%- endmacro %}

{# ISO day of week: 1 = Monday ... 7 = Sunday #}
{% macro iso_day_of_week(expr) %}{{ return(adapter.dispatch('iso_day_of_week')(expr)) }}{% endmacro %}
{% macro default__iso_day_of_week(expr) %}isodow({{ expr }}){% endmacro %}
{% macro bigquery__iso_day_of_week(expr) %}mod(extract(dayofweek from {{ expr }}) + 5, 7) + 1{% endmacro %}

{% macro day_name(expr) %}{{ return(adapter.dispatch('day_name')(expr)) }}{% endmacro %}
{% macro default__day_name(expr) %}strftime({{ expr }}, '%A'){% endmacro %}
{% macro bigquery__day_name(expr) %}format_date('%A', {{ expr }}){% endmacro %}

{# Monday of the week the date falls in #}
{% macro week_start(expr) %}{{ return(adapter.dispatch('week_start')(expr)) }}{% endmacro %}
{% macro default__week_start(expr) %}cast(date_trunc('week', {{ expr }}) as date){% endmacro %}
{% macro bigquery__week_start(expr) %}date_trunc({{ expr }}, isoweek){% endmacro %}

{# One row per day from start_date to end_date (columns of the given relation or CTE). #}
{% macro date_spine(bounds) %}{{ return(adapter.dispatch('date_spine')(bounds)) }}{% endmacro %}
{% macro default__date_spine(bounds) -%}
    select cast(day_ts as date) as date_day
    from (
        select unnest(generate_series(
            cast(start_date as timestamp), cast(end_date as timestamp), interval 1 day
        )) as day_ts
        from {{ bounds }}
    )
{%- endmacro %}
{% macro bigquery__date_spine(bounds) -%}
    select date_day
    from {{ bounds }}, unnest(generate_date_array(start_date, end_date)) as date_day
{%- endmacro %}


{# ---------- lists ---------- #}

{# Turns "a;b;c" in one row into three rows. Used as a join: from t {{ explode(...) }} #}
{% macro explode(expr, delimiter, alias) %}{{ return(adapter.dispatch('explode')(expr, delimiter, alias)) }}{% endmacro %}
{% macro default__explode(expr, delimiter, alias) -%}
    cross join unnest(string_split({{ expr }}, '{{ delimiter }}')) as exploded({{ alias }})
{%- endmacro %}
{% macro bigquery__explode(expr, delimiter, alias) -%}
    cross join unnest(split({{ expr }}, '{{ delimiter }}')) as {{ alias }}
{%- endmacro %}
