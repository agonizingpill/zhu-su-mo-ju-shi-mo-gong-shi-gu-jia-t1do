"""Substitute resin is released only through an evaluated, quota-capped trial."""
from decimal import Decimal
from sqlalchemy import String,ForeignKey,JSON,CheckConstraint,Numeric,UniqueConstraint,Index,text
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base

class SubstitutePolicy(Base):
    __tablename__='substitute_policies'
    __table_args__=(CheckConstraint('tolerance_pp >= 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    tolerance_pp:Mapped[Decimal]=mapped_column(Numeric(6,3))
    updated_by:Mapped[str]=mapped_column(String(120),default='')
    updated_at:Mapped[str]=mapped_column(String(40),default='')

class SubstituteTrial(Base):
    """One active substitute-grade trial per estimate and substitute grade."""
    __tablename__='substitute_trials'
    __table_args__=(
        CheckConstraint('quota_kg >= 0 AND issued_kg >= 0 AND issued_kg <= quota_kg'),
        CheckConstraint('base_grade <> substitute_grade'),
        Index('uq_substitute_active','estimate_id','substitute_grade',unique=True,
              postgresql_where=text("status <> 'revoked'"),
              sqlite_where=text("status <> 'revoked'")),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    estimate_id:Mapped[int]=mapped_column(ForeignKey('estimates.id'))
    mould_id:Mapped[int]=mapped_column(ForeignKey('moulds.id'))
    machine:Mapped[str]=mapped_column(String(40))
    base_grade:Mapped[str]=mapped_column(String(60))
    substitute_grade:Mapped[str]=mapped_column(String(60))
    status:Mapped[str]=mapped_column(String(20),default='pending')
    tolerance_pp:Mapped[Decimal|None]=mapped_column(Numeric(6,3),nullable=True)
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    quota_kg:Mapped[Decimal]=mapped_column(Numeric(12,3),default=0)
    issued_kg:Mapped[Decimal]=mapped_column(Numeric(12,3),default=0)
    created_by:Mapped[str]=mapped_column(String(120),default='')
    created_at:Mapped[str]=mapped_column(String(40),default='')
    approved_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    approved_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
    revoked_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    revoked_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
    revoke_reason:Mapped[str]=mapped_column(String(300),default='')

class SubstituteIssue(Base):
    """Idempotent link between an approved quota and one material issue row."""
    __tablename__='substitute_issues'
    __table_args__=(UniqueConstraint('idempotency_key'),CheckConstraint('issued_kg > 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    trial_id:Mapped[int]=mapped_column(ForeignKey('substitute_trials.id'))
    material_id:Mapped[int]=mapped_column(ForeignKey('trial_materials.id'),unique=True)
    execution_id:Mapped[int]=mapped_column(ForeignKey('trial_executions.id'))
    lot_id:Mapped[int]=mapped_column(ForeignKey('resin_lots.id'))
    issued_kg:Mapped[Decimal]=mapped_column(Numeric(12,3))
    idempotency_key:Mapped[str]=mapped_column(String(80))
    created_by:Mapped[str]=mapped_column(String(120),default='')
    created_at:Mapped[str]=mapped_column(String(40),default='')
