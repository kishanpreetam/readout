{#
  ClinicalTrials.gov dates come as YYYY-MM-DD or, for many anticipated dates, YYYY-MM.
  A month-only date is read as the last day of that month, and its precision is kept in a separate column.
#}
{% macro ct_date(column) -%}
    case
        when {{ column }} is null then null
        when length({{ column }}) = 7 then last_day(strptime({{ column }} || '-01', '%Y-%m-%d')::date)
        when length({{ column }}) = 10 then strptime({{ column }}, '%Y-%m-%d')::date
    end
{%- endmacro %}

{% macro ct_date_precision(column) -%}
    case when {{ column }} is null then null when length({{ column }}) = 7 then 'month' else 'day' end
{%- endmacro %}
