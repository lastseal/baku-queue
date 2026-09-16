# baku-queue

Librería Python para crear microservicios de colas de trabajo usando ZeroMQ. Permite procesamiento asíncrono de tareas mediante el patrón Producer/Consumer.

## Descripción

`baku-queue` proporciona funciones y decoradores para enviar y consumir tareas en colas usando ZeroMQ. Está diseñado para crear microservicios simples con procesamiento asíncrono de tareas, permitiendo desacoplar la recepción de trabajo (por ejemplo, desde `baku-api`) de su procesamiento.

## Instalación

### Desde GitHub Packages

Primero, configura pip para usar GitHub Packages creando o editando `~/.pip/pip.conf` (Linux/Mac) o `%APPDATA%\pip\pip.ini` (Windows):

```ini
[global]
extra-index-url = https://lastseal:TOKEN@pkg.github.com
```

Luego instala el paquete:

```bash
pip install baku-queue
```

### Desde el repositorio (desarrollo)

```bash
pip install git+https://github.com/lastseal/baku-queue.git
```

Nota: Reemplaza `TOKEN` con un Personal Access Token con permisos de lectura de paquetes.

### Dependencias

- `pyzmq>=25.0.0` - Bindings de ZeroMQ para Python
- `baku-config>=1.0.0` - Configuración y logging estandarizado

## Uso

### Enviar Tareas a la Cola

Usa la función `queue.send()` para enviar tareas a la cola:

```python
from baku import queue

# Enviar una tarea
task = {
    "task_type": "process_data",
    "data": {"id": 123, "name": "Example"},
    "priority": "high"
}

success = queue.send(task)
if success:
    print("Tarea enviada exitosamente")
```

### Consumir Tareas de la Cola

Usa el decorador `@queue.consume()` para procesar tareas de forma continua:

```python
from baku import queue
import logging

@queue.consume()
def process_task(task):
    """Procesa una tarea de la cola."""
    logging.info(f"Procesando tarea: {task['task_type']}")
    
    # Tu lógica de procesamiento aquí
    task_type = task.get('task_type')
    data = task.get('data', {})
    
    if task_type == "process_data":
        # Procesar datos
        result = process_data(data)
        return {"status": "completed", "result": result}
    
    return {"status": "ok"}
```

## Variables de Entorno

```bash
# Puerto ZeroMQ (opcional, default: 5555)
QUEUE_PORT=5555

# Host al que el consumidor hace connect (opcional, default: 127.0.0.1)
QUEUE_HOST=127.0.0.1

# Timeout en segundos para operaciones (opcional, default: 30)
QUEUE_TIMEOUT=30

# Número de intentos de reintento (opcional, default: 3)
QUEUE_RETRY_ATTEMPTS=3

# Número de workers concurrentes (opcional, default: 1)
QUEUE_WORKERS=1

# Número de tareas a pre-cargar por worker (opcional, default: 10)
QUEUE_PREFETCH=10
```

### Cómo se conectan productor y consumidor

- **`queue.send()`** (productor): socket PUSH con **bind** en `tcp://0.0.0.0:{QUEUE_PORT}` al enviar cada tarea.
- **`@queue.consume()`** (consumidor): socket PULL con **connect** persistente a `tcp://{QUEUE_HOST}:{QUEUE_PORT}`.

En el mismo host o contenedor, dejar `QUEUE_HOST=127.0.0.1` (default). En Docker Compose con servicios separados, el worker debe usar el **nombre del servicio** del API como `QUEUE_HOST`.

## Docker Compose (API + Worker)

Patrón de referencia: `vigia-export-service` en `vidatec-sos-compose`.

### Arquitectura

Dos servicios con la misma imagen:

| Servicio | Comando | Rol |
|----------|---------|-----|
| `{nombre}-api` | `python src/main.py` | HTTP (`baku-api`) + `queue.send()` |
| `{nombre}-worker` | `python src/worker.py` | `@queue.consume()` + lógica pesada |

El API responde rápido (`{ status: "queued" }`). El worker procesa en background.

### `docker-compose.yml`

```yaml
  export-api:
    build: ../mi-servicio
    restart: unless-stopped
    environment:
      - QUEUE_PORT=5555
      # QUEUE_HOST no hace falta en el API (send hace bind en 0.0.0.0)
    depends_on:
      - database

  export-worker:
    build: ../mi-servicio
    restart: unless-stopped
    command: python src/worker.py
    environment:
      - QUEUE_PORT=5555
      - QUEUE_HOST=export-api   # nombre del servicio API en Compose
      - QUEUE_WORKERS=1         # obligatorio si el worker usa baku-db
    depends_on:
      - export-api
```

Reglas:

1. **`QUEUE_HOST` solo en el worker**, con el nombre DNS del servicio API (`export-api`, no `127.0.0.1`).
2. **Mismo `QUEUE_PORT`** en ambos servicios.
3. **`depends_on: export-api`** en el worker para que el consumidor arranque después del API.
4. **No hace falta `network_mode`** ni publicar el puerto 5555 al host: la red interna de Compose alcanza `export-api:5555`.
5. Si el worker usa **`baku-db`**, mantener **`QUEUE_WORKERS=1`** (el singleton de DB no es thread-safe).

### Código mínimo

**`src/main.py`** (productor):

```python
from baku import api, queue

@api.post("/api/tareas/")
async def crear_tarea(req):
    task = req.json
    if not queue.send(task):
        raise Exception({"status": 500, "message": "No se pudo encolar"})
    return {"status": "queued"}
```

**`src/worker.py`** (consumidor):

```python
from baku import config, queue

config.register_signal_handlers()

@queue.consume(workers=1)
def process_task(task):
    # lógica de procesamiento
    pass
```

### Checklist para implementar en otro proyecto

1. Crear `main.py` (API) y `worker.py` (consumidor) en el mismo repo.
2. Dockerfile con `CMD ["python", "src/main.py"]`; el worker se sobreescribe en Compose con `command`.
3. Dos servicios en `docker-compose.yml` como arriba.
4. Gateway/nginx: proxy HTTP solo al servicio API (ej. `/api/export/` → `export-api:3000`).
5. Validar en el API lo que el cliente controla; el worker asume tareas bien formadas.
6. Publicar/instalar la versión de `baku-queue` que incluye `QUEUE_HOST` y bind `0.0.0.0` en `send()`.

## API de Referencia

### `queue.send(task, address=None, timeout=None)`

Envía una tarea a la cola ZeroMQ.

**Parámetros:**
- `task` (dict): Diccionario con la tarea a enviar. Debe ser serializable a JSON.
- `address` (str, opcional): Dirección ZeroMQ del broker (default: `QUEUE_ADDRESS`)
- `timeout` (int, opcional): Timeout en segundos (default: `QUEUE_TIMEOUT`)

**Retorna:**
- `bool`: `True` si la tarea se envió exitosamente, `False` en caso contrario

**Raises:**
- `ValueError`: Si la tarea no es un diccionario o no es serializable a JSON

**Ejemplo:**
```python
from baku import queue

task = {
    "task_type": "process",
    "data": {"id": 123}
}

if queue.send(task):
    print("Tarea enviada")
```

### `@queue.consume(address=None, timeout=None, retry_attempts=None, workers=None, prefetch=None)`

Decorador que consume tareas de la cola ZeroMQ de forma continua.

**Parámetros:**
- `address` (str, opcional): Dirección ZeroMQ del broker (default: `QUEUE_ADDRESS`)
- `timeout` (int, opcional): Timeout en segundos para recibir tareas (default: `QUEUE_TIMEOUT`)
- `retry_attempts` (int, opcional): Número de intentos de reintento (default: `QUEUE_RETRY_ATTEMPTS`)
- `workers` (int, opcional): Número de workers concurrentes (default: `QUEUE_WORKERS`)
- `prefetch` (int, opcional): Número de tareas a pre-cargar por worker (default: `QUEUE_PREFETCH`)

**Retorna:**
- `decorator`: Decorador que consume tareas de la cola

**Comportamiento:**
- El decorador bloquea el hilo principal ejecutando el loop de consumo
- El handler recibe la tarea deserializada como diccionario
- Si el handler lanza una excepción, se registra un error pero el consumo continúa
- Soporta múltiples workers concurrentes para procesamiento paralelo

**Ejemplo:**
```python
from baku import queue
import logging

@queue.consume(workers=4)
def process_task(task):
    logging.info(f"Procesando: {task['task_type']}")
    # Tu lógica aquí
    return {"status": "ok"}
```

## Ejemplos

### Ejemplo 1: Productor Simple

```python
from baku import queue
import logging

# Enviar múltiples tareas
tasks = [
    {"task_type": "process", "data": {"id": 1}},
    {"task_type": "process", "data": {"id": 2}},
    {"task_type": "process", "data": {"id": 3}},
]

for task in tasks:
    if queue.send(task):
        logging.info(f"Tarea {task['data']['id']} enviada")
    else:
        logging.error(f"Error al enviar tarea {task['data']['id']}")
```

### Ejemplo 2: Consumidor Simple

```python
from baku import queue
import logging

@queue.consume()
def process_task(task):
    """Procesa una tarea de la cola."""
    task_type = task.get('task_type')
    data = task.get('data', {})
    
    logging.info(f"Procesando tarea tipo: {task_type}")
    
    # Procesar según el tipo
    if task_type == "process":
        result = process_data(data)
        return {"status": "completed", "result": result}
    elif task_type == "cleanup":
        cleanup_resources(data)
        return {"status": "cleaned"}
    else:
        logging.warning(f"Tipo de tarea desconocido: {task_type}")
        return {"status": "unknown"}
```

### Ejemplo 3: Múltiples Workers

```python
from baku import queue
import logging

# Procesar con 4 workers concurrentes
@queue.consume(workers=4, prefetch=20)
def process_task(task):
    """Procesa tareas con múltiples workers."""
    logging.info(f"Worker procesando: {task['task_type']}")
    
    # Procesamiento que puede tomar tiempo
    result = heavy_processing(task)
    
    return {"status": "completed", "result": result}
```

### Ejemplo 4: Integración con baku-api

**servicio-api.py** (Productor):
```python
from baku import api
from baku import queue
import logging

@api.post("/api/tasks")
def create_task(req):
    """Recibe una petición HTTP y la envía a la cola."""
    data = req.json
    
    task = {
        "task_type": "process",
        "data": data,
        "timestamp": time.time()
    }
    
    if queue.send(task):
        logging.info("Tarea enviada a la cola")
        return {"status": "queued", "message": "Tarea en cola para procesamiento"}
    else:
        return {"status": "error", "message": "Error al enviar tarea"}, 500
```

**servicio-worker.py** (Consumidor):
```python
from baku import queue
import logging

@queue.consume(workers=2)
def process_task(task):
    """Procesa tareas de la cola."""
    logging.info(f"Procesando tarea: {task['task_type']}")
    
    data = task.get('data', {})
    # Procesar datos
    result = process_data(data)
    
    return {"status": "completed", "result": result}
```

## Arquitectura

### Patrón Producer/Consumer

El módulo implementa el patrón Producer/Consumer usando ZeroMQ:

- **Productor**: Usa socket `PUSH` para enviar tareas
- **Consumidor**: Usa socket `PULL` para recibir tareas
- **Balanceo de carga**: ZeroMQ distribuye automáticamente las tareas entre workers

### Flujo de Trabajo

```
Productor → queue.send() → ZeroMQ PUSH → Broker → ZeroMQ PULL → queue.consume() → Worker
```

### Características

- **Thread-safe**: `queue.send()` puede ser llamada desde múltiples hilos
- **Serialización JSON**: Las tareas se serializan automáticamente a JSON
- **Múltiples workers**: Soporta procesamiento paralelo con múltiples workers
- **Prefetch**: Controla cuántas tareas se pre-cargan por worker
- **Timeout**: Configurable para evitar bloqueos
- **Logging**: Integrado con `baku-config` para logging estandarizado

## Consideraciones

### Diseño para Microservicios

El módulo está diseñado para microservicios simples:
- Un solo consumidor por servicio (aunque puede tener múltiples workers)
- Tareas serializables a JSON
- Sin persistencia de cola (usa ZeroMQ en memoria)

### Escalabilidad

Para escalar:
- **Horizontal**: Ejecutar múltiples instancias del consumidor
- **Vertical**: Aumentar `QUEUE_WORKERS` para más workers por instancia
- **Prefetch**: Ajustar `QUEUE_PREFETCH` según la carga

### Resiliencia

- Si un worker falla, otros workers continúan procesando
- Las tareas no procesadas se pierden si el consumidor se detiene (ZeroMQ no persiste)
- Para persistencia, considerar usar un broker externo (RabbitMQ, Redis, etc.)

### Integración con baku-api

`baku-queue` es completamente independiente de `baku-api`:
- Puede usarse sin HTTP
- Puede integrarse con cualquier productor
- Mismo módulo para productores y consumidores

## Troubleshooting

### Error: "Dirección ZeroMQ inválida"

- Verifica que `QUEUE_ADDRESS` tenga el formato correcto: `tcp://host:port`
- Asegúrate de que el broker esté ejecutándose
- Para TCP, verifica que el puerto esté disponible

### Las tareas no se procesan

1. Verifica que el consumidor esté ejecutándose
2. Verifica que `QUEUE_ADDRESS` sea la misma en productor y consumidor
3. Revisa los logs para errores de conexión
4. Verifica que las tareas se estén enviando correctamente

### Error de serialización JSON

- Asegúrate de que las tareas sean diccionarios
- Verifica que todos los valores sean serializables a JSON
- No uses objetos complejos (clases, funciones, etc.)

### Workers no procesan tareas

- Verifica que `QUEUE_WORKERS` esté configurado correctamente
- Revisa los logs para errores en los workers
- Asegúrate de que el handler no esté bloqueando indefinidamente

## Diferencia con baku-jobs

- **`baku-jobs`**: Tareas programadas por tiempo (cron-like, ejecución periódica)
- **`baku-queue`**: Tareas asíncronas en cola (event-driven, procesamiento bajo demanda)

Ambos pueden usarse juntos:
- `baku-jobs` para tareas periódicas (limpieza, reportes, etc.)
- `baku-queue` para procesamiento asíncrono (tareas generadas por eventos HTTP, webhooks, etc.)

