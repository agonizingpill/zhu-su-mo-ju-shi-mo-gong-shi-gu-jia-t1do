from fastapi import APIRouter,Depends
from sqlalchemy import select
from .db import db_session
from .auth import current,writers,admin
from .substitute_models import SubstituteTrial
from .substitute_schemas import PolicyInput,TrialInput,ApproveInput,RevokeInput,IssueInput
from . import substitute_service as svc
router=APIRouter()
@router.get('/substitute-policy')
def get_policy(s=Depends(db_session),u=Depends(current)):return svc.policy(s)
@router.put('/substitute-policy')
def put_policy(p:PolicyInput,s=Depends(db_session),u=Depends(admin)):
    x=svc.set_policy(s,p,u);s.commit();return x
@router.get('/substitute-trials')
def trials(s=Depends(db_session),u=Depends(current)):return [svc.trial_view(s,x)for x in s.scalars(select(SubstituteTrial).order_by(SubstituteTrial.id.desc()))]
@router.post('/substitute-trials')
def create(p:TrialInput,s=Depends(db_session),u=Depends(writers)):
    x=svc.create_trial(s,p,u);s.commit();return x
@router.post('/substitute-trials/{tid}/evaluate')
def evaluate(tid:int,s=Depends(db_session),u=Depends(writers)):
    x=svc.evaluate(s,tid,u);s.commit();return x
@router.post('/substitute-trials/{tid}/approve')
def approve(tid:int,p:ApproveInput,s=Depends(db_session),u=Depends(admin)):
    x=svc.approve(s,tid,p,u);s.commit();return x
@router.post('/substitute-trials/{tid}/revoke')
def revoke(tid:int,p:RevokeInput,s=Depends(db_session),u=Depends(admin)):
    x=svc.revoke(s,tid,p,u);s.commit();return x
@router.post('/substitute-trials/{tid}/issue')
def issue(tid:int,p:IssueInput,s=Depends(db_session),u=Depends(writers)):
    x=svc.issue(s,tid,p,u);s.commit();return x
