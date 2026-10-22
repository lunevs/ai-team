CREATE SCHEMA synthetic;
CREATE TABLE synthetic.dim_customer (
 customer_id int PRIMARY KEY, region text NOT NULL, segment text NOT NULL
);
CREATE TABLE synthetic.dim_product (product_id int PRIMARY KEY, name text NOT NULL);
CREATE TABLE synthetic.fact_loan (
 loan_id int PRIMARY KEY, customer_id int NOT NULL REFERENCES synthetic.dim_customer,
 product_id int NOT NULL REFERENCES synthetic.dim_product,
 origination_date date NOT NULL, principal numeric(18,2) NOT NULL CHECK(principal>=0),
 annual_rate numeric(8,6) NOT NULL CHECK(annual_rate>=0)
);
CREATE TABLE synthetic.dim_calendar (date date PRIMARY KEY, month text NOT NULL,is_month_end boolean NOT NULL);
CREATE TABLE synthetic.fact_loan_daily (
 loan_id int REFERENCES synthetic.fact_loan, date date REFERENCES synthetic.dim_calendar,
 outstanding_balance numeric(18,2) NOT NULL CHECK(outstanding_balance>=0),
 dpd int NOT NULL CHECK(dpd>=0), accrued_interest numeric(18,2) NOT NULL,
 interest_income numeric(18,2) NOT NULL, PRIMARY KEY(loan_id,date)
);
CREATE TABLE synthetic.dataset_info (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),version text NOT NULL,
 seed int NOT NULL, checksum text NOT NULL, row_counts jsonb NOT NULL
);
GRANT USAGE ON SCHEMA synthetic TO ai_team_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA synthetic TO ai_team_runtime;
