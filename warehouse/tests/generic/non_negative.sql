-- A reusable test: fails if any row has a negative value in the column.
-- Use it in a .yml file like any built-in test:  tests: [non_negative]

{% test non_negative(model, column_name) %}

select *
from {{ model }}
where {{ column_name }} < 0

{% endtest %}
