# -*- coding: utf-8 -*

from typing import Any, Dict, Optional, Callable
from baku import config
import zmq
import json
import logging
import re
import threading
import time
import signal

# Leer configuración desde variables de entorno
QUEUE_PORT = config.get('QUEUE_PORT', default=5555)
QUEUE_TIMEOUT = config.get('QUEUE_TIMEOUT', default=30, converter=int)
QUEUE_RETRY_ATTEMPTS = config.get('QUEUE_RETRY_ATTEMPTS', default=3, converter=int)
QUEUE_WORKERS = config.get('QUEUE_WORKERS', default=1, converter=int)
QUEUE_PREFETCH = config.get('QUEUE_PREFETCH', default=10, converter=int)

# Solo un PUSH puede enlazar el puerto a la vez (send corto: bind → send → cerrar)
_send_bind_lock = threading.Lock()

def send(task: Dict[str, Any], timeout: Optional[int] = None) -> bool:
    """
    Envía una tarea a la cola ZeroMQ.
    
    Crea un socket PUSH, hace bind en el endpoint de envío, envía la tarea (JSON)
    y cierra. Los consumidores deben usar connect a la misma QUEUE_ADDRESS (host:puerto).
    Varios hilos: serializado con un lock porque solo un bind puede ocupar el puerto.
    
    Args:
        task: Diccionario con la tarea a enviar. Debe ser serializable a JSON.
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
    
    queue_timeout = timeout or QUEUE_TIMEOUT
        
    bind_address = f"tcp://*:{QUEUE_PORT}"

    with _send_bind_lock:
        context = None
        socket = None

        try:
            context = zmq.Context()
            socket = context.socket(zmq.PUSH)
            socket.setsockopt(zmq.LINGER, 0)
            socket.setsockopt(zmq.SNDTIMEO, queue_timeout * 1000)
            socket.bind(bind_address)

            socket.send_json(task)

            logging.debug(f"Tarea enviada (bind {bind_address}): {task}")

            return True

        except zmq.ZMQError as e:
            logging.error(f"Error de ZeroMQ al enviar tarea: {e}")
            return False
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error inesperado al enviar tarea: {e}", exc_info=True)
            return False
        finally:
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


threads = []

def consume(timeout: Optional[int] = None, 
            retry_attempts: Optional[int] = None, 
            workers: Optional[int] = None,
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
        address: Dirección a la que este PULL hace connect; el productor PUSH hace bind
            en el mismo puerto (default: QUEUE_ADDRESS)
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
    queue_address = f"tcp://localhost:{QUEUE_PORT}"
    queue_timeout = timeout or QUEUE_TIMEOUT
    queue_retry = retry_attempts or QUEUE_RETRY_ATTEMPTS
    queue_workers = workers or QUEUE_WORKERS
    queue_prefetch = prefetch or QUEUE_PREFETCH

    logging.debug("queue_address: %s, queue_timeout: %s, queue_retry: %s, queue_workers: %s, queue_prefetch: %s", 
        queue_address, 
        queue_timeout, 
        queue_retry, 
        queue_workers, 
        queue_prefetch)
        
    def decorator(func: Callable) -> Callable:
        """Decorador interno que consume tareas."""

        logging.debug("decorator: %s", func)
        
        def worker_thread(worker_id: int):
            """Hilo worker que consume tareas."""
            context = None
            socket = None

            logging.debug("connecting to %s", queue_address)
            
            try:
                # Crear contexto y socket PULL
                context = zmq.Context()
                socket = context.socket(zmq.PULL)
                socket.setsockopt(zmq.LINGER, 0)
                socket.setsockopt(zmq.RCVTIMEO, queue_timeout * 1000)  # Timeout en ms
                
                # Configurar prefetch para balanceo de carga
                socket.setsockopt(zmq.RCVHWM, queue_prefetch)
                
                socket.connect(queue_address)

                logging.info(
                    "Worker %s conectado a %s (PUSH bind en el puerto de esta dirección)",
                    worker_id,
                    queue_address,
                )
                
                # Loop de consumo
                while True:
                    try:
                        # Recibir tarea (el timeout está configurado en RCVTIMEO)
                        task = socket.recv_json()
                        
                        # Procesar tarea
                        logging.debug(f"Worker {worker_id} procesando tarea: {task}")
                        
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
        
        """Wrapper que inicia los workers y bloquea el hilo principal."""
        logging.info(f"Iniciando consumidor de cola en {queue_address}")
        logging.info(f"Workers: {queue_workers}, Prefetch: {queue_prefetch}, Timeout: {queue_timeout}s")
        
        # Crear e iniciar workers
        
        for i in range(queue_workers):
            thread = threading.Thread(target=worker_thread, args=(i+1,), daemon=True)
            thread.start()
            threads.append(thread)
            logging.info(f"Worker {i+1} iniciado")
            
    return decorator

def handle_sigint_sigterm(signum, frame):
    logging.info("SIGINT received, closing workers")
    for thread in threads:
        thread.stop()
        thread.join()
    logging.info("all workers joined")

signal.signal(signal.SIGINT,  handle_sigint_sigterm)
signal.signal(signal.SIGTERM, handle_sigint_sigterm)
