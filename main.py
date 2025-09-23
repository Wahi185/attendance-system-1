import csv, io, os
from datetime import datetime
from typing import Optional
import pytz
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session

# --- Config ---
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
TIMEZONE = os.getenv("TIMEZONE", "America/Vancouver")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# --- Database setup ---
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

# --- Models ---
class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"))
    location_id = Column(Integer, ForeignKey("locations.id"))
    active = Column(Boolean, default=True)
    qr_code_value = Column(String, unique=True, nullable=False)
    department = relationship("Department")
    location = relationship("Location")

class Punch(Base):
    __tablename__ = "punches"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), index=True, nullable=False)
    ts = Column(DateTime, index=True, nullable=False)
    action = Column(String, default="in")
    m_number = Column(String, nullable=True)
    location_id = Column(Integer, ForeignKey("locations.id"))
    department_id = Column(Integer, ForeignKey("departments.id"))
    device_label = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    employee = relationship("Employee")
    location = relationship("Location")
    department = relationship("Department")

Index("ix_punches_range", Punch.employee_id, Punch.ts)
Base.metadata.create_all(engine)

# --- FastAPI app ---
app = FastAPI(title="QR Time Punch API", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Helpers ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def now_local():
    tz = pytz.timezone(TIMEZONE)
    return datetime.now(tz)

# Default values
DEFAULT_DEPTS = ["Assembly", "Fabrication", "Electrical", "Admin", "IT"]
DEFAULT_LOCS = ["Main Shop", "Shop 6", "Field Site", "Office"]

@app.on_event("startup")
def seed_defaults():
    with SessionLocal() as db:
        if db.query(Department).count() == 0:
            for n in DEFAULT_DEPTS:
                db.add(Department(name=n))
        if db.query(Location).count() == 0:
            for n in DEFAULT_LOCS:
                db.add(Location(name=n))
        db.commit()

        # Auto-seed employees from CSV if present
        csv_path = "employees.csv"
        if os.path.isfile(csv_path):
            dept_map = {d.name: d.id for d in db.query(Department)}
            loc_map = {l.name: l.id for l in db.query(Location)}
            existing_qrs = {e.qr_code_value for e in db.query(Employee).all()}

            with open(csv_path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    qr = row.get("qr_code_value", "").strip()
                    name = row.get("name", "").strip()
                    if not qr or not name or qr in existing_qrs:
                        continue
                    dep_id = dept_map.get(row.get("department", "").strip())
                    loc_id = loc_map.get(row.get("location", "").strip())
                    db.add(Employee(
                        name=name,
                        qr_code_value=qr,
                        department_id=dep_id,
                        location_id=loc_id
                    ))
            db.commit()

# --- Schemas ---
class EmployeeCreate(BaseModel):
    name: str
    qr_code_value: str
    department_id: Optional[int] = None
    location_id: Optional[int] = None

class PunchIn(BaseModel):
    qr_code_value: str
    action: str = Field(default="in")
    m_number: Optional[str] = None
    location_id: Optional[int] = None
    department_id: Optional[int] = None
    device_label: Optional[str] = None
    notes: Optional[str] = None

# --- Security ---
def require_admin(x_api_key: Optional[str] = Header(None)):
    if ADMIN_API_KEY and x_api_key != ADMIN_API_KEY:
        raise HTTPException(401, detail="Invalid or missing X-API-Key")

# --- Routes ---
@app.post("/api/employees")
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    emp = Employee(
        name=payload.name, qr_code_value=payload.qr_code_value,
        department_id=payload.department_id, location_id=payload.location_id
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return {"id": emp.id, "name": emp.name}

@app.get("/api/employees")
def list_employees(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return [{"id": e.id, "name": e.name, "qr": e.qr_code_value} for e in db.query(Employee).all()]

@app.post("/api/punch")
def punch(payload: PunchIn, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter_by(qr_code_value=payload.qr_code_value, active=True).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    # Duplicate prevention
    last = (
        db.query(Punch)
        .filter(Punch.employee_id == emp.id)
        .order_by(Punch.ts.desc())
        .first()
    )
    if last and last.action == payload.action and (last.m_number or "") == (payload.m_number or ""):
        raise HTTPException(400, f"Duplicate punch: already '{payload.action.upper()}' for this job")

    ts = now_local().replace(microsecond=0)
    p = Punch(
        employee_id=emp.id, ts=ts, action=payload.action, m_number=payload.m_number,
        location_id=payload.location_id or emp.location_id,
        department_id=payload.department_id or emp.department_id,
        device_label=payload.device_label, notes=payload.notes
    )
    db.add(p)
    db.commit()
    return {"message": "Punch recorded", "employee": emp.name, "ts": ts.isoformat(), "action": p.action}

@app.get("/api/departments")
def list_departments(db: Session = Depends(get_db)):
    return [{"id": d.id, "name": d.name} for d in db.query(Department)]

@app.get("/api/locations")
def list_locations(db: Session = Depends(get_db)):
    return [{"id": l.id, "name": l.name} for l in db.query(Location)]

@app.get("/api/export")
def export_csv(db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)

    # Title row
    writer.writerow([])
    writer.writerow(["", "", "Optimil QR Time Punch System", "", "", ""])
    writer.writerow([])

    # Header
    writer.writerow(["Employee", "Date", "Department", "Location", "Action", "M_Number", "Timestamp", "Duration"])

    punches = (
        db.query(Punch)
        .join(Employee)
        .order_by(Employee.name.asc(), Punch.ts.asc())
        .all()
    )

    last_emp, last_date = None, None
    in_time, total_for_day = None, 0

    for p in punches:
        emp_name = p.employee.name if p.employee else ""
        punch_date = p.ts.date().isoformat()

        # Blank line when employee changes
        if last_emp and last_emp != emp_name:
            writer.writerow([])
            last_date = None

        # Daily total row when date changes
        if last_date and last_date != punch_date:
            writer.writerow([last_emp, last_date, "", "", "TOTAL", "", "", f"{round(total_for_day/3600,2)}h"])
            writer.writerow([])
            total_for_day = 0
            in_time = None

        duration_str = ""
        if p.action.lower() == "in":
            in_time = p.ts
        elif p.action.lower() == "out" and in_time:
            delta = (p.ts - in_time).total_seconds()
            duration_str = f"{int(delta//3600)}h {int((delta%3600)//60)}m"
            total_for_day += delta
            in_time = None
        elif p.action.lower() == "break_in":
            in_time = p.ts
        elif p.action.lower() == "break_out" and in_time:
            delta = (p.ts - in_time).total_seconds()
            duration_str = f"BREAK {int(delta//3600)}h {int((delta%3600)//60)}m"
            in_time = None

        writer.writerow([
            emp_name,
            punch_date,
            p.department.name if p.department else "",
            p.location.name if p.location else "",
            p.action.upper(),
            p.m_number or "",
            p.ts.strftime("%Y-%m-%d %H:%M:%S"),
            duration_str
        ])

        last_emp, last_date = emp_name, punch_date

    # Final total row
    if last_emp and last_date:
        writer.writerow([last_emp, last_date, "", "", "TOTAL", "", "", f"{round(total_for_day/3600,2)}h"])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=attendance_report.csv"}
    )

@app.get("/")
def root():
    return FileResponse("static/index.html")
