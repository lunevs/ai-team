WITH months AS (
    SELECT DISTINCT month
    FROM synthetic.dim_calendar
    WHERE is_month_end
),
month_end AS (
    SELECT c.month, c.date AS snapshot_date
    FROM synthetic.dim_calendar c
    WHERE c.is_month_end
),
grid AS (
    SELECT m.month, p.product_id, r.region
    FROM months m
    CROSS JOIN (SELECT DISTINCT product_id FROM synthetic.dim_product) p
    CROSS JOIN (SELECT DISTINCT region FROM synthetic.dim_customer) r
),
snapshot AS (
    SELECT
        me.month,
        fl.product_id,
        dc.region,
        fld.loan_id,
        fld.outstanding_balance,
        fld.dpd
    FROM month_end me
    JOIN synthetic.fact_loan_daily fld
        ON fld.date = me.snapshot_date
    JOIN synthetic.fact_loan fl
        ON fl.loan_id = fld.loan_id
    JOIN synthetic.dim_customer dc
        ON dc.customer_id = fl.customer_id
),
snap_agg AS (
    SELECT
        month,
        product_id,
        region,
        round(sum(outstanding_balance), 2) AS balance,
        round(sum(CASE WHEN dpd >= 30 THEN outstanding_balance ELSE 0 END), 2) AS dpd30_balance,
        round(sum(CASE WHEN dpd >= 90 THEN outstanding_balance ELSE 0 END), 2) AS dpd90_balance
    FROM snapshot
    GROUP BY month, product_id, region
),
income AS (
    SELECT
        c.month,
        fl.product_id,
        dc.region,
        round(sum(fld.interest_income), 2) AS interest_income
    FROM synthetic.fact_loan_daily fld
    JOIN synthetic.dim_calendar c
        ON c.date = fld.date
    JOIN synthetic.fact_loan fl
        ON fl.loan_id = fld.loan_id
    JOIN synthetic.dim_customer dc
        ON dc.customer_id = fl.customer_id
    GROUP BY c.month, fl.product_id, dc.region
)
SELECT
    g.month,
    g.product_id,
    g.region,
    s.balance,
    s.dpd30_balance,
    s.dpd90_balance,
    CASE
        WHEN s.dpd90_balance IS NULL OR s.balance IS NULL OR s.balance = 0 THEN NULL
        ELSE round(s.dpd90_balance / s.balance, 6)
    END AS npl90_ratio,
    i.interest_income
FROM grid g
LEFT JOIN snap_agg s
    ON s.month = g.month AND s.product_id = g.product_id AND s.region = g.region
LEFT JOIN income i
    ON i.month = g.month AND i.product_id = g.product_id AND i.region = g.region
ORDER BY g.month, g.product_id, g.region;