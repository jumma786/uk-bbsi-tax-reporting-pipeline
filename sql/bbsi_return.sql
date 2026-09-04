-- BBSI return: the same logic as src/, expressed in SQL.
--
-- Written for SQL Server / Azure SQL. The window functions, CTEs and the
-- FULL OUTER JOIN reconciliation port to Snowflake and Teradata unchanged;
-- only DATEFROMPARTS and the string concatenation are dialect-specific.
--
-- Run against the CSVs in output/ loaded as staging tables, or point the
-- source CTEs at real warehouse tables.

---------------------------------------------------------------------------
-- 1. Tax year boundary
--    Interest posted 5 April belongs to the closing year; 6 April opens the
--    next. Derived once here so no downstream query re-implements it.
---------------------------------------------------------------------------
DECLARE @tax_year_start_year INT = 2025;

DECLARE @ty_start DATE = DATEFROMPARTS(@tax_year_start_year,     4, 6);
DECLARE @ty_end   DATE = DATEFROMPARTS(@tax_year_start_year + 1, 4, 5);

---------------------------------------------------------------------------
-- 2. The return
---------------------------------------------------------------------------
WITH postings_classified AS (
    -- Classify rather than filter, so excluded value stays countable.
    SELECT
        p.posting_id,
        p.account_id,
        p.posted_on,
        p.gross_interest,
        CASE
            WHEN p.posted_on BETWEEN @ty_start AND @ty_end THEN 'in_year'
            WHEN p.posted_on <  @ty_start                  THEN 'before_year'
            ELSE                                                'after_year'
        END AS year_bucket
    FROM postings AS p
),

account_interest AS (
    SELECT
        account_id,
        SUM(gross_interest) AS account_gross_interest
    FROM postings_classified
    WHERE year_bucket = 'in_year'
    GROUP BY account_id
),

account_holders AS (
    -- COUNT(*) OVER (PARTITION BY ...) gives every row the account's holder
    -- count without a second pass or a self-join.
    SELECT
        a.account_id,
        a.holder_id,
        a.product,
        a.is_isa,
        COUNT(*) OVER (PARTITION BY a.account_id) AS n_holders
    FROM (SELECT DISTINCT account_id, holder_id, product, is_isa FROM accounts) AS a
),

allocated AS (
    -- Joint accounts split equally. Rounding to pence happens exactly once.
    SELECT
        ah.account_id,
        ah.holder_id,
        ah.is_isa,
        ah.n_holders,
        ROUND(ai.account_gross_interest / ah.n_holders, 2) AS allocated_interest
    FROM account_holders AS ah
    INNER JOIN account_interest AS ai
        ON ai.account_id = ah.account_id
),

holder_exceptions AS (
    -- Structural NINO checks. A blocking fault keeps the holder off the
    -- return entirely; advisory faults only populate the remediation queue.
    SELECT
        h.holder_id,
        CASE
            WHEN h.nino IS NULL OR LTRIM(RTRIM(h.nino)) = ''       THEN 'nino_missing'
            WHEN LEN(REPLACE(h.nino, ' ', '')) <> 9                THEN 'nino_malformed'
            WHEN LEFT(h.nino, 2) IN ('BG','GB','KN','NK','NT','TN','ZZ')
                                                                   THEN 'nino_malformed'
            WHEN SUBSTRING(h.nino, 1, 1) IN ('D','F','I','Q','U','V')
                                                                   THEN 'nino_malformed'
            WHEN SUBSTRING(h.nino, 2, 1) IN ('D','F','I','O','Q','U','V')
                                                                   THEN 'nino_malformed'
            WHEN RIGHT(h.nino, 1) NOT IN ('A','B','C','D')         THEN 'nino_malformed'
            ELSE NULL
        END AS blocking_check,
        CASE WHEN h.date_of_birth  IS NULL THEN 1 ELSE 0 END AS dob_missing,
        CASE WHEN h.postcode       IS NULL
               OR LTRIM(RTRIM(h.postcode)) = ''
               OR h.address_line_1 IS NULL
               OR LTRIM(RTRIM(h.address_line_1)) = '' THEN 1 ELSE 0 END AS address_incomplete
    FROM holders AS h
),

reportable AS (
    SELECT
        al.holder_id,
        SUM(al.allocated_interest) AS gross_interest_reported
    FROM allocated AS al
    INNER JOIN holder_exceptions AS he
        ON he.holder_id = al.holder_id
    WHERE al.is_isa = 0              -- ISA interest is exempt
      AND he.blocking_check IS NULL  -- blocking exceptions do not get filed
    GROUP BY al.holder_id
    HAVING SUM(al.allocated_interest) > 0
)

SELECT
    r.holder_id,
    h.first_name,
    h.last_name,
    h.nino,
    h.date_of_birth,
    h.address_line_1,
    h.town,
    h.postcode,
    r.gross_interest_reported,
    -- Banding supports the "who are the largest reportable persons" question
    -- that always follows the return.
    NTILE(10) OVER (ORDER BY r.gross_interest_reported DESC) AS interest_decile
FROM reportable AS r
INNER JOIN holders AS h
    ON h.holder_id = r.holder_id
ORDER BY r.gross_interest_reported DESC;


---------------------------------------------------------------------------
-- 3. Reconciliation waterfall
--    Every line is a stated reason the return differs from the ledger. The
--    residual must fall inside a tolerance derived from the number of
--    rounding events, not a flat cash figure. See README.
---------------------------------------------------------------------------
WITH ledger AS (
    SELECT SUM(gross_interest) AS total FROM postings
),
out_of_year AS (
    SELECT SUM(p.gross_interest) AS total
    FROM postings AS p
    WHERE p.posted_on < @ty_start OR p.posted_on > @ty_end
),
reported AS (
    SELECT SUM(gross_interest_reported) AS total FROM bbsi_return
)
SELECT 'Ledger gross interest'          AS line, (SELECT total FROM ledger)       AS amount_gbp
UNION ALL
SELECT 'Postings outside the tax year', -(SELECT total FROM out_of_year)
UNION ALL
SELECT 'Reported on return',            -(SELECT total FROM reported)
UNION ALL
SELECT 'Unexplained residual',
         (SELECT total FROM ledger)
       - (SELECT total FROM out_of_year)
       - (SELECT total FROM reported);


---------------------------------------------------------------------------
-- 4. Feed reconciliation: two sources that should agree, and do not
--    The pattern behind "identify integration data anomalies where feeds
--    disagree". A FULL OUTER JOIN is the only join that surfaces breaks in
--    BOTH directions; an INNER JOIN hides exactly the rows you are looking
--    for, and a LEFT JOIN hides half of them.
---------------------------------------------------------------------------
WITH warehouse_a AS (
    SELECT account_id, SUM(gross_interest) AS interest FROM postings GROUP BY account_id
),
warehouse_b AS (
    SELECT account_id, SUM(gross_interest) AS interest FROM postings_replica GROUP BY account_id
)
SELECT
    COALESCE(a.account_id, b.account_id) AS account_id,
    a.interest                            AS interest_a,
    b.interest                            AS interest_b,
    CASE
        WHEN a.account_id IS NULL                              THEN 'missing_in_a'
        WHEN b.account_id IS NULL                              THEN 'missing_in_b'
        WHEN ABS(a.interest - b.interest) > 0.01               THEN 'value_disagrees'
        ELSE                                                        'agrees'
    END AS break_type,
    ROUND(COALESCE(a.interest, 0) - COALESCE(b.interest, 0), 2) AS difference
FROM warehouse_a AS a
FULL OUTER JOIN warehouse_b AS b
    ON a.account_id = b.account_id
WHERE a.account_id IS NULL
   OR b.account_id IS NULL
   OR ABS(a.interest - b.interest) > 0.01
ORDER BY ABS(COALESCE(a.interest, 0) - COALESCE(b.interest, 0)) DESC;
