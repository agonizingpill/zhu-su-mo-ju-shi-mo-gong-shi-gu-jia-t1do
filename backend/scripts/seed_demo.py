import random
from sqlalchemy import select, func
from src.db import Base,engine,Session
from src.models import User,Mould,MachineSlot,Estimate,TrialHistory
from src.execution_models import TrialExecution,CostReview
from src.workshop_models import MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery
from src.substitute_models import SubstitutePolicy,SubstituteTrial,SubstituteIssue
from src.auth import hash_password


SUB_DEMO = {
    'ABS自然色': {'slug': 'BASE', 'moulds': {'MD-42': .95, 'MD-86': .90}, 'lot': 'RS-HIST-BASE', 'price': 20},
    'ABS-替代-DEMO': {'slug': 'ALT', 'moulds': {'MD-42': .92, 'MD-86': .87}, 'lot': 'RS-HIST-ALT', 'price': 18},
}

def seed_substitute_history(s):
    """Deterministic signed single-grade actuals so a demo trial shows a yield-drop interval."""
    slot_of = {}
    for machine, start, _ in [('M280', '2026-11-18 08:00', 48), ('M450', '2026-11-18 08:00', 72)]:
        slot_of[machine] = s.scalar(select(MachineSlot).where(MachineSlot.machine == machine, MachineSlot.start_hour == start))
    moulds = {m.code: m for m in s.scalars(select(Mould))}
    for grade, spec in SUB_DEMO.items():
        # Each grade's history uses its own fully consumed lot, leaving live demo stock untouched.
        per_grade = [(code, machine) for code in spec['moulds'] for machine in slot_of if slot_of[machine]]
        total_kg = 5 * len(per_grade) * 2
        lot = s.scalar(select(ResinLot).where(ResinLot.code == spec['lot']))
        if not lot:
            lot = ResinLot(code=spec['lot'], grade=grade, received_kg=total_kg, available_kg=0, unit_price=spec['price'], state='available')
            s.add(lot); s.flush()
        for mould_code, base in spec['moulds'].items():
            for machine, offset in [('M280', 0), ('M450', .01)]:
                slot = slot_of[machine]
                if not slot:
                    continue
                for rep in range(2):
                    code = f'SUB-DEMO-{spec["slug"]}-{mould_code}-{machine}-{rep + 1}'
                    if s.scalar(select(Estimate).where(Estimate.code == code)):
                        continue
                    good = int(round((base - offset) * 100)) - rep
                    e = Estimate(code=code, mould_id=moulds[mould_code].id, slot_id=slot.id, rate=300, setup_hours=1,
                                 predicted_hours=9, quote_amount=4000, status='committed',
                                 model_result={'note': '替代树脂评估用已签结示范实绩，非模型推理'})
                    s.add(e); s.flush()
                    x = TrialExecution(estimate_id=e.id, started_at=f'2026-11-1{7 + rep}T00:00:00+00:00',
                                       ended_at=f'2026-11-1{7 + rep}T09:00:00+00:00', actual_hours=9,
                                       good_shots=good, total_shots=100, overtime_reason='',
                                       snapshot={'estimate_code': code, 'predicted_hours': 9, 'quoted_amount': 4000,
                                                 'rate': 300, 'mould_code': mould_code, 'slot_id': slot.id, 'machine': machine},
                                       status='settled')
                    s.add(x); s.flush()
                    ledger_net = round(5 * spec['price'], 2)
                    r = CostReview(execution_id=x.id, material_cost=ledger_net, actual_cost=round(2700 + ledger_net, 2),
                                   quoted_amount=4000, variance_hours=0, variance_pct=0,
                                   margin=round(4000 - 2700 - ledger_net, 2), reason='', status='signed',
                                   signed_by='admin@ruimo.demo')
                    s.add(r); s.flush()
                    s.add(TrialMaterial(execution_id=x.id, lot_id=lot.id, issued_kg=5, returned_kg=0,
                                        unit_price_snapshot=spec['price'], state='issued'))
                    s.add(ReviewMaterialLedger(review_id=r.id, net_cost=ledger_net,
                                               lines=[{'lot_id': lot.id, 'issued_kg': 5, 'returned_kg': 0,
                                                       'unit_price_snapshot': spec['price'], 'net_cost': ledger_net}]))
                    s.flush()

def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('admin','estimator','viewer'):
            if not s.scalar(select(User).where(User.email==role+'@ruimo.demo')): s.add(User(email=role+'@ruimo.demo',role=role,password_hash=hash_password('Mould2026!')))
        for code,name,cav,slides,steel,kg in [('MD-86','八腔医疗接头模',8,4,'S136',860),('MD-42','双腔仪表外壳模',2,2,'P20',420),('MD-115','十六腔盖件模',16,6,'H13',1150)]:
            if not s.scalar(select(Mould).where(Mould.code==code)): s.add(Mould(code=code,name=name,cavities=cav,slides=slides,steel=steel,weight_kg=kg))
        s.flush()
        for machine,start,cap in [('M280','2026-11-18 08:00',48),('M280','2026-11-19 08:00',4),('M450','2026-11-18 08:00',72)]:
            if not s.scalar(select(MachineSlot).where(MachineSlot.machine==machine,MachineSlot.start_hour==start)): s.add(MachineSlot(machine=machine,start_hour=start,capacity_hours=cap,reserved_hours=0))
        rng=random.Random(42)
        for i in range(160):
            code=f'TR-{i+1:03d}'
            if s.scalar(select(TrialHistory).where(TrialHistory.trial_code==code)):continue
            cav=rng.choice([2,4,8,16]);sl=rng.randint(0,8);kg=rng.uniform(180,1600);steel=rng.choice(['P20','H13','S136'])
            hours=2+cav*.3+sl*.65+kg*.004+cav*sl*.035+(['P20','H13','S136'].index(steel))*.8+rng.gauss(0,.4)
            s.add(TrialHistory(trial_code=code,cavities=cav,slides=sl,steel=steel,weight_kg=round(kg,1),actual_hours=round(hours,2)))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-26017')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-86'))
            s.add(Estimate(code='QE-26017',mould_id=m.id,rate=280,setup_hours=2,quote_amount=8000,status='draft'))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-FLOOR-DEMO')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-42'));slot=s.scalar(select(MachineSlot).where(MachineSlot.machine=='M450'))
            s.add(Estimate(code='QE-FLOOR-DEMO',mould_id=m.id,slot_id=slot.id,rate=300,setup_hours=1,predicted_hours=10,quote_amount=5000,status='committed',model_result={'note':'已承诺的演示订单，供实绩采集；不作模型效果指标'}));slot.reserved_hours+=10
        for code,grade,qty,price,state in [('RS-P20-DEMO','ABS自然色',500,20,'available'),('RS-ISOLATED','ABS复检隔离',50,18,'quarantine'),('RS-ALT-DEMO','ABS-替代-DEMO',300,18,'available')]:
            if not s.scalar(select(ResinLot).where(ResinLot.code==code)):
                s.add(ResinLot(code=code,grade=grade,received_kg=qty,available_kg=qty,unit_price=price,state=state))
        s.flush()
        seed_substitute_history(s)
        s.commit()
        print({t.__tablename__:s.scalar(select(func.count()).select_from(t)) for t in (User,Mould,MachineSlot,Estimate,TrialHistory,TrialExecution,CostReview,MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery,SubstitutePolicy,SubstituteTrial,SubstituteIssue)})
if __name__=='__main__':seed()
