# baku-queue para Servicios MCP de Lastseal en la Nube

Este documento describe cómo usar `baku-queue` en servicios MCP (Model Context Protocol) desplegados en Lastseal en la Nube para procesamiento asíncrono de tareas mediante colas de trabajo.

## Contexto

Los servicios MCP de Lastseal en la Nube frecuentemente requieren procesar tareas de forma asíncrona:
- Procesamiento de datos pesados sin bloquear endpoints HTTP
- Procesamiento de webhooks y eventos
- Tareas de transformación de datos
- Generación de reportes y documentos
- Integración con sistemas externos que requieren tiempo de procesamiento

El módulo `baku-queue` proporciona funciones y decoradores para enviar y consumir tareas en colas usando ZeroMQ, permitiendo desacoplar la recepción de trabajo (por ejemplo, desde `baku-api`) de su procesamiento.

## Instalación en Servicios MCP

### Requisitos

- Python 3.9 o superior
- Acceso al registry de GitHub Packages de Lastseal
- ZeroMQ instalado en el sistema (o usar `pyzmq` que lo incluye)

### Instalación

```bash
# Configurar pip para usar GitHub Packages
pip install --upgrade pip
pip install keyring artifacts-keyring

# Instalar baku-queue
pip install baku-queue \
  --index-url https://pypi.org/simple \
  --extra-index-url https://lastseal:TOKEN@pkg.github.com
```

O desde el repositorio:

```bash
pip install git+https://github.com/lastseal/baku-queue.git
```

## Uso en Servicios MCP

### Configuración Básica

```python
# Al inicio del servicio MCP
from baku import queue
import logging

# El módulo automáticamente consume tareas al usar el decorador
@queue.consume()
def process_task(task):
    logging.info(f"Procesando tarea: {task['task_type']}")
    # Tu lógica de procesamiento aquí
    return {"status": "completed"}
```

### Variables de Entorno

Los servicios MCP deben definir las siguientes variables de entorno (en `.env` o en el entorno de despliegue):

```bash
# Dirección ZeroMQ del broker (requerido)
QUEUE_ADDRESS=tcp://localhost:5555

# Timeout en segundos para operaciones (opcional, default: 30)
QUEUE_TIMEOUT=30

# Número de intentos de reintento (opcional, default: 3)
QUEUE_RETRY_ATTEMPTS=3

# Número de workers concurrentes (opcional, default: 1)
QUEUE_WORKERS=1

# Número de tareas a pre-cargar por worker (opcional, default: 10)
QUEUE_PREFETCH=10
```

### Formatos de Dirección ZeroMQ

El módulo soporta diferentes tipos de direcciones ZeroMQ:

- **TCP**: `tcp://localhost:5555` o `tcp://192.168.1.100:5555` (para comunicación entre procesos/máquinas)
- **IPC**: `ipc:///tmp/queue.sock` (solo Unix/Linux, para comunicación entre procesos en la misma máquina)
- **In-Process**: `inproc://queue` (solo dentro del mismo proceso, para testing)

## Ejemplos de Uso en Servicios MCP

### Ejemplo 1: Productor Simple

```python
from baku import queue
import logging

# Enviar una tarea a la cola
task = {
    "task_type": "process_data",
    "data": {"id": 123, "name": "Example"},
    "priority": "high"
}

if queue.send(task):
    logging.info("Tarea enviada exitosamente")
else:
    logging.error("Error al enviar tarea")
```

Con variable de entorno:
```bash
QUEUE_ADDRESS=tcp://localhost:5555
```

### Ejemplo 2: Consumidor Simple

```python
from baku import queue
import logging

@queue.consume()
def process_task(task):
    """Procesa una tarea de la cola."""
    logging.info(f"Procesando tarea: {task['task_type']}")
    
    task_type = task.get('task_type')
    data = task.get('data', {})
    
    if task_type == "process_data":
        # Procesar datos
        result = process_data(data)
        return {"status": "completed", "result": result}
    
    return {"status": "ok"}
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

Con variable de entorno:
```bash
QUEUE_ADDRESS=tcp://localhost:5555
QUEUE_WORKERS=4
QUEUE_PREFETCH=20
```

### Ejemplo 4: Integración con baku-api

**servicio-api.py** (Productor):
```python
from baku import config  # Configura logging y variables de entorno
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
from baku import config  # Configura logging y variables de entorno
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

### Ejemplo 5: Procesamiento de Webhooks

```python
from baku import api
from baku import queue
import logging

@api.post("/api/webhook")
def webhook_handler(req):
    """Recibe webhook y lo envía a la cola para procesamiento asíncrono."""
    webhook_data = req.json
    
    task = {
        "task_type": "webhook",
        "source": webhook_data.get('source'),
        "data": webhook_data,
        "timestamp": time.time()
    }
    
    if queue.send(task):
        return {"status": "received", "message": "Webhook en cola"}
    else:
        return {"status": "error"}, 500

# En otro servicio/worker
@queue.consume()
def process_webhook(task):
    """Procesa webhooks de la cola."""
    logging.info(f"Procesando webhook de {task['source']}")
    
    # Procesar webhook (puede tomar tiempo)
    process_webhook_data(task['data'])
    
    return {"status": "processed"}
```

## Consideraciones para Lastseal en la Nube

### Variables de Entorno en Producción

En Lastseal en la Nube, las variables de entorno se configuran a través del panel de control o archivos de configuración del servicio. El módulo `baku-queue` las leerá automáticamente a través de `baku-config`.

**Recomendaciones:**
- Usar direcciones TCP para comunicación entre servicios (`tcp://host:port`)
- Configurar `QUEUE_WORKERS` según la carga esperada (típicamente 2-4 workers por CPU)
- Ajustar `QUEUE_PREFETCH` según el tamaño de las tareas (más prefetch para tareas pequeñas)
- Usar timeouts apropiados según el tiempo de procesamiento esperado

### Logging en la Nube

El módulo `baku-queue` usa el logging estándar de Python. Asegúrate de que `baku-config` esté configurado para logging consistente:

```python
from baku import config  # Configura logging automáticamente
from baku import queue
import logging

@queue.consume()
def process_task(task):
    logging.info("Tarea procesada")  # Usa el logging configurado por baku-config
    return {"status": "ok"}
```

### Arquitectura de Colas

**Patrón Producer/Consumer:**

```
Cliente HTTP → baku-api (endpoint) → queue.send() → ZeroMQ PUSH → Broker
                                                              ↓
                                                      ZeroMQ PULL → queue.consume() → Workers
```

**Escalabilidad:**

- **Horizontal**: Ejecutar múltiples instancias del consumidor (cada una con sus workers)
- **Vertical**: Aumentar `QUEUE_WORKERS` para más workers por instancia
- **Balanceo**: ZeroMQ distribuye automáticamente las tareas entre workers

### Resiliencia y Manejo de Errores

- **Errores en procesamiento**: Si un worker falla al procesar una tarea, se registra un error pero el consumo continúa
- **Reintentos**: Configurable mediante `QUEUE_RETRY_ATTEMPTS` (implementación básica)
- **Pérdida de tareas**: ZeroMQ no persiste tareas en disco por defecto. Si el consumidor se detiene, las tareas no procesadas se pierden
- **Para persistencia**: Considerar usar un broker externo (RabbitMQ, Redis, etc.) o implementar persistencia personalizada

### Integración con Otros Servicios

`baku-queue` es completamente independiente de `baku-api`:
- Puede usarse sin HTTP (scripts, otros servicios)
- Puede integrarse con cualquier productor
- Mismo módulo para productores y consumidores

### Performance y Recursos

- **Threads**: Cada worker es un thread separado
- **Memoria**: El consumo de memoria depende del número de workers y prefetch
- **CPU**: Los workers comparten el GIL de Python, pero ZeroMQ maneja I/O eficientemente
- **Red**: ZeroMQ es eficiente en el uso de red, minimizando overhead

### Integración con PM2 / Systemd

El decorador `@queue.consume()` bloquea el hilo principal, lo cual es compatible con PM2 y systemd:

```python
# servicio-worker.py
from baku import queue

@queue.consume(workers=4)
def process_task(task):
    # Tu lógica aquí
    return {"status": "ok"}

# PM2/systemd ejecutará este script y el decorador manejará el loop
```

## Referencia de API

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

## Troubleshooting

### Error: "Dirección ZeroMQ inválida"

- Verifica que `QUEUE_ADDRESS` tenga el formato correcto: `tcp://host:port`
- Asegúrate de que el broker esté ejecutándose (si usas un broker externo)
- Para TCP, verifica que el puerto esté disponible y accesible

### Las tareas no se procesan

1. **Verificar que el consumidor esté ejecutándose:**
   ```bash
   ps aux | grep python
   ```

2. **Verificar que `QUEUE_ADDRESS` sea la misma en productor y consumidor:**
   ```bash
   echo $QUEUE_ADDRESS
   ```

3. **Revisar logs para errores de conexión:**
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

4. **Verificar que las tareas se estén enviando correctamente:**
   ```python
   if queue.send(task):
       print("Tarea enviada")
   else:
       print("Error al enviar")
   ```

### Error de serialización JSON

- Asegúrate de que las tareas sean diccionarios
- Verifica que todos los valores sean serializables a JSON (no uses objetos complejos, clases, funciones, etc.)
- Ejemplo de tarea válida:
  ```python
  task = {
      "task_type": "process",
      "data": {"id": 123, "name": "Example"},  # ✅ Válido
      "timestamp": 1234567890.0  # ✅ Válido
  }
  ```

### Workers no procesan tareas

1. **Verificar que `QUEUE_WORKERS` esté configurado correctamente:**
   ```bash
   echo $QUEUE_WORKERS
   ```

2. **Revisar logs para errores en los workers:**
   - Los errores se registran con el ID del worker
   - Verifica que no haya excepciones no capturadas

3. **Asegúrate de que el handler no esté bloqueando indefinidamente:**
   - Si el handler tiene operaciones bloqueantes, considera usar timeouts
   - Verifica que no haya deadlocks o condiciones de carrera

### El consumidor se detiene inesperadamente

- Revisa los logs para errores fatales
- Verifica que el handler no esté lanzando excepciones no capturadas
- Asegúrate de que ZeroMQ esté instalado correctamente
- Verifica que la conexión al broker sea estable

### Problemas de rendimiento

- **Aumenta `QUEUE_WORKERS`** para más procesamiento paralelo
- **Ajusta `QUEUE_PREFETCH`** según el tamaño de las tareas
- **Verifica el timeout** - si es muy corto, puede causar timeouts innecesarios
- **Considera usar IPC** en lugar de TCP si productor y consumidor están en la misma máquina

## Ejemplo Completo de Servicio MCP

```python
#!/usr/bin/env python3
"""Servicio MCP de ejemplo usando baku-queue para procesamiento asíncrono."""

from baku import config  # Configura logging y variables de entorno
from baku import queue
import logging

# El módulo baku-config ya configuró logging y cargó .env automáticamente

@queue.consume(workers=2)
def process_task(task):
    """Procesa tareas de la cola."""
    task_type = task.get('task_type')
    data = task.get('data', {})
    
    logging.info(f"Procesando tarea tipo: {task_type}")
    
    # Obtener configuración
    max_retries = config.get('MAX_RETRIES', default=3, converter=int)
    
    # Procesar según el tipo
    if task_type == "process_data":
        result = process_data(data)
        return {"status": "completed", "result": result}
    elif task_type == "generate_report":
        report = generate_report(data)
        return {"status": "completed", "report": report}
    else:
        logging.warning(f"Tipo de tarea desconocido: {task_type}")
        return {"status": "unknown"}

# El decorador @queue.consume() bloquea el hilo principal ejecutando el loop
# No necesitas llamar a la función explícitamente
```

Con variables de entorno:
```bash
# Configuración de baku-config
LOG_LEVEL=INFO

# Configuración de baku-queue
QUEUE_ADDRESS=tcp://localhost:5555
QUEUE_WORKERS=2
QUEUE_PREFETCH=10
QUEUE_TIMEOUT=30
QUEUE_RETRY_ATTEMPTS=3

# Variables específicas del servicio
MAX_RETRIES=3
```

## Notas Importantes

- El módulo está diseñado para **microservicios simples** con procesamiento asíncrono
- El decorador **bloquea el hilo principal** con un loop infinito (comportamiento esperado)
- **ZeroMQ no persiste tareas** - las tareas no procesadas se pierden si el consumidor se detiene
- Para **persistencia**, considerar usar un broker externo (RabbitMQ, Redis, etc.)
- El módulo es **completamente independiente** de `baku-api` y puede usarse sin HTTP
- Los **threads** se usan para permitir múltiples workers procesando tareas en paralelo
- El módulo es compatible con `baku-config` para logging y configuración estandarizada

## Diferencia con baku-jobs

- **`baku-jobs`**: Tareas programadas por tiempo (cron-like, ejecución periódica)
- **`baku-queue`**: Tareas asíncronas en cola (event-driven, procesamiento bajo demanda)

Ambos pueden usarse juntos:
- `baku-jobs` para tareas periódicas (limpieza, reportes, sincronización)
- `baku-queue` para procesamiento asíncrono (tareas generadas por eventos HTTP, webhooks, etc.)

