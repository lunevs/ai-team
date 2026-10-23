ALTER TABLE stories ADD COLUMN delivery_mode text NOT NULL DEFAULT 'credit_demo'
 CHECK(delivery_mode IN ('credit_demo','product'));
CREATE TABLE product_releases (
 id uuid PRIMARY KEY, story_id uuid NOT NULL REFERENCES stories(id), revision int NOT NULL,
 product_key text NOT NULL, version text NOT NULL, status text NOT NULL DEFAULT 'DEVELOPING',
 specification jsonb, package jsonb, qa jsonb, review jsonb, git jsonb,
 documentation_url text, deployment jsonb, accepted boolean NOT NULL DEFAULT false,
 operation text, operation_id uuid, lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(story_id,revision), UNIQUE(product_key,version)
);
