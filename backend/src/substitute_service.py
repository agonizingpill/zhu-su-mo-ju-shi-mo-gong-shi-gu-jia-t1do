"""Yield-drop interval estimated from signed single-grade actuals, corrected by mould and machine."""
import math
from datetime import datetime,timezone
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .models import Estimate,Mould,MachineSlot
from .execution_models import TrialExecution,CostReview
from .workshop_models import ResinLot,TrialMaterial
from .substitute_models import SubstitutePolicy,SubstituteTrial,SubstituteIssue
from .services import item,serialize,record
try:
    from scipy.stats import t as student_t
except Exception:  # pragma: no cover - scipy ships with scikit-learn
    student_t=None

MIN_SAMPLES=3
DEFAULT_CONFIDENCE=0.95

def now_iso(): return datetime.now(timezone.utc).isoformat()
def policy(s):
    p=s.get(SubstitutePolicy,1)
    return {'tolerance_pp':float(p.tolerance_pp) if p and p.tolerance_pp is not None else None,
            'updated_by':p.updated_by if p else '', 'updated_at':p.updated_at if p else ''}
def set_policy(s,data,u):
    p=s.query(SubstitutePolicy).filter_by(id=1).with_for_update().one_or_none()
    if not p:
        p=SubstitutePolicy(id=1,tolerance_pp=data.tolerance_pp,updated_by=u.email,updated_at=now_iso());s.add(p)
    else:
        p.tolerance_pp=data.tolerance_pp;p.updated_by=u.email;p.updated_at=now_iso()
    record(s,u,f'设置替代树脂良率容差 {data.tolerance_pp}pp');s.flush();return policy(s)

def trial_view(s,x):
    d=serialize(x)
    e=item(s,Estimate,x.estimate_id);d['estimate_code']=e.code;d['mould_code']=item(s,Mould,x.mould_id).code
    d['quota_kg']=float(x.quota_kg);d['issued_kg']=float(x.issued_kg);d['remaining_kg']=float(x.quota_kg-x.issued_kg)
    d['can_issue']=x.status=='approved' and x.quota_kg>x.issued_kg
    d['issues']=[issue_view(s,i) for i in s.scalars(select(SubstituteIssue).where(SubstituteIssue.trial_id==x.id).order_by(SubstituteIssue.id))]
    return d
def issue_view(s,x):
    d=serialize(x);m=item(s,TrialMaterial,x.material_id);e=item(s,TrialExecution,x.execution_id)
    d.update(estimate_code=e.snapshot['estimate_code'],lot_code=item(s,ResinLot,x.lot_id).code,
             unit_price_snapshot=float(m.unit_price_snapshot),material_state=m.state,
             returned_kg=float(m.returned_kg),
             net_cost=float(((m.issued_kg-m.returned_kg)*m.unit_price_snapshot).quantize(Decimal('.01'))))
    return d

def target_context(s,e):
    if e.status!='committed': raise HTTPException(409,'仅已承诺机时的估单可登记替代试用，机台须先锁定')
    if not e.slot_id: raise HTTPException(409,'估单尚未承诺机台，无法按机台校正良率，请先完成机时承诺')
    slot=item(s,MachineSlot,e.slot_id)
    return e.mould_id,slot.machine

def create_trial(s,data,u):
    e=item(s,Estimate,data.estimate_id)
    if data.base_grade==data.substitute_grade: raise HTTPException(409,'替代牌号必须与基准牌号不同')
    if s.scalar(select(SubstituteTrial.id).where(SubstituteTrial.estimate_id==e.id,
            SubstituteTrial.substitute_grade==data.substitute_grade,SubstituteTrial.status!='revoked')):
        raise HTTPException(409,'该估单与替代牌号已有未撤销的试用，请直接查看或用新估单')
    mould_id,machine=target_context(s,e)
    x=SubstituteTrial(estimate_id=e.id,mould_id=mould_id,machine=machine,base_grade=data.base_grade,
                      substitute_grade=data.substitute_grade,status='pending',created_by=u.email,created_at=now_iso())
    try:
        with s.begin_nested():s.add(x);s.flush()
    except IntegrityError:
        raise HTTPException(409,'该估单与替代牌号已有未撤销的试用，请直接查看或用新估单')
    record(s,u,f'登记替代树脂试用 {e.code} / {data.substitute_grade}');return trial_view(s,x)

def signed_single_grade_samples(s):
    """Signed actuals whose issued lots all carry one grade, with mould/machine context."""
    rows=[];excluded={'multi_grade':0,'no_material':0,'no_machine':0,'no_shots':0}
    reviews=s.scalars(select(CostReview).where(CostReview.status=='signed').order_by(CostReview.id))
    for r in reviews:
        x=s.get(TrialExecution,r.execution_id)
        if not x: continue
        if not x.total_shots: excluded['no_shots']+=1;continue
        e=s.get(Estimate,x.estimate_id)
        if not e: continue
        snapshot=x.snapshot or {}
        slot_id=snapshot.get('slot_id') or e.slot_id
        slot=s.get(MachineSlot,slot_id) if slot_id else None
        machine=slot.machine if slot else snapshot.get('machine')
        if not machine: excluded['no_machine']+=1;continue
        mats=list(s.scalars(select(TrialMaterial).where(TrialMaterial.execution_id==x.id)))
        if not mats: excluded['no_material']+=1;continue
        grades={s.get(ResinLot,m.lot_id).grade for m in mats}
        if len(grades)!=1: excluded['multi_grade']+=1;continue
        rows.append({'grade':grades.pop(),'mould_id':e.mould_id,'machine':machine,
                     'yield':x.good_shots/x.total_shots,'execution_id':x.id,'estimate_code':e.code,
                     'good_shots':x.good_shots,'total_shots':x.total_shots})
    return rows,excluded

def _fit(rows,target_mould,target_machine):
    """Additive mould+machine OLS on yield; returns target prediction (fraction) and its standard error."""
    import numpy as np
    moulds=sorted({r['mould_id'] for r in rows});machines=sorted({r['machine'] for r in rows})
    def design(r):
        v=[1.0]+[1.0 if r['mould_id']==m else 0.0 for m in moulds[1:]]+[1.0 if r['machine']==m else 0.0 for m in machines[1:]]
        return v
    X=np.array([design(r) for r in rows],dtype=float);y=np.array([r['yield'] for r in rows],dtype=float)
    xt=np.array(design({'mould_id':target_mould,'machine':target_machine}),dtype=float)
    beta,_,rank,_=np.linalg.lstsq(X,y,rcond=None)
    df=len(rows)-int(rank)
    if df<1: raise ValueError('样本组合不足以估计波动')
    resid=y-X@beta;sigma2=float(resid@resid)/df
    cov=np.linalg.pinv(X.T@X);lever=float(xt@cov@xt)
    se=math.sqrt(max(sigma2*lever,0.0))
    return float(xt@beta),se,df,int(rank)

def _grade_stats(rows,grade,target_mould,target_machine):
    subset=[r for r in rows if r['grade']==grade]
    if len(subset)<MIN_SAMPLES: return None,f'牌号 {grade} 已签结单牌号实绩仅 {len(subset)} 条，少于 {MIN_SAMPLES} 条'
    if not any(r['mould_id']==target_mould for r in subset): return None,f'牌号 {grade} 缺少同模具已签结实绩'
    if not any(r['machine']==target_machine for r in subset): return None,f'牌号 {grade} 缺少同机台已签结实绩'
    try: pred,se,df,rank=_fit(subset,target_mould,target_machine)
    except ValueError as ex: return None,f'牌号 {grade} {ex}'
    return {'grade':grade,'samples':len(subset),'moulds':len({r['mould_id'] for r in subset}),
            'machines':len({r['machine'] for r in subset}),'yield_pct':round(pred*100,2),
            'se_pp':round(se*100,3),'df':df,'rank':rank,
            'yields':[round(r['yield']*100,2) for r in subset]},None

def evaluate(s,tid,u):
    x=s.query(SubstituteTrial).filter_by(id=tid).with_for_update().one_or_none()
    if not x: raise HTTPException(404,'替代试用不存在')
    if x.status in ('approved','revoked'): raise HTTPException(409,'已批准或已撤销的试用不能重新评估')
    pol=policy(s);tol=pol['tolerance_pp']
    rows,excluded=signed_single_grade_samples(s)
    base,base_err=_grade_stats(rows,x.base_grade,x.mould_id,x.machine)
    sub,sub_err=_grade_stats(rows,x.substitute_grade,x.mould_id,x.machine)
    result={'method':'按模具与机台加性校正的良率差，95% 区间由已签结单牌号实绩拟合',
            'confidence':DEFAULT_CONFIDENCE,'target':{'mould_id':x.mould_id,'machine':x.machine},
            'base_grade':x.base_grade,'substitute_grade':x.substitute_grade,
            'base':base,'substitute':sub,'excluded':excluded}
    x.tolerance_pp=Decimal(str(tol)) if tol is not None else None
    if base is None or sub is None:
        result.update(verdict='insufficient',reason=base_err or sub_err,
                      notes=[e for e in (base_err,sub_err) if e],signed_samples=len(rows))
        x.result=result;x.status='held';record(s,u,f'替代树脂试用暂缓评估 {x.id}');return trial_view(s,x)
    diff=base['yield_pct']-sub['yield_pct'];se=math.hypot(base['se_pp'],sub['se_pp']);df=max(1,base['df']+sub['df'])
    crit=float(student_t.ppf(.975,df)) if student_t else 1.96
    margin=crit*se
    result.update(drop_pp=round(diff,2),lower_pp=round(diff-margin,2),upper_pp=round(diff+margin,2),
                  se_pp=round(se,3),df=df,critical=round(crit,3),signed_samples=len(rows))
    if tol is None:
        result.update(verdict='tolerance_missing',reason='主管尚未设置良率容差，试用暂缓')
        x.status='held';record(s,u,f'替代树脂试用暂缓：未设容差 {x.id}');return trial_view(s,x)
    result['tolerance_pp']=tol
    if result['upper_pp']>tol:
        result.update(verdict='over_tolerance',reason=f'良率降幅区间上限 {result["upper_pp"]:.2f}pp 超过容差 {tol:.2f}pp')
        x.status='held'
    else:
        result.update(verdict='within_tolerance',reason=f'区间上限 {result["upper_pp"]:.2f}pp 未超出容差 {tol:.2f}pp，可提交主管批额度')
        x.status='eligible'
    x.result=result;record(s,u,f'评估替代树脂试用 {x.id}：{result["verdict"]}');return trial_view(s,x)

def approve(s,tid,data,u):
    x=s.query(SubstituteTrial).filter_by(id=tid).with_for_update().one_or_none()
    if not x: raise HTTPException(404,'替代试用不存在')
    if x.status!='eligible': raise HTTPException(409,'只有评估在容差内、可批准的试用才能批额度')
    tol=policy(s)['tolerance_pp']
    if tol is None or x.tolerance_pp is None or not math.isclose(float(x.tolerance_pp),tol,abs_tol=1e-9):
        raise HTTPException(409,'良率容差已变更，请重新评估后再批额度')
    if data.quota_kg<=0: raise HTTPException(409,'批准额度必须大于 0kg')
    x.quota_kg=data.quota_kg;x.status='approved';x.approved_by=u.email;x.approved_at=now_iso()
    record(s,u,f'批准替代树脂额度 {x.id} / {data.quota_kg}kg');return trial_view(s,x)

def revoke(s,tid,data,u):
    x=s.query(SubstituteTrial).filter_by(id=tid).with_for_update().one_or_none()
    if not x: raise HTTPException(404,'替代试用不存在')
    if x.status=='revoked': raise HTTPException(409,'该替代试用已撤销，不可重复撤销')
    x.status='revoked';x.revoked_by=u.email;x.revoked_at=now_iso();x.revoke_reason=data.reason
    record(s,u,f'撤销替代树脂试用 {x.id}');return trial_view(s,x)

def issue(s,tid,data,u):
    # The trial row lock orders this against concurrent revoke: whichever commits first wins.
    x=s.query(SubstituteTrial).filter_by(id=tid).with_for_update().one_or_none()
    if not x: raise HTTPException(404,'替代试用不存在')
    prior=s.scalar(select(SubstituteIssue).where(SubstituteIssue.idempotency_key==data.idempotency_key))
    if prior:
        if prior.trial_id!=x.id or prior.execution_id!=data.execution_id or prior.lot_id!=data.lot_id:
            raise HTTPException(409,'幂等键已用于其他领料请求')
        if prior.issued_kg!=data.issued_kg: raise HTTPException(409,'幂等键已用于不同领料重量')
        return trial_view(s,x)
    if x.status=='revoked': raise HTTPException(409,'替代试用已撤销，撤销生效后不能再领料')
    if x.status!='approved': raise HTTPException(409,'替代试用尚未批准额度，不能领料')
    ex=s.query(TrialExecution).filter_by(id=data.execution_id).with_for_update().one_or_none()
    if not ex: raise HTTPException(404,'实绩记录不存在')
    if ex.estimate_id!=x.estimate_id: raise HTTPException(409,'领料实绩不属于本试用的估单')
    if ex.status!='running': raise HTTPException(409,'只有运行中的试模可领材料')
    ex_machine=(ex.snapshot or {}).get('machine')
    if ex_machine and ex_machine!=x.machine:
        raise HTTPException(409,f'实绩机台为 {ex_machine}，与按 {x.machine} 评估批额度的机台不一致，请重新登记试用')
    lot=s.query(ResinLot).filter_by(id=data.lot_id).with_for_update().one_or_none()
    if not lot: raise HTTPException(404,'树脂批次不存在')
    if lot.grade!=x.substitute_grade: raise HTTPException(409,'领料批次牌号与获批替代牌号不一致')
    if lot.state!='available': raise HTTPException(409,'树脂批次已隔离，不能领料')
    remaining=x.quota_kg-x.issued_kg
    if data.issued_kg>remaining: raise HTTPException(409,f'超出剩余额度，仅可领 {remaining}kg（退料不恢复额度）')
    if s.scalar(select(TrialMaterial.id).where(TrialMaterial.execution_id==ex.id,TrialMaterial.lot_id==lot.id)):
        raise HTTPException(409,'该试模已领此批材料，不可重复领料')
    if data.issued_kg>lot.available_kg: raise HTTPException(409,'树脂可用库存不足，库存与额度均未扣减')
    m=TrialMaterial(execution_id=ex.id,lot_id=lot.id,issued_kg=data.issued_kg,returned_kg=0,
                    unit_price_snapshot=lot.unit_price,state='issued')
    link=SubstituteIssue(trial_id=x.id,material_id=None,execution_id=ex.id,lot_id=lot.id,
                         issued_kg=data.issued_kg,idempotency_key=data.idempotency_key,
                         created_by=u.email,created_at=now_iso())
    # Stock and quota move inside one savepoint; a duplicate-key race rolls both back, so retries replay.
    try:
        with s.begin_nested():
            lot.available_kg-=data.issued_kg
            x.issued_kg+=data.issued_kg
            s.add(m);s.flush()
            link.material_id=m.id
            s.add(link);s.flush()
    except IntegrityError:
        s.refresh(lot);s.refresh(x)
        prior=s.scalar(select(SubstituteIssue).where(SubstituteIssue.idempotency_key==data.idempotency_key))
        if prior and prior.trial_id==x.id and prior.execution_id==data.execution_id and prior.lot_id==data.lot_id and prior.issued_kg==data.issued_kg:
            return trial_view(s,x)
        raise HTTPException(409,'并发领料冲突，请稍后用同一凭据重试；库存与额度不会重复扣减')
    record(s,u,f'替代树脂领料 {x.id} / {data.issued_kg}kg');s.flush();return trial_view(s,x)
