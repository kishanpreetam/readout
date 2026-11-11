with source as (
    select * from {{ source('raw', 'ct_studies') }}
),

typed as (
    select
        nct_id,
        brief_title,
        official_title,
        acronym,
        upper(overall_status) as overall_status,
        why_stopped,
        {{ ct_date('start_date') }} as start_date,
        {{ ct_date_precision('start_date') }} as start_date_precision,
        upper(coalesce(start_date_type, 'UNKNOWN')) as start_date_type,
        {{ ct_date('primary_completion_date') }} as primary_completion_date,
        {{ ct_date_precision('primary_completion_date') }} as primary_completion_date_precision,
        upper(coalesce(primary_completion_date_type, 'UNKNOWN')) as primary_completion_date_type,
        {{ ct_date('completion_date') }} as completion_date,
        {{ ct_date_precision('completion_date') }} as completion_date_precision,
        upper(coalesce(completion_date_type, 'UNKNOWN')) as completion_date_type,
        {{ ct_date('first_post_date') }} as first_posted_date,
        {{ ct_date('last_update_date') }} as last_update_date,
        trim(lead_sponsor_name) as lead_sponsor_name,
        lead_sponsor_class,
        study_type,
        phases,
        case
            when phases is null or len(phases) = 0 then 'UNKNOWN'
            when list_contains(phases, 'PHASE2') and list_contains(phases, 'PHASE3') then 'PHASE2_PHASE3'
            when list_contains(phases, 'PHASE1') and list_contains(phases, 'PHASE2') then 'PHASE1_PHASE2'
            when list_contains(phases, 'PHASE4') then 'PHASE4'
            when list_contains(phases, 'PHASE3') then 'PHASE3'
            when list_contains(phases, 'PHASE2') then 'PHASE2'
            when list_contains(phases, 'PHASE1') then 'PHASE1'
            when list_contains(phases, 'EARLY_PHASE1') then 'EARLY_PHASE1'
            when list_contains(phases, 'NA') then 'NA'
            else 'UNKNOWN'
        end as phase,
        enrollment_count,
        upper(enrollment_type) as enrollment_type,
        allocation,
        primary_purpose,
        keywords,
        brief_summary,
        coalesce(has_results, false) as has_results
    from source
)

select
    *,
    case phase
        when 'EARLY_PHASE1' then 'Early Phase 1'
        when 'PHASE1' then 'Phase 1'
        when 'PHASE1_PHASE2' then 'Phase 1/2'
        when 'PHASE2' then 'Phase 2'
        when 'PHASE2_PHASE3' then 'Phase 2/3'
        when 'PHASE3' then 'Phase 3'
        when 'PHASE4' then 'Phase 4'
        when 'NA' then 'Not applicable'
        else 'Unknown'
    end as phase_label,
    case phase
        when 'EARLY_PHASE1' then 0.5
        when 'PHASE1' then 1.0
        when 'PHASE1_PHASE2' then 1.5
        when 'PHASE2' then 2.0
        when 'PHASE2_PHASE3' then 2.5
        when 'PHASE3' then 3.0
        when 'PHASE4' then 4.0
    end as phase_number,
    overall_status in ('RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING', 'ENROLLING_BY_INVITATION')
        as is_active
from typed
