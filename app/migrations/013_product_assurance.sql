ALTER TABLE product_releases ADD COLUMN assurance_version integer NOT NULL DEFAULT 0;
ALTER TABLE product_releases ADD COLUMN verification jsonb;
CREATE TABLE product_evidence (
    operation_id uuid PRIMARY KEY,
    release_id uuid NOT NULL REFERENCES product_releases(id),
    receipt jsonb NOT NULL,
    report_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE product_outputs (
    id uuid PRIMARY KEY,
    release_id uuid NOT NULL REFERENCES product_releases(id),
    operation_id uuid NOT NULL REFERENCES product_evidence(operation_id),
    path text NOT NULL,
    sha256 text NOT NULL,
    content bytea NOT NULL CHECK(octet_length(content)<=1048576),
    UNIQUE(operation_id,path)
);
