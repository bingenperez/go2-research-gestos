#!/usr/bin/env python3
"""usecases/uc09_gesture_control/perception/pose.py

Detección de gestos con MediaPipe PoseLandmarker.

    imagen ──► PoseLandmarker (hasta N personas) ──► la más grande ──► gesto

Devuelve un gesto EN BRUTO por fotograma: "mano", "cruzados" o "ninguno". No
decide nada: la confirmación (mantener ~1 s), el cooldown y la acción están en
deploy/gesture_control.py.

POR QUÉ PoseLandmarker Y NO mp.solutions.pose
---------------------------------------------
La API clásica solo detecta UNA persona, y no deja elegir cuál. La API Tasks
(PoseLandmarker) acepta num_poses > 1: se queda con la pose cuya caja de
landmarks visibles es mayor, que es la persona más cercana. Mismo criterio
que uc04 con YOLO. La contrapartida: necesita el fichero del modelo
(pose_landmarker_lite.task), que no se descarga solo. Ver el README.

LOS GESTOS
----------
Coordenadas normalizadas de MediaPipe: x a la derecha, y HACIA ABAJO.

    mano       una muñeca por encima de la nariz (o de los hombros, según
               `referencia_mano`). Si la referencia no se ve: ninguno.
    cruzados   las dos muñecas visibles, cada una al otro lado de la línea
               media de los hombros, y las dos a la altura del pecho.

Si se cumplen los dos a la vez, el resultado es "ninguno": ante la duda, no se
hace nada.

LA CÁMARA DEL ROBOT ESTÁ BAJA
-----------------------------
A 30 cm del suelo, a 2-3 m ve piernas y torso. Para ver la cabeza y una mano
levantada hace falta más distancia. Compruébalo con --dry-run --show antes de
nada.

Uso como programa, para probar con la webcam sin robot:

    python3 usecases/uc09_gesture_control/perception/pose.py --show
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

NINGUNO, MANO, CRUZADOS = "ninguno", "mano", "cruzados"

# Índices de los landmarks de MediaPipe Pose que se usan.
NARIZ = 0
HOMBRO_I, HOMBRO_D = 11, 12
CODO_I, CODO_D = 13, 14
MUNECA_I, MUNECA_D = 15, 16
CADERA_I, CADERA_D = 23, 24

# Esqueleto para dibujar. Se define aquí para no depender de mp.solutions,
# que algunas versiones recientes de mediapipe ya no traen.
CONEXIONES = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),   # brazos y hombros
    (11, 23), (12, 24), (23, 24),                       # tronco
    (23, 25), (25, 27), (24, 26), (26, 28),             # piernas
    (0, 11), (0, 12),                                   # cabeza a hombros
]


@dataclass
class Pose:
    """La persona elegida en un fotograma."""

    visible: bool
    gesto: str = NINGUNO
    puntos: np.ndarray | None = None    # (33, 3): x, y, visibilidad
    caja: tuple[int, int, int, int] | None = None
    n_personas: int = 0


# ===========================================================================
# Clasificación. Funciones puras sobre un array (33, 3) para poder probarlas
# sin cámara ni MediaPipe.
# ===========================================================================
def _ve(p: np.ndarray, i: int, vis_min: float) -> bool:
    return bool(p[i, 2] >= vis_min)


def mano_levantada(p: np.ndarray, cfg: dict) -> bool:
    vis = float(cfg["vis_min"])
    margen = float(cfg["margen_cabeza"])
    if cfg.get("referencia_mano", "nariz") == "hombros":
        if not (_ve(p, HOMBRO_I, vis) and _ve(p, HOMBRO_D, vis)):
            return False
        ref = min(p[HOMBRO_I, 1], p[HOMBRO_D, 1])
    else:
        if not _ve(p, NARIZ, vis):
            return False          # sin cabeza no se puede decir "por encima"
        ref = p[NARIZ, 1]
    for m in (MUNECA_I, MUNECA_D):
        # y crece hacia abajo: "por encima" es una y MENOR
        if _ve(p, m, vis) and p[m, 1] < ref - margen:
            return True
    return False


def brazos_cruzados(p: np.ndarray, cfg: dict) -> bool:
    vis = float(cfg["vis_min"])
    if not all(_ve(p, i, vis) for i in (HOMBRO_I, HOMBRO_D, MUNECA_I, MUNECA_D)):
        return False

    hi, hd = p[HOMBRO_I], p[HOMBRO_D]
    medio = (hi[0] + hd[0]) / 2.0
    ancho = abs(hi[0] - hd[0])
    if ancho < 1e-3:
        return False              # de perfil: no se puede saber si cruza

    # Cada muñeca al otro lado de la línea media, y pasada un mínimo para que
    # unas manos juntas en el centro no cuenten como cruzadas.
    cruce = float(cfg["cruce_min"]) * ancho
    mi, md = p[MUNECA_I], p[MUNECA_D]
    lado_hi = np.sign(hi[0] - medio)
    lado_hd = np.sign(hd[0] - medio)
    if not ((mi[0] - medio) * lado_hd > cruce and (md[0] - medio) * lado_hi > cruce):
        return False

    # Banda del pecho: entre los hombros y las caderas. La webcam del portátil
    # a menudo no ve las caderas; entonces el límite inferior se estima con la
    # anchura de hombros.
    margen = float(cfg["margen_pecho"])
    arriba = min(hi[1], hd[1]) - margen
    if _ve(p, CADERA_I, vis) and _ve(p, CADERA_D, vis):
        abajo = (p[CADERA_I, 1] + p[CADERA_D, 1]) / 2.0 + margen
    else:
        abajo = max(hi[1], hd[1]) + 1.5 * ancho
    return all(arriba <= m[1] <= abajo for m in (mi, md))


def clasificar(p: np.ndarray, cfg: dict) -> str:
    m, c = mano_levantada(p, cfg), brazos_cruzados(p, cfg)
    if m and c:
        return NINGUNO            # ambiguo: no se hace nada
    return MANO if m else CRUZADOS if c else NINGUNO


def area_visible(p: np.ndarray, vis_min: float) -> float:
    v = p[p[:, 2] >= vis_min]
    if len(v) < 4:
        return 0.0
    return float((v[:, 0].max() - v[:, 0].min()) * (v[:, 1].max() - v[:, 1].min()))


# ===========================================================================
class DetectorGestos:
    """Cámara + PoseLandmarker + elección de la persona más grande."""

    def __init__(self, cfg: dict, camara: int = 0, fuente: str = "webcam",
                 iface: str = "enp3s0", domain: int = 0,
                 init_dds: bool = True) -> None:
        self.cfg = cfg
        self.vis_min = float(cfg["gestos"]["vis_min"])
        self.cfg_gestos = cfg["gestos"]
        self.ancho_salida = int(cfg.get("ancho", 640))

        modelo = Path(cfg["modelo_pose"])
        if not modelo.is_absolute():
            modelo = Path(__file__).resolve().parents[3] / modelo
        if not modelo.exists():
            raise RuntimeError(
                f"No existe el modelo de pose: {modelo}\n"
                "Descargalo (ver usecases/uc09_gesture_control/README.md,\n"
                "'Dependencia: MediaPipe') o cambia `modelo_pose` en gestures.yaml.")

        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
        except ImportError as e:
            raise RuntimeError(
                "Falta mediapipe en esta caja. Ver el README de uc09, "
                "'Dependencia: MediaPipe'.") from e

        self._mp = mp
        opciones = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(modelo)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=int(cfg.get("num_poses", 3)),
            min_pose_detection_confidence=float(cfg.get("confianza_min", 0.5)),
            min_pose_presence_confidence=float(cfg.get("confianza_min", 0.5)),
            min_tracking_confidence=float(cfg.get("confianza_min", 0.5)),
        )
        self.pose = vision.PoseLandmarker.create_from_options(opciones)
        self._t0 = time.monotonic()
        self._ts_prev = -1

        # Dos fuentes, igual que uc04. La del robot llega por la red con
        # VideoClient; timeout corto para no quedarse 3 s bloqueado si se corta.
        self.fuente = fuente
        if fuente == "robot":
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
            from robot_camera import CamaraRobot
            self.cam = CamaraRobot(iface, domain, timeout=1.0, init_dds=init_dds)
            self.cap = None
        else:
            self.cam = None
            self.cap = cv2.VideoCapture(camara)
            if not self.cap.isOpened():
                raise RuntimeError(
                    f"No se puede abrir /dev/video{camara}.\n"
                    "En Linux hay que pasar la camara al contenedor: usa ./go2 en\n"
                    "lugar de `docker compose`, que es quien carga el override.")
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.fps = 0.0
        self._t_prev = time.perf_counter()
        self._firma_prev: int | None = None

    # ------------------------------------------------------------------
    def leer_imagen(self) -> np.ndarray | None:
        """Un fotograma NUEVO, o None si falla o si es igual al anterior.

        Un driver colgado puede devolver el mismo buffer una y otra vez. Con
        ruido de sensor real dos fotogramas nunca son idénticos, así que uno
        repetido se trata como si no hubiera llegado: así el watchdog lo ve.
        """
        if self.cam is not None:
            imagen = self.cam.leer()
        else:
            ok, imagen = self.cap.read()
            imagen = imagen if ok else None
        if imagen is None:
            return None

        firma = hash(imagen[::16, ::16].tobytes())
        if firma == self._firma_prev:
            return None
        self._firma_prev = firma

        if self.ancho_salida and imagen.shape[1] > self.ancho_salida:
            e = self.ancho_salida / imagen.shape[1]
            imagen = cv2.resize(imagen, None, fx=e, fy=e, interpolation=cv2.INTER_AREA)
        return imagen

    def detectar(self, imagen: np.ndarray) -> Pose:
        rgb = cv2.cvtColor(imagen, cv2.COLOR_BGR2RGB)
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        # El modo VIDEO exige marcas de tiempo estrictamente crecientes.
        ts = int((time.monotonic() - self._t0) * 1000)
        ts = max(ts, self._ts_prev + 1)
        self._ts_prev = ts
        res = self.pose.detect_for_video(mp_img, ts)

        ahora = time.perf_counter()
        dt = ahora - self._t_prev
        self._t_prev = ahora
        if dt > 0:
            self.fps = 0.8 * self.fps + 0.2 * (1.0 / dt)

        mejor, mejor_area = None, 0.0
        for persona in res.pose_landmarks or []:
            p = np.array([[l.x, l.y, (l.visibility or 0.0)] for l in persona],
                         dtype=np.float32)
            a = area_visible(p, self.vis_min)
            if a > mejor_area:
                mejor, mejor_area = p, a

        n = len(res.pose_landmarks or [])
        if mejor is None:
            return Pose(visible=False, n_personas=n)

        h, w = imagen.shape[:2]
        v = mejor[mejor[:, 2] >= self.vis_min]
        caja = (int(v[:, 0].min() * w), int(v[:, 1].min() * h),
                int(v[:, 0].max() * w), int(v[:, 1].max() * h))
        return Pose(visible=True, gesto=clasificar(mejor, self.cfg_gestos),
                    puntos=mejor, caja=caja, n_personas=n)

    # ------------------------------------------------------------------
    def dibujar(self, imagen: np.ndarray, pose: Pose, lineas: list[str]) -> np.ndarray:
        """Esqueleto de la persona elegida y líneas de texto de estado."""
        h, w = imagen.shape[:2]
        vis = imagen.copy()
        if pose.visible and pose.puntos is not None:
            p = pose.puntos
            color = {MANO: (0, 200, 0), CRUZADOS: (0, 140, 255)}.get(pose.gesto, (200, 200, 200))
            for a, b in CONEXIONES:
                if p[a, 2] >= self.vis_min and p[b, 2] >= self.vis_min:
                    cv2.line(vis, (int(p[a, 0] * w), int(p[a, 1] * h)),
                             (int(p[b, 0] * w), int(p[b, 1] * h)), color, 2)
            for i in range(len(p)):
                if p[i, 2] >= self.vis_min:
                    cv2.circle(vis, (int(p[i, 0] * w), int(p[i, 1] * h)), 3, color, -1)
            if pose.caja:
                x1, y1, x2, y2 = pose.caja
                cv2.rectangle(vis, (x1, y1), (x2, y2), color, 1)

        for k, texto in enumerate(lineas):
            cv2.putText(vis, texto, (10, 24 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 0, 0), 3)
            cv2.putText(vis, texto, (10, 24 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 230, 230), 1)
        cv2.putText(vis, f"{self.fps:4.1f} fps", (w - 95, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
        return vis

    def cerrar(self) -> None:
        if self.cap is not None:
            self.cap.release()
        try:
            self.pose.close()
        except Exception:
            pass


# ===========================================================================
if __name__ == "__main__":
    import argparse

    import yaml

    p = argparse.ArgumentParser(description="Prueba de gestos con la webcam, sin robot")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--config", default=str(Path(__file__).resolve().parents[1]
                                           / "configs" / "gestures.yaml"))
    p.add_argument("--show", action="store_true")
    p.add_argument("--seconds", type=float, default=30.0)
    args = p.parse_args()

    det = DetectorGestos(yaml.safe_load(Path(args.config).read_text()), camara=args.camera)
    print("detector listo. Ctrl-C para salir.\n")
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < args.seconds:
            img = det.leer_imagen()
            if img is None:
                time.sleep(0.01)
                continue
            pose = det.detectar(img)
            if args.show:
                cv2.imshow("uc09 - gestos", det.dibujar(img, pose, [f"gesto: {pose.gesto}"]))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                print(f"\r  personas {pose.n_personas}  gesto {pose.gesto:<9} "
                      f"{det.fps:4.1f} fps", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        det.cerrar()
        cv2.destroyAllWindows()
        print("\nfin")
