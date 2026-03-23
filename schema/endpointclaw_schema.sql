-- =============================================================================
-- EndpointClaw — Full Supabase PostgreSQL Schema
-- Supabase project: twgdhuimqspfoimfmyxz
-- Deployed into the existing RoofBids Supabase instance
-- =============================================================================
-- Run this file against the Supabase SQL editor or via psql.
-- Idempotent: uses IF NOT EXISTS / OR REPLACE throughout.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 0. Extensions
-- ---------------------------------------------------------------------------
create extension if not exists "pgcrypto";   -- gen_random_uuid()
create extension if not exists "pg_trgm";    -- trigram similarity for ILIKE speed

-- ---------------------------------------------------------------------------
-- 1. Tables
-- ---------------------------------------------------------------------------

-- ---- company_configs -------------------------------------------------------
create table if not exists company_configs (
    id              text primary key,                       -- e.g. 'corvex'
    company_name    text not null,
    ai_system_prompt text,
    monitored_extensions text[],
    activity_tracking jsonb default '{}',
    integrations    jsonb default '{}',
    branding        jsonb default '{}',
    screenshot_policy jsonb default '{}',
    monitoring_config jsonb default '{}',
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ---- endpoints -------------------------------------------------------------
create table if not exists endpoints (
    id              uuid primary key default gen_random_uuid(),
    device_name     text,
    user_name       text,
    user_email      text,
    company_id      text references company_configs(id) on delete set null,
    api_key         text unique,
    status          text not null default 'active',
    last_heartbeat  timestamptz,
    agent_version   text,
    os_version      text,
    ip_address      text,
    config          jsonb not null default '{}',
    config_overrides jsonb,
    screenshot_policy_override jsonb,
    monitored_paths text[],
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ---- endpoint_commands -----------------------------------------------------
create table if not exists endpoint_commands (
    id              uuid primary key default gen_random_uuid(),
    endpoint_id     uuid not null references endpoints(id) on delete cascade,
    command_type    text not null,
    payload         jsonb default '{}',
    status          text not null default 'pending',
    result          jsonb,
    priority        int not null default 5,
    issued_by       text,
    timeout_seconds int default 300,
    created_at      timestamptz not null default now(),
    completed_at    timestamptz,
    acknowledged_at timestamptz
);

-- ---- endpoint_file_index ---------------------------------------------------
create table if not exists endpoint_file_index (
    id              uuid primary key default gen_random_uuid(),
    endpoint_id     uuid not null references endpoints(id) on delete cascade,
    file_path       text not null,
    filename        text,
    file_type       text,
    file_size       bigint,
    modified_at     timestamptz,
    content_hash    text,
    content_extract text,
    inferred_project text,
    inferred_customer text,
    tags            text[],
    company_id      text references company_configs(id) on delete set null,
    synced_at       timestamptz not null default now(),
    source          text not null default 'local',
    -- Full-text search vector — maintained by trigger below
    fts             tsvector,
    constraint uq_endpoint_file unique (endpoint_id, file_path)
);

-- ---- endpoint_activity -----------------------------------------------------
create table if not exists endpoint_activity (
    id              uuid primary key default gen_random_uuid(),
    endpoint_id     uuid not null references endpoints(id) on delete cascade,
    event_type      text,
    application     text,
    window_title    text,
    file_path       text,
    file_id         uuid,
    duration_ms     int,
    metadata        jsonb,
    company_id      text references company_configs(id) on delete set null,
    created_at      timestamptz not null default now()
);

-- ---- endpoint_heartbeats ---------------------------------------------------
create table if not exists endpoint_heartbeats (
    id              uuid primary key default gen_random_uuid(),
    endpoint_id     uuid not null references endpoints(id) on delete cascade,
    status          text,
    cpu_percent     real,
    ram_used_mb     real,
    disk_used_gb    real,
    sync_state      jsonb,
    agent_version   text,
    created_at      timestamptz not null default now()
);

-- ---- endpoint_screenshots --------------------------------------------------
create table if not exists endpoint_screenshots (
    id              uuid primary key default gen_random_uuid(),
    endpoint_id     uuid not null references endpoints(id) on delete cascade,
    captured_at     timestamptz not null default now(),
    storage_path    text,
    thumbnail_path  text,
    trigger_type    text,
    active_application text,
    window_title    text,
    file_size_bytes int,
    company_id      text references company_configs(id) on delete set null,
    metadata        jsonb
);

-- ---- endpoint_keystrokes ---------------------------------------------------
create table if not exists endpoint_keystrokes (
    id              uuid primary key default gen_random_uuid(),
    endpoint_id     uuid not null references endpoints(id) on delete cascade,
    chunk_start     timestamptz,
    chunk_end       timestamptz,
    text_content    text,
    application     text,
    window_title    text,
    char_count      int,
    company_id      text references company_configs(id) on delete set null,
    created_at      timestamptz not null default now()
);

-- ---- workflow_patterns -----------------------------------------------------
create table if not exists workflow_patterns (
    id                  uuid primary key default gen_random_uuid(),
    company_id          text references company_configs(id) on delete set null,
    pattern_name        text not null,
    description         text,
    steps               jsonb,
    avg_duration_minutes int,
    frequency_per_week  real,
    users               text[],
    automation_score    real,
    detected_at         timestamptz not null default now(),
    status              text not null default 'detected'
);

-- ---- audit_log -------------------------------------------------------------
create table if not exists audit_log (
    id              uuid primary key default gen_random_uuid(),
    actor           text,
    action          text,
    target_type     text,
    target_id       text,
    old_value       jsonb,
    new_value       jsonb,
    created_at      timestamptz not null default now()
);

-- ---- playbook_definitions --------------------------------------------------
create table if not exists playbook_definitions (
    id              uuid primary key default gen_random_uuid(),
    company_id      text references company_configs(id) on delete set null,
    name            text not null,
    description     text,
    version         int not null default 1,
    trigger         jsonb,
    execution_mode  text not null default 'guided',
    steps           jsonb,
    metrics         jsonb,
    status          text not null default 'draft',
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ---- playbook_executions ---------------------------------------------------
create table if not exists playbook_executions (
    id              uuid primary key default gen_random_uuid(),
    playbook_id     uuid not null references playbook_definitions(id) on delete cascade,
    endpoint_id     uuid not null references endpoints(id) on delete cascade,
    status          text not null default 'pending',
    current_step    int not null default 0,
    step_results    jsonb not null default '[]'::jsonb,
    input_data      jsonb,
    started_at      timestamptz,
    completed_at    timestamptz,
    created_at      timestamptz not null default now()
);

-- ---- conversations ---------------------------------------------------------
create table if not exists conversations (
    id              uuid primary key default gen_random_uuid(),
    endpoint_id     uuid references endpoints(id) on delete cascade,
    messages        jsonb not null default '[]'::jsonb,
    summary         text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ---- dashboard_users -------------------------------------------------------
create table if not exists dashboard_users (
    id              uuid primary key default gen_random_uuid(),
    email           text unique not null,
    name            text,
    role            text not null default 'viewer',
    company_id      text references company_configs(id) on delete set null,
    created_at      timestamptz not null default now()
);

-- ---- playbooks (view) ------------------------------------------------------
-- The playbooks.py route queries a table called "playbooks".
-- Create a convenience view pointing at playbook_definitions.
create or replace view playbooks as
    select * from playbook_definitions;


-- ---------------------------------------------------------------------------
-- 2. Indexes
-- ---------------------------------------------------------------------------

-- endpoints
create index if not exists idx_endpoints_company_id       on endpoints (company_id);
create index if not exists idx_endpoints_status            on endpoints (status);
create index if not exists idx_endpoints_last_heartbeat    on endpoints (last_heartbeat desc);
create index if not exists idx_endpoints_company_status    on endpoints (company_id, status);

-- endpoint_commands
create index if not exists idx_ep_commands_endpoint_id     on endpoint_commands (endpoint_id);
create index if not exists idx_ep_commands_status           on endpoint_commands (status);
create index if not exists idx_ep_commands_ep_status        on endpoint_commands (endpoint_id, status);
create index if not exists idx_ep_commands_created_at       on endpoint_commands (created_at desc);

-- endpoint_file_index
create index if not exists idx_ep_files_endpoint_id        on endpoint_file_index (endpoint_id);
create index if not exists idx_ep_files_company_id         on endpoint_file_index (company_id);
create index if not exists idx_ep_files_file_type          on endpoint_file_index (file_type);
create index if not exists idx_ep_files_modified_at        on endpoint_file_index (modified_at desc);
create index if not exists idx_ep_files_inferred_project   on endpoint_file_index (inferred_project);
create index if not exists idx_ep_files_fts                on endpoint_file_index using gin (fts);

-- endpoint_activity
create index if not exists idx_ep_activity_endpoint_id     on endpoint_activity (endpoint_id);
create index if not exists idx_ep_activity_company_id      on endpoint_activity (company_id);
create index if not exists idx_ep_activity_event_type      on endpoint_activity (event_type);
create index if not exists idx_ep_activity_created_at      on endpoint_activity (created_at desc);
create index if not exists idx_ep_activity_ep_created      on endpoint_activity (endpoint_id, created_at desc);
create index if not exists idx_ep_activity_ep_event_created on endpoint_activity (endpoint_id, event_type, created_at desc);

-- endpoint_heartbeats
create index if not exists idx_ep_heartbeats_endpoint_id   on endpoint_heartbeats (endpoint_id);
create index if not exists idx_ep_heartbeats_created_at    on endpoint_heartbeats (created_at desc);
create index if not exists idx_ep_heartbeats_ep_created    on endpoint_heartbeats (endpoint_id, created_at desc);

-- endpoint_screenshots
create index if not exists idx_ep_screenshots_endpoint_id  on endpoint_screenshots (endpoint_id);
create index if not exists idx_ep_screenshots_company_id   on endpoint_screenshots (company_id);
create index if not exists idx_ep_screenshots_captured_at  on endpoint_screenshots (captured_at desc);
create index if not exists idx_ep_screenshots_ep_captured  on endpoint_screenshots (endpoint_id, captured_at desc);

-- endpoint_keystrokes
create index if not exists idx_ep_keystrokes_endpoint_id   on endpoint_keystrokes (endpoint_id);
create index if not exists idx_ep_keystrokes_company_id    on endpoint_keystrokes (company_id);
create index if not exists idx_ep_keystrokes_chunk_start   on endpoint_keystrokes (chunk_start desc);
create index if not exists idx_ep_keystrokes_ep_chunk      on endpoint_keystrokes (endpoint_id, chunk_start desc);

-- workflow_patterns
create index if not exists idx_wf_patterns_company_id      on workflow_patterns (company_id);
create index if not exists idx_wf_patterns_status          on workflow_patterns (status);

-- audit_log
create index if not exists idx_audit_log_created_at        on audit_log (created_at desc);
create index if not exists idx_audit_log_actor             on audit_log (actor);
create index if not exists idx_audit_log_target            on audit_log (target_type, target_id);

-- playbook_definitions
create index if not exists idx_pb_defs_company_id          on playbook_definitions (company_id);
create index if not exists idx_pb_defs_status              on playbook_definitions (status);

-- playbook_executions
create index if not exists idx_pb_exec_playbook_id         on playbook_executions (playbook_id);
create index if not exists idx_pb_exec_endpoint_id         on playbook_executions (endpoint_id);
create index if not exists idx_pb_exec_status              on playbook_executions (status);
create index if not exists idx_pb_exec_created_at          on playbook_executions (created_at desc);

-- conversations
create index if not exists idx_conversations_endpoint_id   on conversations (endpoint_id);

-- dashboard_users
create index if not exists idx_dashboard_users_company_id  on dashboard_users (company_id);


-- ---------------------------------------------------------------------------
-- 3. RPC Functions
-- ---------------------------------------------------------------------------

-- ---- search_endpoint_files -------------------------------------------------
-- Full-text search with ts_rank; falls back to ILIKE for short / non-FTS queries.
-- Accepts optional p_file_type filter used by the files route.
create or replace function search_endpoint_files(
    p_company_id  text,
    p_query       text,
    p_endpoint_id uuid    default null,
    p_file_type   text    default null,
    p_limit       int     default 20
)
returns setof endpoint_file_index
language plpgsql
security definer
set search_path = public
as $$
declare
    v_tsquery tsquery;
begin
    -- Attempt to build a tsquery; short or odd input will fail gracefully.
    begin
        v_tsquery := websearch_to_tsquery('english', p_query);
    exception when others then
        v_tsquery := null;
    end;

    -- If we got a usable tsquery, use full-text ranking
    if v_tsquery is not null and length(p_query) > 2 then
        return query
            select f.*
            from   endpoint_file_index f
            where  f.company_id = p_company_id
              and  (p_endpoint_id is null or f.endpoint_id = p_endpoint_id)
              and  (p_file_type  is null or f.file_type   = p_file_type)
              and  f.fts @@ v_tsquery
            order  by ts_rank(f.fts, v_tsquery) desc,
                      f.modified_at desc nulls last
            limit  p_limit;
    else
        -- Fallback: ILIKE on filename, file_path, inferred_project, content_extract
        return query
            select f.*
            from   endpoint_file_index f
            where  f.company_id = p_company_id
              and  (p_endpoint_id is null or f.endpoint_id = p_endpoint_id)
              and  (p_file_type  is null or f.file_type   = p_file_type)
              and  (
                       f.filename         ilike '%' || p_query || '%'
                    or f.file_path        ilike '%' || p_query || '%'
                    or f.inferred_project ilike '%' || p_query || '%'
                    or f.content_extract  ilike '%' || p_query || '%'
                   )
            order  by f.modified_at desc nulls last
            limit  p_limit;
    end if;
end;
$$;

-- ---- get_effective_config --------------------------------------------------
-- Merges company_configs base config with endpoint-level config_overrides.
-- Returns a single JSONB object.
create or replace function get_effective_config(
    p_endpoint_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_company_id text;
    v_base       jsonb := '{}';
    v_overrides  jsonb := '{}';
begin
    select e.company_id, coalesce(e.config_overrides, '{}'::jsonb)
      into v_company_id, v_overrides
      from endpoints e
     where e.id = p_endpoint_id;

    if v_company_id is not null then
        select coalesce(c.monitoring_config, '{}'::jsonb)
          into v_base
          from company_configs c
         where c.id = v_company_id;
    end if;

    -- Endpoint overrides take precedence (shallow merge via || )
    return v_base || v_overrides;
end;
$$;

-- ---- get_effective_screenshot_policy ---------------------------------------
-- Merges company screenshot_policy with endpoint screenshot_policy_override.
create or replace function get_effective_screenshot_policy(
    p_endpoint_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_company_id text;
    v_base       jsonb := '{}';
    v_override   jsonb := '{}';
begin
    select e.company_id, coalesce(e.screenshot_policy_override, '{}'::jsonb)
      into v_company_id, v_override
      from endpoints e
     where e.id = p_endpoint_id;

    if v_company_id is not null then
        select coalesce(c.screenshot_policy, '{}'::jsonb)
          into v_base
          from company_configs c
         where c.id = v_company_id;
    end if;

    return v_base || v_override;
end;
$$;


-- ---------------------------------------------------------------------------
-- 4. Row Level Security (RLS)
-- ---------------------------------------------------------------------------

-- Enable RLS on every table
alter table company_configs       enable row level security;
alter table endpoints             enable row level security;
alter table endpoint_commands     enable row level security;
alter table endpoint_file_index   enable row level security;
alter table endpoint_activity     enable row level security;
alter table endpoint_heartbeats   enable row level security;
alter table endpoint_screenshots  enable row level security;
alter table endpoint_keystrokes   enable row level security;
alter table workflow_patterns     enable row level security;
alter table audit_log             enable row level security;
alter table playbook_definitions  enable row level security;
alter table playbook_executions   enable row level security;
alter table conversations         enable row level security;
alter table dashboard_users       enable row level security;

-- ---- Service-role bypass ---------------------------------------------------
-- The dashboard API connects with the service_role key, which bypasses RLS
-- by default in Supabase. These policies cover non-service-role access.

-- ---- company_configs -------------------------------------------------------
create policy "service_role_full_access_company_configs"
    on company_configs for all
    using (auth.role() = 'service_role');

create policy "authenticated_read_own_company_config"
    on company_configs for select
    using (
        auth.role() = 'authenticated'
        and id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- endpoints -------------------------------------------------------------
create policy "service_role_full_access_endpoints"
    on endpoints for all
    using (auth.role() = 'service_role');

-- Endpoints authenticate with their api_key embedded in the JWT sub claim
create policy "endpoint_read_own"
    on endpoints for select
    using (
        auth.role() = 'authenticated'
        and api_key = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
            ''
        )
    );

create policy "endpoint_update_own"
    on endpoints for update
    using (
        auth.role() = 'authenticated'
        and api_key = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
            ''
        )
    );

-- Company isolation for dashboard users
create policy "company_isolation_endpoints"
    on endpoints for select
    using (
        auth.role() = 'authenticated'
        and company_id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- endpoint_commands -----------------------------------------------------
create policy "service_role_full_access_ep_commands"
    on endpoint_commands for all
    using (auth.role() = 'service_role');

create policy "endpoint_read_own_commands"
    on endpoint_commands for select
    using (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

create policy "endpoint_update_own_commands"
    on endpoint_commands for update
    using (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

-- ---- endpoint_file_index ---------------------------------------------------
create policy "service_role_full_access_ep_files"
    on endpoint_file_index for all
    using (auth.role() = 'service_role');

create policy "endpoint_manage_own_files"
    on endpoint_file_index for all
    using (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

create policy "company_isolation_ep_files"
    on endpoint_file_index for select
    using (
        auth.role() = 'authenticated'
        and company_id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- endpoint_activity -----------------------------------------------------
create policy "service_role_full_access_ep_activity"
    on endpoint_activity for all
    using (auth.role() = 'service_role');

create policy "endpoint_insert_own_activity"
    on endpoint_activity for insert
    with check (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

create policy "company_isolation_ep_activity"
    on endpoint_activity for select
    using (
        auth.role() = 'authenticated'
        and company_id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- endpoint_heartbeats ---------------------------------------------------
create policy "service_role_full_access_ep_heartbeats"
    on endpoint_heartbeats for all
    using (auth.role() = 'service_role');

create policy "endpoint_insert_own_heartbeats"
    on endpoint_heartbeats for insert
    with check (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

create policy "endpoint_read_own_heartbeats"
    on endpoint_heartbeats for select
    using (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

-- ---- endpoint_screenshots --------------------------------------------------
create policy "service_role_full_access_ep_screenshots"
    on endpoint_screenshots for all
    using (auth.role() = 'service_role');

create policy "endpoint_insert_own_screenshots"
    on endpoint_screenshots for insert
    with check (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

create policy "company_isolation_ep_screenshots"
    on endpoint_screenshots for select
    using (
        auth.role() = 'authenticated'
        and company_id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- endpoint_keystrokes ---------------------------------------------------
create policy "service_role_full_access_ep_keystrokes"
    on endpoint_keystrokes for all
    using (auth.role() = 'service_role');

create policy "endpoint_insert_own_keystrokes"
    on endpoint_keystrokes for insert
    with check (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

create policy "company_isolation_ep_keystrokes"
    on endpoint_keystrokes for select
    using (
        auth.role() = 'authenticated'
        and company_id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- workflow_patterns -----------------------------------------------------
create policy "service_role_full_access_wf_patterns"
    on workflow_patterns for all
    using (auth.role() = 'service_role');

create policy "company_isolation_wf_patterns"
    on workflow_patterns for select
    using (
        auth.role() = 'authenticated'
        and company_id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- audit_log -------------------------------------------------------------
create policy "service_role_full_access_audit_log"
    on audit_log for all
    using (auth.role() = 'service_role');

create policy "authenticated_read_audit_log"
    on audit_log for select
    using (auth.role() = 'authenticated');

-- ---- playbook_definitions --------------------------------------------------
create policy "service_role_full_access_pb_defs"
    on playbook_definitions for all
    using (auth.role() = 'service_role');

create policy "company_isolation_pb_defs"
    on playbook_definitions for select
    using (
        auth.role() = 'authenticated'
        and company_id = coalesce(
            current_setting('request.jwt.claims', true)::jsonb ->> 'company_id',
            ''
        )
    );

-- ---- playbook_executions ---------------------------------------------------
create policy "service_role_full_access_pb_exec"
    on playbook_executions for all
    using (auth.role() = 'service_role');

create policy "endpoint_read_own_pb_exec"
    on playbook_executions for select
    using (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

-- ---- conversations ---------------------------------------------------------
create policy "service_role_full_access_conversations"
    on conversations for all
    using (auth.role() = 'service_role');

create policy "endpoint_manage_own_conversations"
    on conversations for all
    using (
        auth.role() = 'authenticated'
        and endpoint_id in (
            select id from endpoints
            where api_key = coalesce(
                current_setting('request.jwt.claims', true)::jsonb ->> 'sub',
                ''
            )
        )
    );

-- ---- dashboard_users -------------------------------------------------------
create policy "service_role_full_access_dashboard_users"
    on dashboard_users for all
    using (auth.role() = 'service_role');

create policy "dashboard_user_read_own"
    on dashboard_users for select
    using (
        auth.role() = 'authenticated'
        and id = auth.uid()
    );


-- ---------------------------------------------------------------------------
-- 5. updated_at trigger
-- ---------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- Apply to tables with an updated_at column
do $$
declare
    t text;
begin
    for t in select unnest(array[
        'company_configs','endpoints','playbook_definitions','conversations'
    ]) loop
        begin
            execute format(
                'create trigger trg_set_updated_at
                 before update on %I
                 for each row execute function set_updated_at()',
                t
            );
        exception when duplicate_object then
            null;  -- trigger already exists, skip
        end;
    end loop;
end;
$$;


-- ---------------------------------------------------------------------------
-- 5b. FTS trigger for endpoint_file_index
-- ---------------------------------------------------------------------------
create or replace function update_file_index_fts()
returns trigger
language plpgsql
as $$
begin
    new.fts :=
        setweight(to_tsvector('english', coalesce(new.filename, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(new.inferred_project, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(new.inferred_customer, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(new.content_extract, '')), 'C') ||
        setweight(to_tsvector('english', coalesce(array_to_string(new.tags, ' '), '')), 'B');
    return new;
end;
$$;

do $$
begin
    begin
        create trigger trg_update_file_index_fts
        before insert or update on endpoint_file_index
        for each row execute function update_file_index_fts();
    exception when duplicate_object then
        null;
    end;
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. Storage — screenshot bucket
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('endpointclaw-screenshots', 'endpointclaw-screenshots', false)
on conflict (id) do nothing;

-- Allow service role to manage objects
create policy "service_role_storage_all"
    on storage.objects for all
    using (
        bucket_id = 'endpointclaw-screenshots'
        and auth.role() = 'service_role'
    );

-- Allow endpoints to upload their own screenshots
create policy "endpoint_upload_screenshots"
    on storage.objects for insert
    with check (
        bucket_id = 'endpointclaw-screenshots'
        and auth.role() = 'authenticated'
    );

-- Allow authenticated users to read screenshots
create policy "authenticated_read_screenshots"
    on storage.objects for select
    using (
        bucket_id = 'endpointclaw-screenshots'
        and auth.role() = 'authenticated'
    );


-- ---------------------------------------------------------------------------
-- 7. Seed Data — Corvex company config
-- ---------------------------------------------------------------------------
insert into company_configs (
    id,
    company_name,
    ai_system_prompt,
    monitored_extensions,
    activity_tracking,
    integrations,
    branding,
    screenshot_policy,
    monitoring_config
) values (
    'corvex',
    'Corvex Management',
    'You are an AI assistant for Corvex Management, a commercial roofing company. '
    'You help managers monitor field and office staff productivity, track project files, '
    'and provide insights into team workflows. Always be professional and concise. '
    'When discussing employee activity, focus on work patterns rather than surveillance. '
    'Reference specific applications, documents, and projects when available.',
    array[
        '.pdf', '.xlsx', '.xls', '.docx', '.doc', '.pptx', '.ppt',
        '.csv', '.txt', '.dwg', '.dxf', '.rvt',
        '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp',
        '.eml', '.msg',
        '.zip', '.rar'
    ],
    '{
        "track_applications": true,
        "track_window_titles": true,
        "track_file_access": true,
        "idle_timeout_seconds": 300,
        "heartbeat_interval_seconds": 60,
        "activity_batch_interval_seconds": 30,
        "productive_applications": [
            "EXCEL.EXE", "chrome.exe", "msedge.exe", "OUTLOOK.EXE",
            "WINWORD.EXE", "ACROBAT.EXE", "POWERPNT.EXE",
            "AutoCAD.exe", "Revit.exe", "navisworks.exe",
            "Teams.exe", "Zoom.exe", "slack.exe"
        ],
        "ignore_applications": [
            "LockApp.exe", "SearchUI.exe", "ShellExperienceHost.exe",
            "StartMenuExperienceHost.exe", "SystemSettings.exe"
        ]
    }'::jsonb,
    '{
        "procore": {"enabled": false, "api_key": null},
        "quickbooks": {"enabled": false},
        "slack": {"enabled": false, "webhook_url": null}
    }'::jsonb,
    '{
        "company_name": "Corvex Management",
        "logo_url": null,
        "primary_color": "#1a365d",
        "secondary_color": "#e53e3e",
        "dashboard_title": "EndpointClaw — Corvex Fleet Manager"
    }'::jsonb,
    '{
        "enabled": true,
        "interval_seconds": 300,
        "capture_on_app_switch": true,
        "capture_on_idle_resume": true,
        "blur_sensitive": false,
        "retention_days": 30,
        "max_storage_mb_per_endpoint": 500,
        "excluded_applications": ["LockApp.exe"],
        "working_hours_only": true,
        "working_hours": {"start": "06:00", "end": "20:00", "timezone": "America/Chicago"}
    }'::jsonb,
    '{
        "file_index": {
            "enabled": true,
            "scan_interval_minutes": 60,
            "scan_paths": ["C:\\Users\\*\\Documents", "C:\\Users\\*\\Desktop", "C:\\Users\\*\\Downloads"],
            "max_file_size_mb": 100,
            "extract_content": true,
            "content_extract_max_chars": 2000
        },
        "keystrokes": {
            "enabled": true,
            "chunk_interval_seconds": 60,
            "exclude_passwords": true,
            "exclude_applications": ["KeePass.exe", "1Password.exe"]
        },
        "network": {
            "track_bandwidth": false
        }
    }'::jsonb
) on conflict (id) do update set
    company_name         = excluded.company_name,
    ai_system_prompt     = excluded.ai_system_prompt,
    monitored_extensions = excluded.monitored_extensions,
    activity_tracking    = excluded.activity_tracking,
    integrations         = excluded.integrations,
    branding             = excluded.branding,
    screenshot_policy    = excluded.screenshot_policy,
    monitoring_config    = excluded.monitoring_config,
    updated_at           = now();


-- ---------------------------------------------------------------------------
-- Done.
-- ---------------------------------------------------------------------------
