-- Development seed: tenant + user + membership for the AUTH_DISABLED principal.
-- Idempotent — safe to run on every start. IDs match DEV_TENANT_ID / DEV_USER_ID
-- in backend/.env.example.

INSERT INTO tenants (id, name)
VALUES ('00000000-0000-0000-0000-000000000002', 'Dev Tenant')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, google_sub, email)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'dev-local',
    'dev@example.local'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO memberships (user_id, tenant_id, roles)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000002',
    ARRAY['member', 'admin']
)
ON CONFLICT (user_id) DO NOTHING;
