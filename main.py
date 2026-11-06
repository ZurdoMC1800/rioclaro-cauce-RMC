"""Río Claro · Cauce — Centro de atención con IA (piloto AI-901)."""
import os, re, secrets, time, unicodedata
from pathlib import Path
from fastapi import FastAPI, Form, Header, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).parent
app = FastAPI(title="Río Claro Cauce")

# Montar estáticos de manera segura si la carpeta existe
if (BASE / "static").exists():
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

def norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def tabla(nombre):
    try:
        path = BASE / "data" / nombre
        if not path.exists():
            return []
        filas = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("|")]
        if len(filas) < 3:
            return []
        cab = [c.strip() for c in filas[0].strip("|").split("|")]
        return [dict(zip(cab, [c.strip() for c in f.strip("|").split("|")])) for f in filas[2:]]
    except Exception:
        return []

# Carga segura de datos (si falta un archivo, inicia vacío en lugar de crashear con 503)
INV = tabla("inventario.md")
FAC = tabla("facturas.md")
ENT = tabla("entregas.md")

mxn = lambda t: float(str(t).replace("$", "").replace(",", "")) if t else 0.0

QUEJAS = [{"folio": "Q-1001", "cliente": "Colegio del Pacífico, A.C.", "sucursal": "Querétaro",
           "tema": "Entrega con 23 días de atraso", "estado": "Abierta", "min": 0}]

FACT_REG = [{"id": i + 1, "proveedor": f.get("Proveedor (razón social)", "Proveedor"), "fecha": f.get("Fecha de emisión", "2026-01-01"),
             "total": mxn(f.get("Total (MXN)", "0")), "estado": f.get("Estado", "Pendiente"), "origen": "histórico"} for i, f in enumerate(FAC)]

USUARIOS = {  
    "ernesto": ("Don Ernesto", "Dirección", {"inv", "fac", "ent", "que"}),
    "laura": ("Laura", "Cuentas por pagar", {"inv", "fac"}),
    "mariana": ("Mariana", "Gerente León", {"inv", "ent", "que"}),
    "toño": ("Toño", "Jefe de bodega", {"inv"}),
    "carlos": ("Carlos", "Soporte y sistemas", {"inv", "fac", "ent", "que"}),
}

CLAVE_DEMO = "Cauce2026!"
SESIONES = {}
IA_ACTIVA = True

def quien(token):
    s = SESIONES.get(token or "")
    if not s: raise HTTPException(401, "Inicia sesión")
    return s

class Login(BaseModel): usuario: str; clave: str
class Pregunta(BaseModel): texto: str

@app.get("/")
def home(): 
    index_path = BASE / "static" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"status": "ok", "message": "App activa pero falta el archivo index.html en static/"}

@app.get("/health")
def health(): 
    ep = os.getenv("AZURE_AI_PROJECT_ENDPOINT") or os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    return {"status": "ok", "foundry": bool(ep), "ia_activa": IA_ACTIVA}

@app.post("/api/login")
def login(d: Login):
    u = USUARIOS.get(norm(d.usuario).replace("n~", "ñ")) or USUARIOS.get(d.usuario.lower())
    if not u or d.clave != CLAVE_DEMO: raise HTTPException(401, "Usuario o clave incorrectos")
    t = secrets.token_hex(16)
    SESIONES[t] = {"user": d.usuario.lower(), "nombre": u[0], "rol": u[1], "perm": u[2]}
    return {"token": t, "nombre": u[0], "rol": u[1], "perm": sorted(u[2])}

def resumen():
    bajos = [i for i in INV if int(i.get("Cantidad", 0) or 0) < 60]
    ven = [f for f in FACT_REG if f["estado"] == "Vencido"]
    atr = [e for e in ENT if e.get("Estado de la entrega") == "Retrasado"]
    return {"skus": len(INV), "unidades": sum(int(i.get("Cantidad", 0) or 0) for i in INV), "bajos": len(bajos),
            "vencido_mxn": sum(f["total"] for f in ven), "facturas_vencidas": len(ven),
            "pendiente_mxn": sum(f["total"] for f in FACT_REG if f["estado"] == "Pendiente"),
            "entregas_retrasadas": len(atr), "prom_atraso": 0,
            "quejas_abiertas": sum(q["estado"] == "Abierta" for q in QUEJAS)}

@app.get("/api/resumen")
def api_resumen(x_token: str = Header(None)):
    s = quien(x_token)
    return {**resumen(), "inventario": INV, "entregas": ENT, "facturas": FACT_REG[-40:], "quejas": QUEJAS, "perm": sorted(s["perm"])}

STOP = set("cuanto cuantos cuanta que de del la el los las nos queda quedan hay tengo tenemos en un una por para me dime dame cual es son y a al con stock existencia existencias litro litros".split())
SENSIBLE = ["nomina", "sueldo", "salario", "contrasena", "password", "tarjeta", "clave de"]

def buscar_producto(q):
    toks = [t for t in re.findall(r"[a-z0-9]+", norm(q)) if t not in STOP and len(t) > 2]
    mejor, pts = None, 0
    for i in INV:
        txt = norm(i.get("Producto", "") + " " + i.get("SKU", ""))
        p = sum(1 for t in toks if t.rstrip("s") in txt)
        if p > pts: mejor, pts = i, p
    return mejor

def local(q, s):
    n, p = norm(q), s["perm"]
    m = re.search(r"\b([a-z]{3}-\d{3})\b", n)
    
    if any(w in n for w in SENSIBLE):
        return "No puedo ayudar con nóminas, contraseñas ni datos personales. Esa información está protegida.", "Política de privacidad", False

    if any(w in n for w in ["existencia", "stock", "inventario", "producto"]) and any(w in n for w in ["menos", "minimo", "minima", "bajo"]):
        if INV:
            b = sorted(INV, key=lambda i: int(i.get("Cantidad", 0) or 0))
            it = b[0]
            return f"El producto con menos existencias es {it.get('Producto', '')} ({it.get('SKU', '')}) con {it.get('Cantidad', 0)} unidades. {it.get('Descripción', '')}.", "inventario.md", False
        return "No hay datos de inventario cargados.", "inventario.md", False

    temas = {"fac": ["factura", "proveedor", "pagar", "vencid", "duplic"], "ent": ["entrega", "pedido", "retras"], "que": ["queja", "reclamo"]}
    for perm, ws in temas.items():
        if perm not in p and any(w in n for w in ws):
            return f"Tu rol ({s['rol']}) no tiene acceso a esa información. Pídesela a la persona responsable.", "Permisos por rol", False
    
    if "inv" in p and (m or any(w in n for w in ["queda", "existencia", "stock", "inventario", "hay", "unidades", "bajo", "reabast", "poco"])):
        it = next((i for i in INV if m and i.get("SKU", "").lower() == m.group(1)), None) or buscar_producto(q)
        if it:
            return f"{it.get('Producto', '')} ({it.get('SKU', '')}): {it.get('Cantidad', 0)} unidades. {it.get('Descripción', '')}.", "inventario.md", False

    return None

def foundry(q, s):
    ep = os.getenv("AZURE_AI_PROJECT_ENDPOINT") or os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    ag = os.getenv("AZURE_AI_AGENT_NAME") or os.getenv("FOUNDRY_AGENT_NAME")
    if not (ep and ag and IA_ACTIVA): return None
    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
        oc = AIProjectClient(endpoint=ep, credential=DefaultAzureCredential()).get_openai_client()
        ctx = f"[CONTEXTO] rol={s['rol']}; permisos={','.join(sorted(s['perm']))}\n"
        r = oc.responses.create(input=ctx + q, extra_body={"agent": {"name": ag, "type": "agent_reference"}})
        return r.output_text
    except Exception:
        return None

@app.post("/api/chat")
def chat(d: Pregunta, x_token: str = Header(None)):
    s = quien(x_token); t0 = time.time()
    r = local(d.texto, s)
    if r: 
        txt, fuente, rev, origen = r[0], r[1], r[2], "datos locales"
    else:
        f = foundry(d.texto, s)
        if f: 
            txt, fuente, rev, origen = f, "Agente Microsoft Foundry", False, "foundry"
        else: 
            txt, fuente, rev, origen = ("No tengo información suficiente para responder con certeza. Reformula o consulta a una persona del equipo.", "—", False, "sin respuesta")
    
    return {"respuesta": txt, "fuente": fuente, "origen": origen, "revision_humana": rev, "ms": int((time.time() - t0) * 1000),
            "aviso": "Respuesta generada por IA. No sustituye la revisión de una persona."}

