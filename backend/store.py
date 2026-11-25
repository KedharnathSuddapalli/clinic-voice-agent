"""In-memory mock clinic data + a tiny event bus for the dashboard."""
import asyncio, datetime as dt, re

DOCTORS = [
    {"id": "d1", "name": "Dr. Ananya Rao", "speciality": "Cardiology"},
    {"id": "d2", "name": "Dr. Vikram Shah", "speciality": "Dermatology"},
    {"id": "d3", "name": "Dr. Meera Iyer", "speciality": "Pediatrics"},
    {"id": "d4", "name": "Dr. Rohan Mehta", "speciality": "Orthopedics"},
    {"id": "d5", "name": "Dr. Sana Khan", "speciality": "General Physician"},
]
TIMES = ["09:00", "10:30", "12:00", "15:00", "16:30"]
SLOTS = {}  # id -> slot
for _d in DOCTORS:
    for i in range(1, 6):
        day = (dt.date.today() + dt.timedelta(days=i)).isoformat()
        for t in TIMES:
            SLOTS[f"{_d['id']}|{day}|{t}"] = dict(doctor_id=_d["id"], date=day, time=t, phone=None, patient=None)

_subs: set = set()
def subscribe():
    q = asyncio.Queue(); _subs.add(q); return q
def unsubscribe(q): _subs.discard(q)
def publish(ev):
    for q in list(_subs): q.put_nowait(ev)

def norm_phone(p):
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else None

def _spec_match(q, s):
    """Loose match so 'dermatologist' hits Dermatology, 'orthopedician' hits Orthopedics, 'GP' hits General Physician."""
    q, s = q.lower().strip(), s.lower()
    if q in s or s[:5] in q or q[:5] in s: return True
    return s.startswith("general") and any(w in q for w in ("general", "physician", "practi", "gp", "family"))

def _find(doctor=None, speciality=None):
    out = []
    for d in DOCTORS:
        if doctor and doctor.lower().replace("dr.", "").strip() not in d["name"].lower(): continue
        if speciality and not _spec_match(speciality, d["speciality"]): continue
        out.append(d)
    return out

def available(doctor=None, speciality=None, date=None):
    res = []
    for d in _find(doctor, speciality):
        times = [(s["date"], s["time"]) for s in SLOTS.values()
                 if s["doctor_id"] == d["id"] and not s["phone"] and (not date or s["date"] == date)]
        if times: res.append({**d, "slots": [{"date": a, "time": b} for a, b in sorted(times)][:6]})
    return res

def doctors_with_slots():
    return [{**d, "slots": [dict(date=s["date"], time=s["time"]) for s in SLOTS.values()
             if s["doctor_id"] == d["id"] and not s["phone"]][:10]} for d in DOCTORS]

def appointments():
    names = {d["id"]: d for d in DOCTORS}
    return sorted([{"doctor": names[s["doctor_id"]]["name"], "speciality": names[s["doctor_id"]]["speciality"],
                    "date": s["date"], "time": s["time"], "phone": s["phone"], "patient": s["patient"]}
                   for s in SLOTS.values() if s["phone"]], key=lambda a: (a["date"], a["time"]))

def book(phone, doctor, date, time, patient):
    p = norm_phone(phone)
    if not p: return {"ok": False, "error": "invalid_phone"}
    if any(s["phone"] == p for s in SLOTS.values()):
        return {"ok": False, "error": "caller already has an appointment; cancel it first"}
    ds = _find(doctor)
    if len(ds) != 1: return {"ok": False, "error": "doctor not found or ambiguous"}
    s = SLOTS.get(f"{ds[0]['id']}|{date}|{time}")
    if not s: return {"ok": False, "error": "no such slot"}
    if s["phone"]: return {"ok": False, "error": "slot already taken"}
    s.update(phone=p, patient=patient)
    appt = next(a for a in appointments() if a["phone"] == p)
    publish({"type": "booked", "appointment": appt})
    return {"ok": True, "appointment": appt}

def cancel(phone):
    p = norm_phone(phone)
    if not p: return {"ok": False, "error": "invalid_phone"}
    for s in SLOTS.values():
        if s["phone"] == p:
            appt = next(a for a in appointments() if a["phone"] == p)
            s.update(phone=None, patient=None)
            publish({"type": "cancelled", "appointment": appt})
            return {"ok": True, "cancelled": appt}
    return {"ok": False, "error": "no appointment found for this number"}