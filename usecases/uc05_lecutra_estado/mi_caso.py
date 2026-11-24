import math

PARAMS = {}

def decidir(tel, t):
    """Recorrido ultra-seguro y estable para evitar cualquier vuelco."""
    
    # Imprimimos la inclinación para monitorizar
    incl = math.degrees(tel.inclinacion()) if tel else 0.0
    print(f"t={t:.1f}s | Inclinación: {incl:.1f}°")

    # 1. Estabilización inicial en pie (0s - 3s)
    if t < 3.0:
        return 0.0, 0.0, 0.0
        
    # 2. Avance recto suave y moderado (3s - 12s)
    elif t < 12.0:
        return 0.2, 0.0, 0.0
        
    # 3. Giro muy suave sobre su propio eje (12s - 20s)
    elif t < 20.0:
        return 0.0, 0.0, 0.8
        
    # 4. Frenado progresivo y lento (20s - 25s)
    elif t < 25.0:
        return 0.05, 0.0, 0.0
        
    # 5. Parada total de seguridad (> 25s)
    else:
        return 0.0, 0.0, 0.0
