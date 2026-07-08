-- FraudLens Track B Supabase Auth setup.
-- Apply in the shared Supabase project after enabling RSA JWT signing and disabling open signup.
-- The hook stamps app-owned tenant/RBAC claims into access tokens from public.users.

create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
  claims jsonb;
  user_agency_id text;
  user_role text;
begin
  select users.agency_id::text, users.role::text
    into user_agency_id, user_role
    from public.users
   where users.id = (event->>'user_id')::uuid;

  if user_agency_id is null or user_role is null then
    return event;
  end if;

  claims := coalesce(event->'claims', '{}'::jsonb);
  claims := jsonb_set(claims, '{agency_id}', to_jsonb(user_agency_id), true);
  claims := jsonb_set(claims, '{user_role}', to_jsonb(user_role), true);

  return jsonb_set(event, '{claims}', claims, true);
end;
$$;

revoke all on function public.custom_access_token_hook(jsonb) from public;
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
grant usage on schema public to supabase_auth_admin;
grant select on table public.users to supabase_auth_admin;

alter table public.users enable row level security;

drop policy if exists users_read_same_agency on public.users;
create policy users_read_same_agency
  on public.users
  for select
  to authenticated
  using (agency_id::text = auth.jwt()->>'agency_id');
