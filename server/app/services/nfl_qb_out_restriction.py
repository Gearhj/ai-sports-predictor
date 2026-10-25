"""Source-only consumer qualifier for the approved NFL expected-QB-Out gate.
Not wired to a decision, importer, or live setting. No I/O or provider access.
The caller must obtain all trusted context from consumer-verified admission,
event and player registries, never from the producer's policy-admitted flag.
"""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone

def _clock(value):
    result = datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)

def qualify(raw_record: bytes, *, admitted_sha256: str | None, context: dict, decision_at) -> dict:
    result = {"state":"UNKNOWN","qualified_out":False,"reason":"UNADMITTED_OBSERVATION",
              "probability_changed":False,"funding_executed":False}
    if not admitted_sha256 or not re.fullmatch(r"[0-9a-f]{64}",admitted_sha256):
        return result
    if hashlib.sha256(raw_record).hexdigest()!=admitted_sha256:
        return {**result,"reason":"ADMITTED_BYTES_MISMATCH"}
    try:
        record=json.loads(raw_record)
        now=_clock(decision_at)
        if _clock(context["admitted_at_utc"])>now:
            raise ValueError("ADMISSION_AFTER_DECISION")
        if record["schema_version"]!="AISP_PERSONNEL_OBSERVATION_V1" or record["sport"]!="NFL":
            raise ValueError("WRONG_CONTRACT_OR_SPORT")
        event=record["event"]; team=record["team"]; person=record["person"]
        if event.get("sport")!="NFL":
            raise ValueError("CROSS_SPORT_EVENT")
        for key in ("canonical_id","native_id","native_service","event_revision"):
            if not context["event"].get(key) or event.get(key)!=context["event"][key]:
                raise ValueError("EVENT_IDENTITY_MISMATCH:"+key)
        if _clock(event["original_start_utc"])!=_clock(context["event"]["original_start_utc"]):
            raise ValueError("ORIGINAL_START_MISMATCH")
        if now>=_clock(event["original_start_utc"]):
            raise ValueError("NOT_PREGAME")
        for key in ("canonical_id","code","side"):
            if not context["team"].get(key) or team.get(key)!=context["team"][key]:
                raise ValueError("TEAM_IDENTITY_MISMATCH:"+key)
        if team["side"] not in ("home","away"):
            raise ValueError("INVALID_TEAM_SIDE")
        if person["identity_state"]!="RESOLVED" or not person.get("identity_evidence_ref"):
            raise ValueError("PLAYER_IDENTITY_UNRESOLVED")
        for key in ("native_id","canonical_id"):
            if not context["expected_person"].get(key) or person.get(key)!=context["expected_person"][key]:
                raise ValueError("EXPECTED_QB_IDENTITY_MISMATCH:"+key)
        role=record["role"]; evidence=record["evidence"]; role_ref=role.get("evidence_ref") or {}
        if role.get("kind")!="EXPECTED_QB_STARTER" or role.get("confirmation")!="CONFIRMED_EXPECTED_STARTER":
            raise ValueError("EXPECTED_STARTER_NOT_CONFIRMED")
        for ref in (evidence,role_ref):
            if not re.fullmatch(r"[0-9a-f]{64}",str(ref.get("sha256") or "")):
                raise ValueError("SOURCE_PROVENANCE_MISSING")
            observed=_clock(ref["observed_at_utc"])
            if observed>now:
                raise ValueError("OBSERVED_AFTER_DECISION")
            for key in ("report_asof_utc","published_at_utc"):
                if ref.get(key) and _clock(ref[key])>observed:
                    raise ValueError("FUTURE_SOURCE_CLOCK")
        if evidence.get("type") not in ("OFFICIAL_FINAL_GAME_STATUS","OFFICIAL_INACTIVES"):
            raise ValueError("OFFICIAL_CURRENT_STATUS_REQUIRED")
        if any(k in record for k in ("probability_adjustment","stake_change","funding_action")):
            raise ValueError("OBSERVATION_CONTAINS_ACTION")
        identity={"event":event,"team":team,"person":person,
                  "source_sha256":evidence["sha256"],"source_locator":evidence["locator"],
                  "observed_at_utc":evidence["observed_at_utc"]}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        if record.get("observation_id")!=digest:
            raise ValueError("OBSERVATION_IDENTITY_MISMATCH")
        status=record["status"]["normalized"]
        if status in ("OUT","INACTIVE"):
            return {**result,"state":"QUALIFIED_OUT","qualified_out":True,"reason":"EXACT_EXPECTED_QB_OFFICIAL_OUT",
                    "observation_id":digest,"admitted_sha256":admitted_sha256,"status":status,
                    "decision_at_utc":now.isoformat(),"producer_admission_flag_used":False}
        if status not in ("DOUBTFUL","QUESTIONABLE","PROBABLE","AVAILABLE","ACTIVE_ROSTER",
                          "UNKNOWN","NO_DESIGNATION_REPORTED"):
            raise ValueError("UNSUPPORTED_STATUS")
        return {**result,"state":"UNKNOWN" if status=="UNKNOWN" else "NOT_OUT",
                "reason":"STATUS_IS_NOT_OUT","status":status,"producer_admission_flag_used":False}
    except (ValueError,TypeError,KeyError,AttributeError,json.JSONDecodeError) as error:
        return {**result,"reason":str(error)}

# Consumer-owned immutable admission registry and prospective NFL funding adapter.
# No acquisition, publisher writes, model calculation or direct database mutation.
from pathlib import Path
from uuid import UUID

ADMISSION_SCHEMA = "AISP_NFL_QB_OUT_CONSUMER_ADMISSION_V1"
CONTRACT_SHA256 = "db775a47a5220c0d51354d549c046c97f3585caa543d87c0630bd1cb438c4fed"
OWNER_DIRECTIVE_SHA256 = "0feb83661de0824bdccf02e37a11a724cc3cad17d6602933877265a110f11443"
QB_RULE = "AISP_OWNER_NFL_EXPECTED_QB_OUT_WEEK7_2026_RECORD_ONLY_V1"
QB_REASON = "OWNER_EXPECTED_QB_OUT_RECORD_ONLY"

def _sha(value):
    return bool(isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value))

def runtime_state(settings, *, now=None):
    observed = _clock(now or datetime.now(timezone.utc))
    value = getattr(settings,"owner_nfl_qb_out_record_only_effective_from",None)
    try:
        activation = _clock(value) if value else None
        invalid = False
    except (ValueError,TypeError):
        activation = None
        invalid = True
    return {"implemented":True, "active_at_observation":bool(activation and observed>=activation),
            "effective_from_utc":activation.isoformat() if activation else None,
            "configuration_valid":not invalid, "rule":QB_RULE, "season":2026,"week":7,
            "admissions_root_configured":bool(getattr(settings,"owner_nfl_qb_out_admissions_root",None)),
            "observation_root_configured":bool(getattr(settings,"owner_nfl_qb_out_observation_root",None)),
            "missing_evidence":"UNKNOWN_NOT_CLEARED_NO_ADDITIONAL_VETO",
            "model_probability_changed":False}

def _within(path,root):
    p=Path(path).resolve();r=Path(root).resolve()
    if not p.is_relative_to(r):
        raise ValueError("REFERENCE_OUTSIDE_CONFIGURED_ROOT")
    return p

def admission_bytes(*, observation_relative_path, observation_sha256, context,
                    review_evidence_ref, source_evidence_sha256,
                    observation_packet_sha256=None, observation_line_index=None):
    """Construct an INTERNAL consumer receipt only after actual MAIN review.
    This is not producer self-admission or a replacement for source verification.
    No file write or runtime invocation is performed by this factory.
    """
    context={**context,"admitted_at_utc":datetime.now(timezone.utc).isoformat()}
    doc={"schema":ADMISSION_SCHEMA,"producer_contract_sha256":CONTRACT_SHA256,
         "owner_directive_sha256":OWNER_DIRECTIVE_SHA256,
         "observation_relative_path":observation_relative_path,
         "observation_sha256":observation_sha256, "context":context,
         "observation_packet_sha256":observation_packet_sha256,
         "observation_line_index":observation_line_index,
         "review_evidence_ref":review_evidence_ref,
         "source_evidence_sha256":source_evidence_sha256}
    return (json.dumps(doc,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n").encode("utf-8")

def load_admissions(*, game_id, native_event_id, starts_at, teams, moment,
                    admissions_root, observation_root):
    """Read this game's exact admitted immutable receipts; never scan all runtime.
    'teams' and event context MUST be current consumer registry facts. MAIN's
    independent identity/role/status/provenance review supplies expected_person.
    """
    cases=[];errors=[]
    if not admissions_root or not observation_root:
        return {"state":"UNKNOWN","reason":"CONSUMER_ADMISSION_ROOT_NOT_CONFIGURED","cases":[],"errors":[]}
    if not re.fullmatch(r"2026_07_[A-Z0-9]+_[A-Z0-9]+",str(native_event_id)):
        return {"state":"NOT_APPLICABLE","reason":"OUTSIDE_AUTHORIZED_2026_WEEK7","cases":[],"errors":[]}
    try:
        game_key=str(UUID(str(game_id)))
        configured_roots=[Path(admissions_root),Path(observation_root)]
        if any(not p.is_absolute() or str(p).startswith("\\\\") for p in configured_roots):
            raise ValueError("LOCAL_ABSOLUTE_CONFIGURED_ROOTS_REQUIRED")
        root=configured_roots[0].resolve()
        base=_within(root/game_key/"sha256",root)
        if not base.exists():
            return {"state":"UNKNOWN","reason":"NO_CONSUMER_ADMITTED_OBSERVATION","cases":[],"errors":[]}
        now=_clock(moment)
        for directory in sorted(base.iterdir(),key=lambda p:p.name):
            if not directory.is_dir() or not _sha(directory.name):
                continue
            try:
                path=_within(directory/"admission.json",root)
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=directory.name:
                    raise ValueError("ADMISSION_ADDRESS_MISMATCH")
                doc=json.loads(raw)
                if (doc.get("schema")!=ADMISSION_SCHEMA or
                    doc.get("producer_contract_sha256")!=CONTRACT_SHA256 or
                    doc.get("owner_directive_sha256")!=OWNER_DIRECTIVE_SHA256):
                    raise ValueError("CONSUMER_ADMISSION_AUTHORITY_MISMATCH")
                review=doc.get("review_evidence_ref") or {}
                if not _sha(review.get("sha256")) or not review.get("path"):
                    raise ValueError("CONSUMER_REVIEW_EVIDENCE_MISSING")
                ctx=doc["context"]
                if _clock(ctx["admitted_at_utc"])>now:
                    continue
                event=ctx["event"]
                if str(event.get("canonical_id"))!=game_key or event.get("native_id")!=native_event_id:
                    raise ValueError("CURRENT_EVENT_BINDING_MISMATCH")
                if _clock(event["original_start_utc"])!=_clock(starts_at):
                    raise ValueError("CURRENT_START_BINDING_MISMATCH")
                side=ctx["team"].get("side")
                if side not in teams or any(ctx["team"].get(k)!=teams[side].get(k) for k in ("canonical_id","code","side")):
                    raise ValueError("CURRENT_TEAM_BINDING_MISMATCH")
                rel=Path(doc["observation_relative_path"])
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError("ORIGINAL_OBSERVATION_RELATIVE_PATH_REQUIRED")
                observation_path=_within(Path(observation_root)/rel,observation_root)
                packet=observation_path.read_bytes()
                packet_sha=doc.get("observation_packet_sha256") or doc.get("observation_sha256")
                if not _sha(packet_sha) or hashlib.sha256(packet).hexdigest()!=packet_sha:
                    raise ValueError("ORIGINAL_OBSERVATION_PACKET_BYTES_MISMATCH")
                line_index=doc.get("observation_line_index")
                if line_index is None:
                    original=packet
                elif isinstance(line_index,int) and not isinstance(line_index,bool) and line_index>=0:
                    original=packet.splitlines(keepends=True)[line_index]
                else:
                    raise ValueError("EXACT_ORIGINAL_JSONL_LINE_INDEX_REQUIRED")
                obs=json.loads(original)
                refs=doc.get("source_evidence_sha256") or {}
                identity_ref=obs["person"].get("identity_evidence_ref")
                if not isinstance(identity_ref,dict) or not _sha(identity_ref.get("sha256")):
                    raise ValueError("EXACT_PLAYER_CROSSWALK_SOURCE_REF_REQUIRED")
                actual={"status":obs["evidence"]["sha256"],
                        "role":obs["role"]["evidence_ref"]["sha256"],
                        "identity":identity_ref["sha256"]}
                if refs!=actual or not all(_sha(v) for v in actual.values()):
                    raise ValueError("REVIEWED_SOURCE_ROLE_CROSSWALK_REFS_MISMATCH")
                admitted=_clock(ctx["admitted_at_utc"])
                for ref in (obs["evidence"],obs["role"]["evidence_ref"],identity_ref):
                    if not ref.get("observed_at_utc") or _clock(ref["observed_at_utc"])>admitted:
                        raise ValueError("SOURCE_EVIDENCE_POSTDATES_CONSUMER_ADMISSION")
                assessed=qualify(original,admitted_sha256=doc.get("observation_sha256"),
                                  context=ctx,decision_at=moment)
                cases.append({**assessed,"admission_sha256":directory.name,
                              "review_evidence_ref":review,
                              "admitted_at_utc":ctx["admitted_at_utc"],"team_side":side,
                              "person":ctx["expected_person"]})
            except (OSError,ValueError,KeyError,TypeError,AttributeError,IndexError) as error:
                errors.append({"admission_sha256":directory.name,"reason":str(error)})
    except (OSError,ValueError,KeyError,TypeError,AttributeError,IndexError) as error:
        return {"state":"UNKNOWN","reason":str(error),"cases":[],"errors":errors}
    # UNKNOWN never silently clears a previously explicit admitted Out. A
    # confirmed replacement is a separate person, not a correction of the
    # original expected QB. Only a later explicit same-player non-Out status
    # can replace the earlier explicit status in this consumer registry.
    latest={}
    for case in cases:
        if case["state"] not in ("QUALIFIED_OUT","NOT_OUT"):
            continue
        key=(case["team_side"],case["person"]["native_id"],case["person"]["canonical_id"])
        prior=latest.get(key)
        if prior is None or _clock(case["admitted_at_utc"])>_clock(prior["admitted_at_utc"]):
            latest[key]=case
        elif _clock(case["admitted_at_utc"])==_clock(prior["admitted_at_utc"]) and case["state"]!=prior["state"]:
            retained=prior if prior.get("qualified_out") else case
            latest[key]={**retained,"reason":"CONFLICTING_SAME_TIME_ADMISSIONS_EXPLICIT_OUT_NOT_CLEARED"}
            errors.append({"reason":"CONFLICTING_SAME_TIME_ADMISSIONS","person":case["person"]})
    positive=[c for c in latest.values() if c.get("qualified_out")]
    return {"state":"QUALIFIED_OUT" if positive else ("UNKNOWN" if errors or not latest else "NOT_OUT"),
            "reason":"EXACT_EXPECTED_QB_OFFICIAL_OUT" if positive else "NO_QUALIFIED_OUT_EVIDENCE",
            "cases":cases,"current_cases":list(latest.values()),"qualified_cases":positive,"errors":errors}

def assess_consumer_game(session, *, game_id, decision, moment, settings):
    from sqlalchemy import select
    from app.models import Game,GameTeam,Team,League
    gid=UUID(str(game_id))
    game=session.get(Game,gid)
    if game is None:
        return {"state":"UNKNOWN","reason":"CANONICAL_GAME_NOT_REGISTERED","cases":[]}
    league=session.get(League,game.league_id)
    if league is None or league.code!="NFL":
        return {"state":"UNKNOWN","reason":"CANONICAL_GAME_IS_NOT_NFL","cases":[]}
    starts=game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=timezone.utc)
    rows=session.execute(select(GameTeam,Team).join(Team,Team.id==GameTeam.team_id)
                         .where(GameTeam.game_id==gid)).all()
    teams={("home" if gt.is_home else "away"):
           {"canonical_id":str(gt.team_id),"code":team.abbreviation,
            "side":"home" if gt.is_home else "away"} for gt,team in rows}
    if set(teams)!={"home","away"}:
        return {"state":"UNKNOWN","reason":"CANONICAL_TWO_SIDED_TEAM_BINDING_MISSING","cases":[]}
    return load_admissions(game_id=gid,native_event_id=decision.get("native_event_id"),
                           starts_at=starts,teams=teams,moment=moment,
                           admissions_root=getattr(settings,"owner_nfl_qb_out_admissions_root",None),
                           observation_root=getattr(settings,"owner_nfl_qb_out_observation_root",None))

def apply_to_decision(session, decision, *, game_id, moment, settings):
    """Existing qualified NFL funding only. No missing-data veto or p change."""
    state=runtime_state(settings,now=moment)
    original=(decision.get("funding_restriction") or {}).get("original_qualification") or {}
    if not state["active_at_observation"] or (decision.get("outcome")!="QUALIFIED" and original.get("outcome")!="QUALIFIED"):
        return decision
    assessed=assess_consumer_game(session,game_id=game_id,decision=decision,moment=moment,settings=settings)
    receipt={**state,"assessment":assessed,"record_only":assessed.get("state")=="QUALIFIED_OUT",
             "original_qualification":original or {"outcome":decision.get("outcome"),"reason":decision.get("reason")},
             "reason":QB_REASON if assessed.get("state")=="QUALIFIED_OUT" else "QB_STATUS_UNKNOWN_OR_NOT_OUT",
             "no_missing_data_veto":True}
    result={**decision,"qb_out_gate":receipt}
    if not receipt["record_only"]:
        return result
    existing=decision.get("funding_restriction")
    if existing and existing.get("record_only"):
        result["funding_restriction"]={**existing,
                                       "additional_funding_restrictions":[*(existing.get("additional_funding_restrictions") or []),receipt]}
        return result
    if existing:
        result["samebook_gap_restriction"]=existing
    result.update(outcome="RECORD_ONLY",reason=QB_REASON,
                  detail="Owner-approved expected-QB-Out funding restriction; original decision and prices retained; no position.",
                  funding_restriction=receipt)
    return result
