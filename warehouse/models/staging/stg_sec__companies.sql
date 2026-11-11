select
    cik,
    name as sec_name,
    tickers,
    tickers[1] as ticker,
    exchange,
    sic,
    sic_description,
    former_names,
    fiscal_year_end,
    state_of_incorporation,
    universe_stratum,
    rd_expense_cy2024
from {{ source('raw', 'sec_companies') }}
