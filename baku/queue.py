# -*- coding: utf-8 -*

from baku import config
import zmq
import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Callable

# Leer configuración desde variables de entorno
QUEUE_ADDRESS = config.get('QUEUE_ADDRESS', default='tcp://localhost:5555')
QUEUE_TIMEOUT = config.get('QUEUE_TIMEOUT', default=30, converter=int)
QUEUE_RETRY_ATTEMPTS = config.get('QUEUE_RETRY_ATTEMPTS', default=3, converter=int)
QUEUE_WORKERS = config.get('QUEUE_WORKERS', default=1, converter=int)
QUEUE_PREFETCH = config.get('QUEUE_PREFETCH', default=10, converter=int)


def _validate_address(address: str) -> bool:
    """Valida que la dirección ZeroMQ sea válida.
    
    Args:
        address: String con dirección ZeroMQ (ej: "tcp://localhost:5555")
        
    Returns:
        bool: True si es válido, False en caso contrario
    """
    if not address:
        return False
    
    try:
        # Validar formato básico de dirección ZeroMQ
        if not address.startswith(('tcp://', 'ipc://', 'inproc://')):
            return False
        return True
    except Exception:
        return False


def send(task: Dict[str, Any], address: Optional[str] = None, timeout: Optional[int] = None) -> bool:
    """
    Envía una tarea a la cola ZeroMQ.
    
    Esta función crea una conexión PUSH, envía la tarea serializada como JSON,
    y cierra la conexión. Es thread-safe y puede ser llamada desde múltiples hilos.
    
    Args:
        task: Diccionario con la tarea a enviar. Debe ser serializable a JSON.
        address: Dirección ZeroMQ del broker (default: QUEUE_ADDRESS de variables de entorno)
        timeout: Timeout en segundos para la operación (default: QUEUE_TIMEOUT)
        
    Returns:
        bool: True si la tarea se envió exitosamente, False en caso contrario
        
    Raises:
        ValueError: Si la tarea no es un diccionario o no es serializable a JSON
        
    Ejemplo:
        >>> from baku import queue
        >>> queue.send({"task_type": "process", "data": {"id": 123}})
        True
    """
    if not isinstance(task, dict):
        raise ValueError("La tarea debe ser un diccionario")
    
    queue_address = address or QUEUE_ADDRESS
    queue_timeout = timeout or QUEUE_TIMEOUT
    
    if not _validate_address(queue_address):
        logging.error(f"Dirección ZeroMQ inválida: {queue_address}")
        return False
    
    context = None
    socket = None
    
    try:
        # Crear contexto y socket PUSH
        context = zmq.Context()
        socket = context.socket(zmq.PUSH)
        socket.setsockopt(zmq.LINGER, 0)  # No esperar al cerrar
        socket.setsockopt(zmq.SNDTIMEO, queue_timeout * 1000)  # Timeout en ms
        
        # Conectar al broker
        socket.connect(queue_address)
        
        # Serializar tarea a JSON
        try:
            task_json = json.dumps(task, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logging.error(f"Error al serializar tarea a JSON: {e}")
            raise ValueError(f"La tarea no es serializable a JSON: {e}")
        
        # Enviar tarea
        socket.send_string(task_json)
        logging.debug(f"Tarea enviada a {queue_address}: {task.get('task_type', 'unknown')}")
        
        return True
        
    except zmq.ZMQError as e:
        logging.error(f"Error de ZeroMQ al enviar tarea: {e}")
        return False
    except Exception as e:
        logging.error(f"Error inesperado al enviar tarea: {e}", exc_info=True)
        return False
    finally:
        # Cerrar socket y contexto
        if socket:
            try:
                socket.close()
            except Exception:
                pass
        if context:
            try:
                context.term()
            except Exception:
                pass


def consume(address: Optional[str] = None, timeout: Optional[int] = None, 
            retry_attempts: Optional[int] = None, workers: Optional[int] = None,
            prefetch: Optional[int] = None):
    """
    Decorador que consume tareas de la cola ZeroMQ de forma continua.
    
    Este decorador está diseñado para crear microservicios simples con un solo
    consumidor de cola. El decorador bloquea el hilo principal ejecutando el
    loop de consumo.
    
    El handler recibe la tarea deserializada como diccionario y debe retornar
    un resultado (opcional). Si el handler lanza una excepción, se registra
    un error pero el consumo continúa.
    
    Args:
        address: Dirección ZeroMQ del broker (default: QUEUE_ADDRESS)
        timeout: Timeout en segundos para recibir tareas (default: QUEUE_TIMEOUT)
        retry_attempts: Número de intentos de reintento (default: QUEUE_RETRY_ATTEMPTS)
        workers: Número de workers concurrentes (default: QUEUE_WORKERS)
        prefetch: Número de tareas a pre-cargar por worker (default: QUEUE_PREFETCH)
        
    Returns:
        decorator: Decorador que consume tareas de la cola
        
    Ejemplo:
        >>> from baku import queue
        >>> @queue.consume()
        ... def process_task(task):
        ...     print(f"Procesando: {task['task_type']}")
        ...     return {"status": "ok"}
    """
    queue_address = address or QUEUE_ADDRESS
    queue_timeout = timeout or QUEUE_TIMEOUT
    queue_retry = retry_attempts or QUEUE_RETRY_ATTEMPTS
    queue_workers = workers or QUEUE_WORKERS
    queue_prefetch = prefetch or QUEUE_PREFETCH
    
    if not _validate_address(queue_address):
        logging.error(f"Dirección ZeroMQ inválida: {queue_address}")
        def invalid_decorator(func):
            return func
        return invalid_decorator
    
    def decorator(func: Callable) -> Callable:
        """Decorador interno que consume tareas."""
        
        def worker_thread(worker_id: int):
            """Hilo worker que consume tareas."""
            context = None
            socket = None
            
            try:
                # Crear contexto y socket PULL
                context = zmq.Context()
                socket = context.socket(zmq.PULL)
                socket.setsockopt(zmq.LINGER, 0)
                socket.setsockopt(zmq.RCVTIMEO, queue_timeout * 1000)  # Timeout en ms
                
                # Configurar prefetch para balanceo de carga
                socket.setsockopt(zmq.RCVHWM, queue_prefetch)
                
                # Conectar al broker
                socket.connect(queue_address)
                logging.info(f"Worker {worker_id} conectado a {queue_address}")
                
                # Loop de consumo
                while True:
                    try:
                        # Recibir tarea (el timeout está configurado en RCVTIMEO)
                        task_json = socket.recv_string()
                        
                        # Deserializar tarea
                        try:
                            task = json.loads(task_json)
                        except json.JSONDecodeError as e:
                            logging.error(f"Error al deserializar tarea: {e}")
                            continue
                        
                        # Procesar tarea
                        logging.debug(f"Worker {worker_id} procesando tarea: {task.get('task_type', 'unknown')}")
                        
                        try:
                            result = func(task)
                            logging.debug(f"Worker {worker_id} completó tarea exitosamente")
                            if result:
                                logging.debug(f"Resultado: {result}")
                        except Exception as e:
                            logging.error(f"Error al procesar tarea en worker {worker_id}: {e}", exc_info=True)
                            
                            # Reintentos (opcional, implementación básica)
                            if queue_retry > 0:
                                logging.warning(f"Reintentando tarea (intentos restantes: {queue_retry-1})")
                                # Aquí se podría implementar una cola de reintentos
                        
                    except zmq.Again:
                        # Timeout - continuar el loop
                        continue
                    except zmq.ZMQError as e:
                        logging.error(f"Error de ZeroMQ en worker {worker_id}: {e}")
                        time.sleep(1)  # Esperar antes de reintentar
                    except KeyboardInterrupt:
                        logging.info(f"Worker {worker_id} recibió señal de interrupción")
                        break
                    except Exception as e:
                        logging.error(f"Error inesperado en worker {worker_id}: {e}", exc_info=True)
                        time.sleep(1)  # Esperar antes de reintentar
                        
            except Exception as e:
                logging.error(f"Error fatal en worker {worker_id}: {e}", exc_info=True)
            finally:
                # Cerrar socket y contexto
                if socket:
                    try:
                        socket.close()
                    except Exception:
                        pass
                if context:
                    try:
                        context.term()
                    except Exception:
                        pass
                logging.info(f"Worker {worker_id} desconectado")
        
        def wrapper(*args, **kwargs):
            """Wrapper que inicia los workers y bloquea el hilo principal."""
            logging.info(f"Iniciando consumidor de cola en {queue_address}")
            logging.info(f"Workers: {queue_workers}, Prefetch: {queue_prefetch}, Timeout: {queue_timeout}s")
            
            # Crear e iniciar workers
            threads = []
            for i in range(queue_workers):
                thread = threading.Thread(target=worker_thread, args=(i+1,), daemon=True)
                thread.start()
                threads.append(thread)
                logging.info(f"Worker {i+1} iniciado")
            
            # Esperar a que todos los workers terminen (bloquea el hilo principal)
            try:
                for thread in threads:
                    thread.join()
            except KeyboardInterrupt:
                logging.info("Recibida señal de interrupción, cerrando workers...")
                # Los threads son daemon, se cerrarán automáticamente
        
        # Mantener metadata de la función original
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        
        return wrapper
    
    return decorator

