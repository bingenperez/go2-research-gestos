import os
from google import genai

client = genai.Client()

print("==================================================")
print("  🤖 Gemini Terminal Interactivo para Directorios")
print("==================================================")

# Leemos automáticamente la estructura de la carpeta actual para dársela de contexto
directorio_actual = os.getcwd()
archivos = []
for root, dirs, files in os.walk(directorio_actual):
    # Ignoramos carpetas ocultas como .git
    dirs[:] = [d for d in dirs if not d.startswith('.')]
    for file in files:
        archivos.append(os.path.relpath(os.path.join(root, file), directorio_actual))

print(f"📁 Directorio analizado: {directorio_actual}")
print(f"📄 Archivos encontrados: {len(archivos)} archivos cargados en el contexto.\n")

# Historial de conversación básico
chat = client.chats.create(model="gemini-2.5-flash")

# Contexto inicial automático sobre la estructura
contexto_inicial = f"Estoy trabajando en este directorio: {directorio_actual}. La lista de archivos del proyecto es: {archivos}."
chat.send_message(contexto_inicial)

while True:
    try:
        pregunta = input("\nTú > ")
        if pregunta.lower() in ["salir", "exit", "quit"]:
            break
        if not pregunta.strip():
            continue
            
        respuesta = chat.send_message(pregunta)
        print(f"\nGemini:\n{respuesta.text}")
    except KeyboardInterrupt:
        break

print("\n¡Hasta luego!")
