select
    sponsor_name,
    cik,
    link_method,
    link_score,
    matched_alias,
    alias_source
from {{ source('raw', 'sponsor_links') }}
