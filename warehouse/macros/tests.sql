{# Generic tests written here so the project needs no downloaded dbt packages. #}

{% test expression_is_true(model, expression, where_clause="1 = 1", column_name=None) %}
select *
from {{ model }}
where {{ where_clause }}
  and not ({{ expression }})
{% endtest %}

{% test unique_combination(model, columns) %}
select {{ columns | join(", ") }}, count(*) as n
from {{ model }}
group by {{ columns | join(", ") }}
having count(*) > 1
{% endtest %}
