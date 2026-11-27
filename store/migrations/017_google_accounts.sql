ALTER TABLE users ADD COLUMN google_email TEXT;
ALTER TABLE users ADD COLUMN google_sub TEXT;
CREATE UNIQUE INDEX users_google_email_unique ON users(lower(google_email)) WHERE google_email IS NOT NULL;
CREATE UNIQUE INDEX users_google_sub_unique ON users(google_sub) WHERE google_sub IS NOT NULL;
