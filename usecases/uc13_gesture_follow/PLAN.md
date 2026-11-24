# uc13 — Plan de mejoras (gestos + seguimiento)

Trabajo por fases: al final de cada una se pasan los tests
(`tests/test_uc13.py`), se resume qué ha cambiado y qué probar en el robot, y
no se pasa a la siguiente sin visto bueno. En todas: se mantienen la lista
blanca de órdenes, el watchdog y el cierre seguro; los parámetros nuevos van en
`configs/gesture_follow.yaml`.

## Objetivo

- Saludar con la mano (gesto natural) para que el perro haga Hello, además de
  la T.
- Seguimiento fluido y rápido, como con el mando: acompañar a una persona
  andando a ritmo normal sin quedarse atrás ni ir a tirones.
- Sin seguir y quieto de pie: cabeza inclinada siempre, también después de
  Hello.
- Siguiendo, la persona se para: el perro se para e inclina la cabeza. La
  persona vuelve a andar: endereza rápido y sigue sin retraso apreciable.

## Fase 1 — Saludo con la mano · CERRADA (probada en el robot real)

- Gesto dinámico `saludo` (`perception/pose.py`, `DetectorSaludo`): una muñeca
  alzada (por encima del codo, o por encima de la nariz como en "una mano") y
  la otra no, oscilando en horizontal respecto a su hombro. Cualquier mano.
- DE_PIE → Hello (igual que la T, que no cambia); TUMBADO → levantarse;
  SIGUIENDO/PAUSADO → nada. Prioridad sobre "una mano".
- Se confirma con su propia ventana (sin 15 fotogramas). Cooldown y re-armado:
  no vuelve a saltar hasta bajar el brazo.
- `gestos.saludo.activo` para apagarlo. En QUIETO, MediaPipe en todos los
  fotogramas.
- `metrics.csv` registra `saludo_cambios`, `saludo_amplitud`, `saludo_lado`,
  `saludo_x_i`, `saludo_x_d`; `eval/measure.py` las resume. En un fotograma
  suelto sin brazo se registran los cambios que lleva la historia (no 0).

Resultado en el robot real (2026-11-24):

- Parámetros finales: `ventana_s: 3.0`, `cambios_min: 3` (`amplitud_min: 0.3`,
  `histeresis: 0.10`, `hueco_max_s: 0.3`).
- El saludo se confirma en ~1.5-3 s.
- Sin falsos positivos con la mano quieta levantada.

Historia del ajuste (dry-run 2026-11-24): el saludo perdía el brazo en el
extremo del vaivén (muñeca no por encima del codo) y se borraba la historia →
ahora vale también "por encima de la nariz". Se saluda despacio (~0.5-0.6 Hz):
ventana 1.5 → 2.5 → 3.0 s. Probado `cambios_min: 2`: 18 falsos en la prueba de
mano quieta con atípicos, descartado.

## Fase 2 — Ley de control de avance y giro (`control/seguimiento.py`)

1. `vx_max` configurable, empezando en 0.8 (hasta 1.0), dentro del contrato
   (`vx_range [-0.5, 1.0]`). Si hiciera falta tocar el contrato de uc01: avisar,
   no tocarlo.
2. Estimar la velocidad de la persona: `v_persona ≈ vx_comandada + d(d)/dt`,
   con un filtro de posición y velocidad (alpha-beta o Kalman 1D) en lugar de
   mediana 5 + EMA.
3. Avance continuo, sin arranca/para por histéresis:
   `vx_obj = clip(v_persona + kp_d * (d - d_obj), 0, vx_max)`. Sin el mínimo
   0.25; zona muerta pequeña en `d_obj`; rampas de aceleración y de frenado
   distintas; compensar la zona muerta de Sport Mode al arrancar (parámetro) y
   proponer cómo medirla en el robot.
4. Giro proporcional continuo (`wz = kp_giro * lat`, saturado) con zona muerta
   pequeña, sin el escalón 0.20/0.08 ni el mínimo 0.25. `lat` filtrado.
5. Marcha atrás de seguridad (< 1.4 m) como está.

## Fase 3 — Que el seguimiento no se corte con paradas

1. Siguiendo, sin StopMove ni BalanceStand en pausas cortas: se sigue mandando
   Move con la velocidad que salga (también 0), sin bloquear.
2. Sin esperas bloqueantes en el camino del seguimiento (sleep 0.5 s,
   `enderezar()` 0.7 s...): estados con temporizador dentro del bucle.
3. Persona perdida > 0.5 s: frenar con rampa (watchdog y seguridad siguen
   cortando en seco).

## Fase 4 — Persona quieta → inclinar; persona se mueve → seguir

1. "Persona quieta": `|v_persona|` < umbral (p. ej. 0.15 m/s) y distancia
   estable durante `t_quieta` (p. ej. 1.0 s), con histéresis para salir
   (`|v_persona|` > 0.3 m/s, `d > d_obj + margen` o `|lat|` grande).
2. Solo entonces: Move(0,0,0) → BalanceStand → Euler, como secuencia no
   bloqueante, midiendo cuánto tarda.
3. Persona se mueve: enderezar y volver a mandar Move lo antes posible. Medir
   el mínimo tiempo de enderezado que funciona (ahora 0.7 s), configurable. Sin
   reiniciar filtros ni esperar distancia de arranque.
4. Sustituir la detección por cambio de `tam` (banda 0.04) por el estimador de
   `v_persona`/d.
5. En QUIETO: confirmar que inclina siempre de pie y quieto, también tras
   Hello y tras levantarse.

Pendiente detectado en la Fase 1:

- [ ] **Dos personas en plano:** YOLO (seguimiento) y MediaPipe (gestos)
      eligen cada uno "la más grande" por su cuenta y pueden quedarse con
      personas distintas; `yolo_lat` salta de una a otra. Hay que asociar las
      dos detecciones a la misma persona.

## Fase 5 — Rendimiento y latencia

1. Hilos: cámara (GetImageSample en bucle, solo el último fotograma),
   percepción (YOLO + MediaPipe sobre el último fotograma) y control a
   frecuencia fija (~30-50 Hz: rampas, Move con la última consigna y watchdog).
   Datos compartidos protegidos (lock o cola de 1); el cierre seguro tiene que
   parar el robot si falla cualquier hilo.
2. Decodificar el JPEG ya reducido (p. ej. `cv2.imdecode` con
   `IMREAD_REDUCED_COLOR_2`) en lugar de … *(el enunciado llegó cortado aquí:
   completar)*.
