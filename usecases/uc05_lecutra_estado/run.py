#!/usr/bin/env python3
"""
Script de lectura continua del estado del robot (LowState).
Muestra inclinación, altura estimada y datos del giroscopio en tiempo real.
Ubicación: usecases/uc05_lecutra_estado/run.py
"""

import sys
import time
from pathlib import Path

# Subimos 2 niveles a la raíz del repositorio (go2-research-gestos)
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from go2core.control import contract as ct
from go2core.control.lowlevel import LowLevel

DEFAULT_CONTRACT = REPO_ROOT / "usecases/uc01_locomotion/configs/robot_go2.yaml"

def main():
    mode = "sim"  # Modo simulación (MuJoCo)
    
    print("[1] Cargando contrato de configuración...")
    c = ct.load_contract(str(DEFAULT_CONTRACT))

    print("[2] Conectando con el robot a través de LowLevel (DDS)...")
    robot = LowLevel(contract=c, mode=mode)

    try:
        # robot.start() se conecta a 'rt/lowstate' y espera mensajes
        robot.start(wait_state_s=5.0)
        print("\n--- ¡CONEXIÓN ESTABLECIDA CON ÉXITO! ---")
        print("Leyendo telemetría del cuadrúpedo en tiempo real (Presiona Ctrl+C para salir):\n")

        while True:
            # Lectura de datos en tiempo real desde LowLevel
            inclinacion_deg = round(robot.tilt_rad() * (180.0 / 3.14159), 2)
            altura_m = round(robot.height_proxy(), 3)
            giroscopio = robot.gyro()  # [vx, vy, vz]
            
            # Imprimimos los datos en pantalla actualizando la línea
            print(
                f"\r[Estado Robot] "
                f"Inclinación: {inclinacion_deg}° | "
                f"Altura estimada: {altura_m} m | "
                f"Giroscopio (rad/s): X={giroscopio[0]:+.2f}, Y={giroscopio[1]:+.2f}, Z={giroscopio[2]:+.2f}",
                end="",
                flush=True
            )
            time.sleep(0.1)  # Refresco a 10 Hz

    except KeyboardInterrupt:
        print("\n\nLectura detenida por el usuario.")
    except Exception as e:
        print(f"\nError al conectar con el robot: {e}")
    finally:
        robot.stop()
        print("Conexión DDS cerrada correctamente.")

if __name__ == "__main__":
    main()
