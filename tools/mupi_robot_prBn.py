import time
from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.go2.sport.sport_client import SportClient

# Inicializar la conexión de red con tu interfaz
ChannelFactoryInitialize(0, "eno1")

sport = SportClient()
sport.SetTimeout(10.0)
sport.Init()

try:
    print("Amortiguando...")
    sport.Damp()
    time.sleep(1.5)
    
    print("Levantando...")
    sport.StandUp()
    time.sleep(3.0)
    
    print("Manteniendo equilibrio...")
    sport.BalanceStand()
    
    print("\nRobot de pie. Ejecutando tu asistente (pulsa Ctrl+C para salir y tumbarlo)...")
    
    # Aquí puedes meter el bucle de escucha y procesamiento de voz de Mupi
    while True:
        time.sleep(1) # Mantiene el programa vivo mientras el robot está de pie

except KeyboardInterrupt:
    print("\nCtrl+C detectado. Saliendo...")

finally:
    # Esta parte se ejecuta siempre, garantizando que el robot se tumbe al salir
    print("Tumbando y amortiguando el robot...")
    sport.StopMove()
    time.sleep(0.5)
    sport.StandDown()
    time.sleep(2.0)
    sport.Damp()
    print("Robot amortiguado y seguro.")
