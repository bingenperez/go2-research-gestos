# uc13 — Gestos + seguimiento a 2 m

## Qué hace

El robot real responde a **cinco gestos** y puede **seguir** a una persona a
unos **2 m**. En reposo (quieto de pie, o siguiendo pero parado más de 1 s)
**inclina el cuerpo 0.25 rad (~14°) hacia arriba** para ver los gestos.

| gesto | TUMBADO | DE PIE quieto | SIGUIENDO / PAUSADO |
|---|---|---|---|
| **una mano** levantada | `StandUp()` + `BalanceStand()` | nada | nada |
| **dos manos** levantadas | nada | **empieza a seguir** | **para**: `Move(0,0,0)` + `StopMove()` + `BalanceStand()` |
| **T** (brazos en cruz) | nada | `Hello()` | **para** |
| **saludar** con la mano (vaivén) | `StandUp()` + `BalanceStand()` | `Hello()` | nada |
| **brazos cruzados** | nada | `StopMove()` + `StandDown()` | nada ("para primero") |

Con el robot SENTADO, DUDOSO o SIN_DATOS, ningún gesto hace nada. Cuando no
hace nada, lo dice.

```
cámara ─┬─► YOLO ───────────► persona (lateral, tam) ─► control ─► Move(vx, 0, wz)
        └─► MediaPipe Pose ──► gesto ─► confirmación ─► SportClient
reposo:  BalanceStand + Euler(0, signo·0.25, 0) reenviado cada 0.1 s (morro arriba)
```

Llamadas permitidas:

- `Move`: solo siguiendo, con el cuerpo recto, vy = 0 y vx entre −0.15 y 0.4.
- `Euler`: solo en reposo tras `BalanceStand`, con roll = yaw = 0 y |pitch| ≤ 0.25 rad.
- `StopMove`, `BalanceStand`, `StandUp`, `StandDown` y `Hello`.
- `RiseSit`: solo en el cierre seguro.

**Nunca `Sit` ni acrobacias.**

## Diagnóstico de la prueba del 2026-11-18 ("anda raro")

Datos de `experiments/uc13_gesture_follow/2026-11-18T14*`: 4 ejecuciones y
1 773 fotogramas siguiendo.

- **No eran** la cámara (hueco máximo 0.1 s), el watchdog (0 veces) ni
  PAUSADO (10 fotogramas).
- **Era la velocidad**: vx = 0.20 m/s el 75–95 % del tiempo andando. La
  ganancia estaba en unidades de `tam`, y `tam` cambia ~0.07 por metro: la
  orden salía por debajo de 0.2 y se subía al mínimo útil. El robot andaba en
  la marcha más lenta de Sport Mode (pasitos cortos, balanceo) y la persona se
  alejaba (de 4.3 a 5.5 m en 1.5 s, hasta 13 m).
- **Además:** cada arranque pasaba por 0.08 y 0.17 m/s, el giro se encendía y
  apagaba 13–18 veces por minuto con valores minúsculos, y había valores
  atípicos de `tam` que el filtro dejaba pasar.
- **body_height al andar:** entre 0.282 y 0.320, a 2 mm del límite de pie (0.28).

## Por qué está montado así

**Distancia objetivo 2 m. Andando solo ve piernas.** La cámara está a 30 cm
del suelo y ve ~±23.5° en vertical (deducido de la medida de uc04). A 2 m:

| punto (persona de 1.70 m) | ángulo | recto | inclinado 8° | inclinado 14° (0.25 rad) |
|---|---|---|---|---|
| tobillos y caderas | −6° / +17° | sí | sí | sí |
| hombros, T, cruzados | +29° | no | sí | sí |
| nariz ("una mano") | +32° | no | no | sí |
| muñecas arriba ("dos manos") | +41° | no | no | **no** (a 2 m necesita ~20°; con 14° se ve desde ~2.5 m) |

- **Seguir con YOLO, no con MediaPipe.** MediaPipe (BlazePose) usa la cara
  para detectar a la persona: sin cabeza no es fiable. YOLO (copiado de uc04,
  `perception/personas.py`) detecta cuerpos parciales. MediaPipe queda para
  los gestos. **Si no se ve tu cabeza pero sí el cuerpo, no estás perdido.**
- **Parar a 2 m: con la T.** "Dos manos" no entra con 14°: da medio paso atrás
  (~2.5 m) o usa la T. El ángulo es `inclinacion.max_rad`.
  (Tabla estimada con el campo de visión deducido de uc04: compruébalo en el
  robot.)
- **Tumbado no se inclina** (Euler solo de pie): para levantarlo con "una
  mano" hay que estar a ~4 m, como en uc12.

**Independiente.** Copia lo que necesita de uc12 (gestos, estado, fotogramas
repetidos, confirmador, cierre) y de uc04 (YOLO y la base del control). No
importa nada de uc04, uc09, uc10, uc11 ni uc12. Solo usa `go2core` y
`tools/robot_camera.py`.

## Seguimiento (`control/seguimiento.py`)

| | |
|---|---|
| medida | `tam` = alto de la caja de YOLO / alto de la imagen; pasa a **metros** con dos puntos calibrados (2.0 y 1.3 m) |
| filtro | `mediana+ema` (configurable): la mediana de 5 quita los atípicos de un fotograma |
| avance | **histéresis**: arranca si d > 2.5 m, para si d < 2.15 m. En marcha `vx = 0.5 · (d − 2)`, entre **0.25** (por encima del mínimo de Sport Mode) y 0.4 |
| rampas | 0.5 m/s² al acelerar **y al frenar**; giro 1.5 rad/s². Las paradas de seguridad son inmediatas |
| giro | histéresis: gira si \|lateral\| > 0.20, deja de girar si < 0.08; \|wz\| entre 0.25 y 0.6 |
| marcha atrás | si d < **1.4 m** (`marcha_atras.d_m`; no 1.3: ahí `tam` ya satura, ver "Distancia"): −0.15 m/s, como mucho 3 s seguidos; si sigue cerca, se para y no vuelve a retroceder hasta que pases de 1.6 m |
| pérdida | YOLO sin persona > 0.5 s: velocidad 0 y PAUSADO; 5 s: deja de seguir |
| altura | siguiendo, pausado o inclinado vale como de pie **0.26–0.40 m** (`de_pie_activo`; inclinado sube hasta ~0.366) |

**Aviso:** uc04 midió que Sport Mode ignora o hace tirones por debajo de
~0.2 m/s. La marcha atrás a 0.15 puede no funcionar bien: compruébalo y, si
hace falta, sube `marcha_atras.vx` a 0.2.

## Inclinación (`control/inclinacion.py`)

> **Pruebas en el robot real, 2026-11-20** (`--probar-inclinacion`:
> `BalanceStand`, 1 s, y `Euler(0, signo·0.25, 0)` cada 0.1 s durante 3.5 s;
> `MotionSwitcher.CheckMode` = `mcf`, sport API `1.0.0.1`, `mode` siempre 0):
>
> | prueba | signo | resultado |
> |---|---|---|
> | 1ª, **justo después de encender** | +1 | **no se movió** (IMU de `rt/lowstate` −0.0°, `body_height` igual). Códigos sin registrar |
> | 2ª, seguida de la 1ª | +1 | se movió, **morro ABAJO**. Códigos 0; IMU +13.0°; `body_height` 0.328 → 0.366 m |
> | 3ª, **tras un rato** sin usarlo | −1 | **no se movió** (IMU −0.0°, `body_height` fijo en 0.320). **Códigos 0** |
> | 4ª, seguida de la 3ª | −1 | **morro ARRIBA**. Códigos 0; IMU −12.9°; `body_height` 0.320 → 0.361 m |
> | A, con `--log` (`--mantener 15 --ciclos 2`) | −1 | **se movió a la primera**, los dos ciclos. Ver tabla de tiempos |
> | B, justo después (`--mantener 3`) | −1 | **se movió a la primera** |
>
> - **Signo confirmado:** `signo: -1` (pitch negativo = morro arriba).
> - **Patrón (2 de 2):** la primera secuencia de Euler tras encender, o tras un
>   rato, **no hace nada aunque el robot responde 0 a todo**; la siguiente sí.
>   Causa sin confirmar: ver "Primera secuencia sin efecto".
> - **Tiempos medidos** (A y B, IMU de `rt/lowstate` a ~49 Hz; logs en
>   `experiments/uc13_gesture_follow/2026-11-20T100435_*` y `T100558_*`):
>
>   | | medido | en el programa |
>   |---|---|---|
>   | empieza a moverse tras el primer Euler | 0.16 s | |
>   | llega al 90 % | 0.31 s | `t_inclinar_s` = 0.5 |
>   | ángulo estable | −13.4° de IMU (≈ 12.4° desde recto; pedidos 14.3°), ±0.03° | |
>   | vuelve a < 2° tras Euler 0 | 0.27 s; asentado ≈ 0.4 s | `t_enderezar_s` = **0.7** (antes 0.5) |
>   | espera tras `BalanceStand` | 1 s en todas las pruebas que funcionaron | `espera_balance_s` = **1.0** |
>
> - Con A y B **no se reprodujo** el fallo de la primera secuencia.
> - **`body_height` inclinado** llega a 0.361–0.366 m. El tope de
>   `de_pie_activo` se ha subido de 0.36 a **0.40** para que inclinado siga
>   contando como de pie.

`SportClient.Euler(roll, pitch, yaw)`, API 1007, en radianes.

**Método:** `BalanceStand` y después `Euler(0, pitch, 0)` **en bucle cada
0.1 s**. Un solo Euler de 5° no movía el robot (2026-11-18). **pitch negativo
levanta el morro**: con +0.25 el morro bajó (2026-11-20).

| `inclinacion.` | valor | |
|---|---|---|
| `activa` | `true` | |
| `signo` | `-1` | pitch negativo = morro arriba (con +0.25 bajó, con −0.25 subió; 2026-11-20) |
| `max_rad` | `0.25` | ~14°; también es el tope de la lista blanca de Euler |
| `periodo_envio_s` | `0.1` | inclinado (y enderezando) se reenvía Euler cada 0.1 s |
| `t_inclinar_s` | `0.5` | tras el primer Euler se da por inclinado |
| `t_enderezar_s` | `0.7` | Euler 0 durante 0.7 s antes de volver a mandar `Move` (medido: recto en ~0.4 s) |
| `espera_balance_s` | `1.0` | tras cualquier `BalanceStand`, espera antes del primer Euler (como en las pruebas que funcionaron) |
| `espera_quieto_s` | `1.0` | tiempo en reposo antes de inclinarse |
| `banda_tam` | `0.04` | inclinado: cambio de `tam` que indica que te mueves |

```
RECTO ─inclinar─► INCLINANDO ─0.5 s─► INCLINADO ─enderezar─► ENDEREZANDO ─0.7 s─► RECTO
        Euler(0, signo·0.25, 0) cada 0.1 s ──────────┘        Euler(0,0,0) cada 0.1 s
```

- **Cuándo se inclina:**
  - **QUIETO de pie** esperando gestos. Si al arrancar ya estaba de pie sin
    `BalanceStand` (lo levantó el mando), manda `BalanceStand` primero.
  - **SIGUIENDO pero parado**: vx = 0 y wz ≈ 0 durante más de 1 s (estás
    quieto). Si venía andando, primero `StopMove` + `BalanceStand`.
  - Siempre **1 s después del último `BalanceStand`** como mínimo
    (`espera_balance_s`).
  - Al **empezar a seguir** no se inclina hasta 1 s después: antes mide la
    distancia recto.
  - Así puede ver si levantas la mano estando a menos de 2 m.
- **Solo tiempos, sin IMU para decidir.** Las dos IMU (`rt/lowstate` y
  `sportmodestate`) marcan bien cuando el robot se mueve (−13.4° en A y B); el
  0.0 de las primeras pruebas era porque el robot no se había movido. Ya no se decide nada
  con ella: solo se muestra. `--estado` y `--probar-inclinacion` enseñan
  también el cabeceo calculado del cuaternión de `rt/lowstate`, para ver cuál
  de las dos se mueve.
- **Nunca anda inclinado.** Antes de cualquier `Move`: Euler 0 cada 0.1 s
  durante 0.7 s (`t_enderezar_s`; medido: recto en ~0.4 s) y entonces anda.
  `Move` se rechaza si el cuerpo no está RECTO.
- **Al empezar a seguir** (dos manos) estando inclinado: se endereza y mide la
  distancia recto antes de decidir.
- **La distancia no se calcula inclinado**, y la calibración (`[cal]`) tampoco
  usa esos fotogramas. Para notar que te mueves se compara el `tam` inclinado
  con el `tam` inclinado de referencia (tomado 0.5 s después de inclinarse):
  si cambia más de ±0.04, o te desplazas a un lado, se endereza, mide recto y
  decide.
- **Antes de Hello, StandDown y en el cierre:** Euler 0 durante 0.7 s.
- Si la lista blanca rechaza el Euler o el robot devuelve un código distinto
  de 0, la inclinación se desactiva en la sesión y se endereza.

**Limitación conocida:** si te mueves justo durante el primer segundo de
inclinación (antes de guardar la referencia de `tam`), no lo nota hasta el
siguiente cambio. Por ejemplo, después de una marcha atrás, la marcha atrás
solo se rearma cuando te mide recto a más de 1.6 m.

### Primera secuencia sin efecto (pendiente)

Visto 2 de 2 veces el 2026-11-20: la primera `--probar-inclinacion` tras
encender, o tras un rato, no mueve nada aunque `BalanceStand` y todos los
`Euler` devuelven 0. La siguiente, justo después, sí. Causa sin confirmar; en
estudio. Sin efecto no hay riesgo (no se anda inclinado ni se depende de la
IMU), pero esa vez el robot no mira hacia arriba.

**Arreglo provisional (rama `inclinar-siempre`):** en la práctica el perro solo se inclinaba después del saludo (la segunda secuencia). Ahora, 0.8 s después de darse por inclinado, el bucle mira la IMU; si no ha girado al menos 3°, manda Euler 0, otro `BalanceStand` y se vuelve a inclinar (como mucho 3 veces seguidas; `inclinacion.comprobar` en el yaml). La IMU solo decide si se repite; la seguridad sigue siendo por tiempos. Sale en pantalla como `no se ha inclinado (IMU ...): reintento n/3`.

Para estudiarlo, `--probar-inclinacion` **mide en el tiempo** con la IMU de
`rt/lowstate` (si no llega, la de `sportmodestate`) a ~50 Hz. La IMU solo
mide; no decide nada. Por cada ciclo dice:

- si se ha movido (≥ 3°) y cuánto (IMU negativa = morro arriba);
- cuándo empieza a moverse (> 2°), contado desde el primer Euler y desde el
  `BalanceStand`, y cuándo llega al 90 %;
- cuánto tarda en volver a < 2° tras el Euler 0. Avisa si pasa de
  `t_enderezar_s` (0.7 s): sería andar aún inclinado.

| opción | por defecto | |
|---|---|---|
| `--mantener S` | 3 | segundos inclinado por ciclo (más `t_inclinar_s`) |
| `--espera-balance S` | 1 | espera tras `BalanceStand` antes del primer Euler |
| `--ciclos N` | 1 | ciclos inclinar → enderezar (2 s de Euler 0) **sin** nuevo `BalanceStand` |
| `--log` | | guarda `serie.csv` (IMU, órdenes y códigos con su tiempo) y `manifest.json` (métricas) en `experiments/uc13_gesture_follow/<fecha>_<sha>_probar_inclinacion/` |

Si el ciclo 1 no se mueve y uno posterior sí, lo dice (`PATRON`).

## Saludo con la mano (`perception/pose.py`, `DetectorSaludo`)

Gesto **dinámico**: una muñeca por encima de su codo (`gestos.saludo.referencia`,
`margen_anchos` 0.3) y la otra no, y esa muñeca va y viene en horizontal
respecto a su hombro (también vale la muñeca por encima de la nariz, como en
"una mano"). En los últimos `ventana_s` (3.0 s) tiene que haber
`cambios_min` (3) cambios de dirección, y cada tramo entre dos extremos tiene
que medir al menos `amplitud_min` (0.3 anchos de hombro). Vale cualquier mano.

- **No cuenta 15 fotogramas**: la ventana ya es la confirmación (en las
  pruebas, 0.7–1.4 s desde que empiezas a saludar). Tiene prioridad sobre
  "una mano".
- **Re-armado:** tras actuar, no vuelve a saltar hasta que **bajas el brazo**
  0.3 s (aunque sigas saludando durante y después del Hello).
- Mano levantada quieta, mano que sube y baja, dos manos saludando o vaivén
  pequeño: no es saludo. Un saludo de ~0.6 Hz sí (medido en el robot el 2026-11-24).
- La serie de la muñeca se suaviza con mediana de 3 (quita atípicos de un
  fotograma; 1 fotograma de retraso).
- `gestos.saludo.activo: false` lo apaga (la T sigue haciendo Hello).
- **Umbrales sin medir en el robot.** Con `--log`, `metrics.csv` guarda
  `saludo_cambios`, `saludo_amplitud`, `saludo_lado` y la posición de cada
  muñeca alzada (`saludo_x_i`, `saludo_x_d`); `eval/measure.py` resume lo
  medido con y sin saludo. Con la webcam: `perception/pose.py --show`.

## Gestos durante el cooldown

- **El gesto que acaba de actuar** tiene que dejar de verse para volver a
  contar: mantener "dos manos" no enciende y apaga el seguimiento.
- **Otro gesto distinto** cuenta durante el cooldown. Si se confirma queda
  **pendiente** y se ejecuta al acabar, si lo sigues haciendo. No hace falta
  bajar los brazos.
- **Parar** (dos manos o T siguiendo) no espera al cooldown.
- **En la ventana:** `COOLDOWN 3.2 s` en grande y `pendiente: …` debajo.

## Calibrar (antes de la prueba real)

### Distancia
Calibrado el 2026-11-18 con la cámara del robot: `tam_objetivo: 0.786`
(2.0 m) y `tam_cerca: 0.888` (1.3 m), 0.146 de tam por metro. A 1.3 m la caja
ya toca el borde de arriba y `tam` satura en ~0.89 (`tam_saturacion`): más
cerca la distancia medida se queda en ~1.29 m. Por eso la marcha atrás salta a
1.4 m (tam 0.873), con margen, y no a 1.3 (tam 0.888, a 0.002 de saturar).
En tam: arrancar 2.5 m = 0.713, parar 2.15 m = 0.764, rearme 1.6 m = 0.844.
Por encima de 2 m la recta es extrapolación: si quieres afinar el arranque,
mide también la mediana a 2.5 m.

## Pruebas sin robot

`tests/test_uc13.py`, sin red y con el repo en solo lectura:

- gestos excluyentes;
- filtro y distancia;
- histéresis, sin saltos bruscos, rampas al frenar, marcha atrás limitada y
  rearme, giro con histéresis;
- inclinación: Euler al momento y reenviado cada 0.1 s; inclinado a los 0.5 s;
  Euler 0 cada 0.1 s y recto a los `t_enderezar_s`; si hay rechazo o un código ≠ 0 se
  desactiva;
- lista blanca de `Move` y `Euler`;
- rango de pie activo;
- bucle completo con robot, YOLO y MediaPipe falsos:
  - interruptor sin rebote, parar sin cooldown, gesto pendiente;
  - sin cabeza sigue andando; pérdida de la persona;
  - cámara congelada y estado viejo;
  - histéresis con ruido y marcha atrás;
  - en todas las ejecuciones: **nunca hay `Move` inclinado ni antes de
    `t_enderezar_s` (0.7 s) tras el Euler 0**, ningún Euler inclinado antes de
    `espera_balance_s` (1 s) tras un `BalanceStand`, Euler solo con 0 o
    signo·0.25, y ningún hueco > 0.1 s (más un margen) sin reenviar mientras
    está inclinado o enderezando;
  - siguiendo parado > 1 s se inclina; si te alejas, se endereza y anda;
  - quieto de pie al arrancar: `BalanceStand` y se inclina;
  - Euler 0 antes de `StandDown` y en el cierre;
  - la IMU no cambia nunca y se inclina igual; `--probar-inclinacion`;
  - body_height 0.27 andando, dry-run y `--log`;
- repetición del control nuevo sobre los `metrics.csv` reales.

## Seguir solo a una persona (rama `seguir-solo-a-mi`)

Antes se seguía, en cada fotograma, a la caja de YOLO más grande: si alguien
se cruzaba más cerca, el robot se iba con esa persona. Ahora
(`perception/identidad.py`, `identidad:` en el yaml):

1. Al hacer **dos manos** (empezar a seguir) se guarda la **firma** de quien
   hace el gesto: histogramas de color de su ropa, franja de arriba (camiseta)
   y franja de abajo (pantalón y zapatos). La de abajo pesa más (`peso_abajo`
   0.7): andando a 2 m solo se ven piernas.
2. Siguiendo, de todas las personas se elige la que **más se parece** a la
   firma y está **cerca de donde se le vio** (`salto_max`).
3. Si **nadie se parece** lo bastante (`umbral`), no sigue a otra: se para
   (como al perder a la persona) y a los `pausa_max_s` (5 s) deja de seguir.
4. Siguiendo, **solo cuentan sus gestos** (dos manos o T para parar).
5. Al dejar de seguir, **se olvida** la firma. Quieto, cualquiera puede dar órdenes.

Si no se puede guardar la firma (imagen sin color), sigue como antes, a la más
grande. Umbrales **sin medir en el robot**: con `--debug` mirar `id_d`
(parecido del elegido; 0 = igual) y `id_desc` (personas descartadas).

## No chocar (rama `no-chocar`)

Usa el servicio **obstacles_avoid** del propio robot (LiDAR L1 del EDU), del
SDK: `unitree_sdk2py.go2.obstacles_avoid.ObstaclesAvoidClient`.

- Al arrancar: `SwitchGet` (se guarda como estaba), `SwitchSet(True)` y
  `UseRemoteCommandFromApi(True)`.
- Siguiendo, **todos los Move van por `ObstaclesAvoidClient.Move`**: el robot
  frena o rodea lo que tiene delante. Lo demás (BalanceStand, Euler, Hello,
  StandDown...) sigue por SportClient.
- Al cerrar: `UseRemoteCommandFromApi(False)` y `SwitchSet` como estaba.
- Si el servicio no responde: avisa (`PUEDE CHOCAR`) y Move va directo, como
  antes. Con `obstaculos.obligatorio: true` no arranca.

**Sin probar en el robot.** Qué mirar la primera vez:

1. Al arrancar sale `evitar obstaculos: ENCENDIDO`.
2. Quieto: **sigue inclinándose** (Euler con el servicio encendido no está
   probado). Si no, poner `obstaculos.activa: false` y avisar.
3. Siguiendo, con una caja delante entre tú y el robot: frena o la rodea.
4. Siguiendo a 2 m: no te toma a ti por obstáculo (no se queda parado).
5. Al cerrar, el mando vuelve a mover el robot.

## Estado

- [x] Seguir a 2 m con YOLO; gestos con MediaPipe
- [x] Control en metros con filtro, histéresis, rampas y marcha atrás limitada
- [x] Inclinación 0.25 rad en reposo con Euler cada 0.1 s; recto (0.7 s) antes de cada `Move`
- [x] Gestos pendientes durante el cooldown
- [x] Lógica probada sin robot
- [ ] `tam` calibrado a 2.0 y 1.3 m con la cámara del robot
- [x] Signo de la inclinación comprobado con `--probar-inclinacion` (`-1`)
- [ ] Primera secuencia de Euler sin efecto (tras encender o tras un rato): causa y arreglo
- [ ] Gestos comprobados inclinado a 2 m (T, cruzados y una mano) y a ~2.5 m (dos manos)
- [x] Saludo con la mano (Hello) probado sin robot
- [ ] Saludo con la mano en el robot: umbrales con datos de `--log`
- [ ] Marcha atrás a 0.15 m/s comprobada (Sport Mode puede ignorarla)
- [ ] Probado en robot real
