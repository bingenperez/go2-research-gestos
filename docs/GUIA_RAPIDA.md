# Guía rápida: del PC al perro (uc13, gestos + seguimiento)

Para alguien que **no conoce el proyecto**. Pasos numerados; copia y pega los
comandos. Donde pone `<tu-interfaz>` escribe el nombre que averigües en el
paso 2.

> La conexión de red sigue la guía completa del proyecto,
> [`GUIA_MIS_CASOS_USO.md`, sección 2](GUIA_MIS_CASOS_USO.md#2-conexión-con-el-robot),
> y el script `env/go2_net.sh`. Aquí está resumida y sin suponer que tu puerto se
> llama `eno1`.

Leyenda: 🖥️ **FUERA** = terminal normal de tu PC. 📦 **DENTRO** = dentro del
contenedor (el *prompt* cambia a `go2@...` o `root@...`).

---

## 0. Antes de empezar

- PC con **Linux** (Ubuntu o similar) y **NetworkManager** (lo traen casi
  todos los escritorios). El modo robot real **no funciona** en Windows/macOS.
- **Docker** instalado y tu usuario en el grupo `docker` (ver "Si falla...").
- **git** instalado.
- Cable Ethernet entre el PC y el robot (o un adaptador USB‑Ethernet).
- 🛑 **Seguridad:** mando del robot en la mano (**`L2` + `B` = parada
  inmediata**), 5 m despejados, batería > 50 %, **app del móvil cerrada del
  todo**, cable con holgura.

Datos fijos de la red del perro:

| qué | valor |
|---|---|
| IP del robot | `192.168.123.161` |
| IP que pondrás en tu PC | `192.168.123.222/24` |
| nombre de la conexión que vas a crear | `go2-eth` |

---

## 1. Descargar el proyecto y la imagen (una vez, con internet)

🖥️ FUERA:

```bash
git clone https://github.com/bingenperez/go2-research-gestos.git
cd go2-research-gestos
docker pull ghcr.io/bingenperez/go2-research-gestos:latest
```

La imagen ya trae todo (ROS 2, SDK de Unitree, OpenCV, YOLO, **MediaPipe y su
modelo de pose**). No hay que instalar nada dentro del contenedor.

> Todos los comandos siguientes se ejecutan **dentro de la carpeta
> `go2-research-gestos`**. Si abres otra terminal: `cd go2-research-gestos`.

---

## 2. Averiguar el nombre de TU puerto Ethernet

`eno1` **no es algo que se crea**: es el nombre que Linux le da al puerto
Ethernet, y **cambia en cada PC** (`eno1`, `enp3s0`, `eth0`,
`enx00e04c...` si es un adaptador USB...). Para saber el tuyo:

**2.1** Con el cable **desenchufado**, 🖥️ FUERA:

```bash
ip -br a
```

**2.2** Enchufa el cable al robot (robot encendido y esperado ~2 min) y repite:

```bash
ip -br a
```

**2.3** Tu interfaz es la que ha pasado de `DOWN` a `UP` (o la que acaba de
aparecer, si es un adaptador USB). Ejemplo:

```text
antes:   enp3s0   DOWN
después: enp3s0   UP     fe80::.../64
```

Aquí `<tu-interfaz>` = `enp3s0`. **Ignora** `lo`, `wlp...`/`wlan...` (Wi‑Fi),
`docker0`, `br-...` y `veth...`.

Atajo del proyecto (dice cuáles tienen cable conectado ahora):

```bash
./env/go2_net.sh detect
```

---

## 3. Crear la conexión con IP fija (solo la primera vez)

🖥️ FUERA (pide la contraseña de tu usuario):

```bash
./env/go2_net.sh create <tu-interfaz>
```

Es lo mismo que hacer a mano:

```bash
sudo nmcli con add type ethernet ifname <tu-interfaz> con-name go2-eth \
    ipv4.method manual ipv4.addresses 192.168.123.222/24 \
    ipv4.never-default yes ipv6.method disabled connection.autoconnect no
```

- `ipv4.never-default yes`: **no te quita internet** (el Wi‑Fi sigue siendo la
  salida a internet).
- `autoconnect no`: la conexión solo se activa cuando tú lo pidas (paso 4).

Debe salir una tabla con `ipv4.addresses: 192.168.123.222/24`.

---

## 4. Activar la conexión y comprobar con ping

🖥️ FUERA, **cada vez** que conectes el robot:

```bash
./env/go2_net.sh up          # o:  nmcli con up go2-eth
ping -c 3 192.168.123.161
```

**Qué tiene que salir:** `3 received, 0% packet loss`. Si no, ve a
[Si falla...](#si-falla).

Diagnóstico completo (enlace, IP, ping, ARP):

```bash
./env/go2_net.sh probe
```

Guarda tu interfaz para no tener que escribirla siempre:

```bash
cp -n .env.example .env
sed -i "s/^GO2_IFACE=.*/GO2_IFACE=<tu-interfaz>/" .env
```

---

## 5. Entrar en el contenedor

🖥️ FUERA:

```bash
./run.sh <tu-interfaz>
```

(Si ya guardaste `GO2_IFACE` en `.env`, basta con `./run.sh`.)

`run.sh` da permiso de ventanas (`xhost +local:docker`), usa la red del PC
(necesaria para hablar con el robot) y abre el contenedor. **Qué tiene que
salir:**

```text
  Entorno Go2 listo
    modo      : real
    interfaz  : <tu-interfaz>
    GUI       : x11
```

y el *prompt* cambia: estás 📦 **DENTRO**. Al salir (`exit`) el contenedor se
borra; tu carpeta del proyecto no.

Comprobación rápida 📦 DENTRO (no mueve nada):

```bash
python3 -c "import cv2, mediapipe; print(cv2.__version__, mediapipe.__version__)"
python3 tools/dds_smoketest.py --mode real --iface <tu-interfaz> --duration 10
```

---

## 6. Prueba en seco (`--dry-run`: NO mueve el robot)

Robot **de pie y quieto**. 📦 DENTRO:

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface <tu-interfaz> --source robot --dry-run --show --debug
```

Se abre una ventana con lo que ve el robot, tu esqueleto y el gesto
detectado. En la terminal debe poner `DRY RUN: no se crea SportClient ni se
envia nada`. Sal con `q` en la ventana o **un** `Ctrl + C`.

---

## 7. Prueba real (el robot SE MUEVE)

Robot **tumbado**, mando en la mano, 5 m despejados, cable con holgura.
📦 DENTRO:

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface <tu-interfaz> --source robot --show --duration 180 --log
```

Te pedirá escribir **`si`** antes de mover nada. Gestos (detalle en
[`usecases/uc13_gesture_follow/README.md`](../usecases/uc13_gesture_follow/README.md)):

| gesto | qué hace |
|---|---|
| una mano levantada (a ~4 m) | se levanta |
| T (brazos en cruz) | saluda / para de seguir |
| dos manos levantadas (~2.5 m) | empieza a seguirte a 2 m |
| brazos cruzados | se tumba |

---

## 8. Cómo parar el robot

1. **`L2` + `B` en el mando**: parada inmediata. **Siempre lo primero** ante
   cualquier duda.
2. En el programa: **`q`** en la ventana o **UN SOLO `Ctrl + C`**. Arranca el
   *cierre seguro* (enderezar, parar, tumbar). **No pulses `Ctrl + C` dos
   veces**: lo cortarías a medias. Espera a que termine de imprimir.
3. Si imprime `USA EL MANDO: L2+B`, hazlo.
4. Al acabar: 📦 `exit`, y 🖥️ `./env/go2_net.sh down` (o `nmcli con down go2-eth`).

> Cortar el programa **no** detiene al robot por sí solo: el mando sí.

---

## Si falla...

### No hay ping al robot

| comprobación | comando 🖥️ FUERA |
|---|---|
| ¿la conexión está activa? | `nmcli con show --active` (debe salir `go2-eth`) → si no: `nmcli con up go2-eth` |
| ¿tu PC tiene la IP? | `ip -br a show <tu-interfaz>` → debe salir `192.168.123.222/24` |
| ¿hay enlace físico? | si sale `NO-CARRIER` o `DOWN`: cable mal puesto, robot apagado o aún arrancando (espera 2 min) |
| ¿responde a nivel de red? | `./env/go2_net.sh probe`: "ARP sí / ping no" = IP o máscara mal; "ni ARP ni ping" = cable/puerto/robot |

Si el robot acaba de encenderse, espera un par de minutos y repite el ping.

### Interfaz equivocada

Síntomas: `does not match an available interface`, `La interfaz '...' no
existe`, `No llega rt/sportmodestate`, o el ping falla aunque todo parece bien.

```bash
ip -br a                                  # repite el paso 2 (con y sin cable)
nmcli -g connection.interface-name con show go2-eth   # a qué interfaz está atada go2-eth
./env/go2_net.sh create <tu-interfaz-correcta>        # la recrea sobre la buena
sed -i "s/^GO2_IFACE=.*/GO2_IFACE=<tu-interfaz-correcta>/" .env
```

Usa **el mismo nombre** en `--iface`, en `./run.sh` y en `.env`.

### No se abre la ventana (`--show`)

| ves | haz |
|---|---|
| no aparece ninguna ventana y no hay error | entraste sin `./run.sh`. Sal (`exit`) y entra con `./run.sh <tu-interfaz>`. Dentro, `echo $DISPLAY` debe dar lo mismo que fuera (normalmente `:0` o `:1`) |
| `cannot connect to X server` / `Authorization required` | 🖥️ FUERA: `xhost +local:docker` y repite (no hace falta salir del contenedor) |
| ventana gris o negra, `BadAccess` | 📦 `export QT_X11_NO_MITSHM=1` (la imagen ya lo trae; solo si lo has cambiado) |
| entras por `ssh` | la pantalla del PC es `:0`: 📦 `export DISPLAY=:0` |
| sesión Wayland sin XWayland | cierra sesión y entra en "Ubuntu on Xorg" |

### `permission denied` con docker

`permission denied while trying to connect to the Docker daemon socket`: tu
usuario no está en el grupo `docker`. 🖥️ FUERA:

```bash
sudo usermod -aG docker $USER
```

y **cierra sesión y vuelve a entrar** (o reinicia). Comprueba con
`docker run --rm hello-world`.

Otros `permission denied`:

- `./run.sh: Permiso denegado` → `chmod +x run.sh build.sh push.sh env/go2_net.sh`
- `docker pull` da `denied` / `unauthorized` → la imagen aún es privada: pide
  que la hagan pública o haz `docker login ghcr.io` con un token de GitHub.
- no puedes borrar ficheros creados por el contenedor →
  `sudo chown -R $USER:$USER ~/go2-research-gestos`

### Otros

| ves | qué hacer |
|---|---|
| código `3205` | la app del móvil o el mando tienen el control: cierra la app del todo |
| `la camara ha dejado de dar imagen` | app cerrada, cable firme, `./env/go2_net.sh probe`; si sigue, reinicia el robot (tumbado) |
| `ERROR: GO2_MODE=real requiere GO2_IFACE` | usa `./run.sh <tu-interfaz>` |
| `ModuleNotFoundError: numpy / cv2` | estás FUERA: entra con `./run.sh` |

Guía completa con todos los casos: [`GUIA_MIS_CASOS_USO.md`](GUIA_MIS_CASOS_USO.md).
