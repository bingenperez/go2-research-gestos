import math

PARAMS = {}

def decidir(tel, t):
    """Avanza un poco y luego gira sobre sí mismo."""
    
    # Imprimimos la inclinación para seguir monitorizándola
    incl = math.degrees(tel.inclinacion()) if tel else 0.0
    print(f"t={t:.1f}s | Inclinación: {incl:.1f}°")

    # 1. Durante los primeros 3 segundos: avanza recto
    if t < 3.0:
        return 0.2, 0.0, 0.0  # vx=0.2 (avanza), vy=0.0, omega=0.0
    
    # 2. Del segundo 3 al 6: gira hacia un lado
    elif t < 6.0:
        return 0.0, 0.0, 0.5  # vx=0.0, vy=0.0, omega=0.5 (gira)
    
    # 3. A partir del segundo 6: se para
    return 0.0, 0.0, 0.0