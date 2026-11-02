"""Río Claro · Cauce — Centro de atención con IA (piloto AI-901)."""
import os, re, secrets, time, unicodedata
from pathlib import Path
from fastapi import FastAPI, Form, Header, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).parent
app = FastAPI(title="Río Claro Cauce")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

def norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def tabla(nombre):
    filas = [l for l in (BASE / "data" / nombre).read_text(encoding="utf-8").splitlines() if l.startswith("|")]
    cab = [c.strip() for c in filas[0].strip("|").split("|")]
    return [dict(zip(cab, [c.strip() for c in f.strip("|").split("|")])) for f in filas[2:]]

INV, FAC, ENT = tabla("inventario.md"), tabla("facturas.md"), tabla("entregas.md")
mxn = lambda t: float(t.replace("$", "").replace(",", ""))
# Quejas: registro vivo (se alimenta desde la app; semilla ficticia)
QUEJAS = [{"folio": "Q-1001", "cliente": "Colegio del Pacífico, A.C.", "sucursal": "Querétaro",
           "tema": "Entrega con 23 días de atraso", "estado": "Abierta", "min": 0}]
FACT_REG = [{"id": i + 1, "proveedor": f["Proveedor (razón social)"], "fecha": f["Fecha de emisión"],
             "total": mxn(f["Total (MXN)"]), "estado": f["Estado"], "origen": "histórico"} for i, f in enumerate(FAC)]

USUARIOS = {  # accesos por persona (nada de contraseñas compartidas)
    "ernesto": ("Don Ernesto", "Dirección", {"inv", "fac", "ent", "que"}),
    "laura": ("Laura", "Cuentas por pagar", {"inv", "fac"}),
    "mariana": ("Mariana", "Gerente León", {"inv", "ent", "que"}),
    "toño": ("Toño", "Jefe de bodega", {"inv"}),
    "carlos": ("Carlos", "Soporte y sistemas", {"inv", "fac", "ent", "que"}),
}
CLAVE_DEMO = "Cauce2026!"
SESIONES = {}
IA_ACTIVA = True  # Carlos puede apagar la IA generativa (kill switch)

def quien(token):
    s = SESIONES.get(token or "")
    if not s: raise HTTPException(401, "Inicia sesión")
    return s

class Login(BaseModel): usuario: str; clave: str
class Pregunta(BaseModel): texto: str

@app.get("/")
def home(): return FileResponse(BASE / "static" / "index.html")

@app.get("/health")
def health(): return {"status": "ok", "foundry": bool(os.getenv("FOUNDRY_PROJECT_ENDPOINT")), "ia_activa": IA_ACTIVA}

@app.post("/api/login")
def login(d: Login):
    u = USUARIOS.get(norm(d.usuario).replace("n~", "ñ")) or USUARIOS.get(d.usuario.lower())
    if not u or d.clave != CLAVE_DEMO: raise HTTPException(401, "Usuario o clave incorrectos")
    t = secrets.token_hex(16)
    SESIONES[t] = {"user": d.usuario.lower(), "nombre": u[0], "rol": u[1], "perm": u[2]}
    return {"token": t, "nombre": u[0], "rol": u[1], "perm": sorted(u[2])}

def resumen():
    bajos = [i for i in INV if int(i["Cantidad"]) < 60]
    ven = [f for f in FACT_REG if f["estado"] == "Vencido"]
    atr = [e for e in ENT if e["Estado de la entrega"] == "Retrasado"]
    return {"skus": len(INV), "unidades": sum(int(i["Cantidad"]) for i in INV), "bajos": len(bajos),
            "vencido_mxn": sum(f["total"] for f in ven), "facturas_vencidas": len(ven),
            "pendiente_mxn": sum(f["total"] for f in FACT_REG if f["estado"] == "Pendiente"),
            "entregas_retrasadas": len(atr), "prom_atraso": round(sum(int(e["Días de atraso"]) for e in atr) / max(len(atr), 1), 1),
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
        txt = norm(i["Producto"] + " " + i["SKU"])
        p = sum(1 for t in toks if t.rstrip("s") in txt)
        if p > pts: mejor, pts = i, p
    return mejor

def local(q, s):
    n, p = norm(q), s["perm"]
    m = re.search(r"\b([a-z]{3}-\d{3})\b", n)
    if any(w in n for w in SENSIBLE):
        return "No puedo ayudar con nóminas, contraseñas ni datos personales. Esa información está protegida.", "Política de privacidad", False
    temas = {"fac": ["factura", "proveedor", "pagar", "vencid", "duplic"], "ent": ["entrega", "pedido", "retras"], "que": ["queja", "reclamo"]}
    for perm, ws in temas.items():
        if perm not in p and any(w in n for w in ws):
            return f"Tu rol ({s['rol']}) no tiene acceso a esa información. Pídesela a la persona responsable.", "Permisos por rol", False
    if "inv" in p and (m or any(w in n for w in ["queda", "existencia", "stock", "inventario", "hay", "unidades", "bajo", "reabast", "poco"])):
        if any(w in n for w in ["bajo", "reabast", "poco", "agotar"]):
            b = sorted((i for i in INV if int(i["Cantidad"]) < 60), key=lambda i: int(i["Cantidad"]))
            return "Productos con existencia baja (<60): " + "; ".join(f"{i['Producto']} ({i['SKU']}): {i['Cantidad']}" for i in b) + ".", "inventario.md", False
        it = next((i for i in INV if m and i["SKU"].lower() == m.group(1)), None) or buscar_producto(q)
        if it:
            return f"{it['Producto']} ({it['SKU']}): {it['Cantidad']} unidades. {it['Descripción']} Actualizado el {it['Última actualización']}.", "inventario.md", False
    if "ent" in p and any(w in n for w in ["entrega", "pedido", "atras", "retras", "hotel", "colegio", "restaurante", "escuela"]):
        cli = [e for e in ENT if all(t in norm(e["Cliente (nombre o razón social)"]) for t in re.findall(r"[a-z]{4,}", n) if t not in STOP | {"entrega", "pedido", "pedidos", "entregas", "atraso", "retrasado", "retrasados"})]
        base = cli or [e for e in ENT if e["Estado de la entrega"] == "Retrasado"]
        tit = "Entregas del cliente consultado" if cli else "Entregas retrasadas"
        return f"{tit} ({len(base)}): " + "; ".join(f"{e['Cliente (nombre o razón social)']} — {e['Estado de la entrega']}, entrega {e['Día de entrega']}, atraso {e['Días de atraso']} d" for e in base[:8]) + ". Mariana debe dar seguimiento en menos de 15 min.", "entregas.md", False
    if "fac" in p and any(w in n for w in ["factura", "proveedor", "pagar", "vencid", "pendiente", "pago", "duplic"]):
        r = resumen()
        return (f"Facturas vencidas: {r['facturas_vencidas']} por ${r['vencido_mxn']:,.2f} MXN. Pendientes de pago: ${r['pendiente_mxn']:,.2f} MXN. "
                "Ningún pago se autoriza desde aquí: cada factura la revisa y aprueba una persona de cuentas por pagar."), "facturas.md", True
    if "que" in p and any(w in n for w in ["queja", "reclamo"]):
        return f"Hay {sum(q['estado']=='Abierta' for q in QUEJAS)} queja(s) abiertas: " + "; ".join(f"{q['folio']} {q['cliente']} ({q['tema']})" for q in QUEJAS if q["estado"] == "Abierta"), "quejas (registro vivo)", False
    return None

def foundry(q, s):
    ep, ag = os.getenv("FOUNDRY_PROJECT_ENDPOINT"), os.getenv("FOUNDRY_AGENT_NAME")
    if not (ep and ag and IA_ACTIVA): return None
    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
        oc = AIProjectClient(endpoint=ep, credential=DefaultAzureCredential()).get_openai_client()
        ctx = f"[CONTEXTO] rol={s['rol']}; permisos={','.join(sorted(s['perm']))}\n"
        r = oc.responses.create(input=ctx + q, extra_body={"agent": {"name": ag, "type": "agent_reference"}})
        return r.output_text
    except Exception as e:
        return None

@app.post("/api/chat")
def chat(d: Pregunta, x_token: str = Header(None)):
    s = quien(x_token); t0 = time.time()
    r = local(d.texto, s)
    if r: txt, fuente, rev, origen = r[0], r[1], r[2], "datos locales"
    else:
        f = foundry(d.texto, s)
        if f: txt, fuente, rev, origen = f, "Agente Microsoft Foundry", False, "foundry"
        else: txt, fuente, rev, origen = ("No tengo información suficiente para responder con certeza. Reformula o consulta a una persona del equipo.", "—", False, "sin respuesta")
    return {"respuesta": txt, "fuente": fuente, "origen": origen, "revision_humana": rev, "ms": int((time.time() - t0) * 1000),
            "aviso": "Respuesta generada por IA. No sustituye la revisión de una persona."}

@app.post("/api/factura")
async def factura(proveedor: str = Form(...), fecha: str = Form(...), total: float = Form(...), archivo: UploadFile = File(...), x_token: str = Header(None)):
    s = quien(x_token)
    if "fac" not in s["perm"]: raise HTTPException(403, "Tu rol no gestiona facturas")
    datos = await archivo.read()
    conf = 0.55 if (len(datos) < 3000 or "borros" in norm(archivo.filename)) else 0.96  # lectura simulada
    dup = next((f for f in FACT_REG if norm(f["proveedor"]) == norm(proveedor) and f["fecha"] == fecha and abs(f["total"] - total) < 0.01), None)
    if dup: estado, msg = "Bloqueada", f"Posible duplicado de la factura #{dup['id']} (mismo proveedor, fecha y total). No se registra."
    elif conf < 0.8: estado, msg = "Revisión humana", f"Lectura dudosa (confianza {conf:.0%}). La IA no inventa el total: pasa a revisión de Laura."
    else: estado, msg = "Lista para aprobar", f"Lectura confiable ({conf:.0%}). Queda en espera de aprobación de una persona."
    reg = {"id": len(FACT_REG) + 1, "proveedor": proveedor, "fecha": fecha, "total": total, "estado": estado, "origen": archivo.filename}
    if estado != "Bloqueada": FACT_REG.append(reg)
    return {"estado": estado, "mensaje": msg, "confianza": conf, "factura": reg}

@app.post("/api/factura/{fid}/aprobar")
def aprobar(fid: int, x_token: str = Header(None)):
    s = quien(x_token)
    if s["user"] not in ("laura", "carlos"): raise HTTPException(403, "Solo cuentas por pagar aprueba")
    f = next((f for f in FACT_REG if f["id"] == fid), None)
    if not f: raise HTTPException(404, "No existe")
    f["estado"] = "Aprobada por " + s["nombre"]
    return f

class Queja(BaseModel): cliente: str; sucursal: str; tema: str

@app.post("/api/queja")
def queja(q: Queja, x_token: str = Header(None)):
    quien(x_token)
    r = {"folio": f"Q-{1001 + len(QUEJAS)}", **q.model_dump(), "estado": "Abierta", "min": 0}
    QUEJAS.append(r)
    return r

@app.post("/api/admin/ia/{estado}")
def apagar(estado: str, x_token: str = Header(None)):
    global IA_ACTIVA
    if quien(x_token)["user"] != "carlos": raise HTTPException(403, "Solo soporte")
    IA_ACTIVA = estado == "on"
    return {"ia_activa": IA_ACTIVA}
