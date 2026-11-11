select
    cik,
    sec_name as company_name,
    ticker,
    tickers,
    exchange,
    sic,
    sic_description,
    universe_stratum,
    rd_expense_cy2024,
    fiscal_year_end,
    state_of_incorporation
from {{ ref('stg_sec__companies') }}
