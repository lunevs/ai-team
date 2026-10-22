WITH months AS (
 SELECT DISTINCT month FROM synthetic.dim_calendar
), regions AS (
 SELECT DISTINCT region FROM synthetic.dim_customer
), grid AS (
 SELECT m.month,p.product_id,r.region FROM months m CROSS JOIN synthetic.dim_product p CROSS JOIN regions r
), daily AS (
 SELECT c.month,c.is_month_end,l.loan_id,l.product_id,u.region,d.outstanding_balance,d.dpd,d.interest_income
 FROM synthetic.fact_loan_daily d
 JOIN synthetic.fact_loan l ON l.loan_id=d.loan_id
 JOIN synthetic.dim_customer u ON u.customer_id=l.customer_id
 JOIN synthetic.dim_calendar c ON c.date=d.date
), snapshots AS (
 SELECT month,product_id,region,
 sum(outstanding_balance) AS balance,
 sum(CASE WHEN dpd>=30 THEN outstanding_balance ELSE 0 END) AS dpd30_balance,
 sum(CASE WHEN dpd>=90 THEN outstanding_balance ELSE 0 END) AS dpd90_balance
 FROM daily WHERE is_month_end GROUP BY month,product_id,region
), income AS (
 SELECT month,product_id,region,sum(interest_income) AS interest_income
 FROM daily GROUP BY month,product_id,region
)
SELECT g.month,g.product_id,g.region,
 round(s.balance,2) AS balance,round(s.dpd30_balance,2) AS dpd30_balance,
 round(s.dpd90_balance,2) AS dpd90_balance,
 round(s.dpd90_balance/nullif(s.balance,0),6) AS npl90_ratio,
 round(i.interest_income,2) AS interest_income
FROM grid g
LEFT JOIN snapshots s ON s.month=g.month AND s.product_id=g.product_id AND s.region=g.region
LEFT JOIN income i ON i.month=g.month AND i.product_id=g.product_id AND i.region=g.region
ORDER BY g.month,g.product_id,g.region
