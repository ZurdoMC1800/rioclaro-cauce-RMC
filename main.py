def foundry(q, s):
    # Corregido: Usamos los nombres de variables que configuramos en Azure
    ep = os.getenv("AZURE_AI_PROJECT_ENDPOINT") or os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    ag = os.getenv("AZURE_AI_AGENT_NAME") or os.getenv("FOUNDRY_AGENT_NAME")
    
    if not (ep and ag and IA_ACTIVA): return None
    
    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
        
        oc = AIProjectClient(endpoint=ep, credential=DefaultAzureCredential()).get_openai_client()
        ctx = f"[CONTEXTO] rol={s['rol']}; permisos={','.join(sorted(s['perm']))}\n"
        
        # Eliminamos el tool_choice que causaba el fallo con gpt-5-mini
        r = oc.responses.create(
            input=ctx + q, 
            extra_body={"agent": {"name": ag, "type": "agent_reference"}}
        )
        return r.output_text
    except Exception as e:
        print(f"Error en Foundry: {e}")
        return None
