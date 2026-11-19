from decimal import Decimal
from pydantic import BaseModel,Field,ConfigDict
class Clean(BaseModel):model_config=ConfigDict(str_strip_whitespace=True)
class PolicyInput(BaseModel):
    tolerance_pp:Decimal=Field(ge=0,le=100,max_digits=6,decimal_places=3)
class TrialInput(Clean):
    estimate_id:int=Field(gt=0)
    base_grade:str=Field(min_length=2,max_length=60)
    substitute_grade:str=Field(min_length=2,max_length=60)
class ApproveInput(BaseModel):
    quota_kg:Decimal=Field(gt=0,le=100000,max_digits=12,decimal_places=3)
class RevokeInput(Clean):
    reason:str=Field(min_length=8,max_length=300)
class IssueInput(BaseModel):
    execution_id:int=Field(gt=0)
    lot_id:int=Field(gt=0)
    issued_kg:Decimal=Field(gt=0,le=100000,max_digits=12,decimal_places=3)
    idempotency_key:str=Field(min_length=8,max_length=80)
