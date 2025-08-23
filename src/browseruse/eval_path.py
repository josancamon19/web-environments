import asyncio
import logging
import os
from dotenv import load_dotenv
from pathlib import Path
from browser_use import Agent, ChatOpenAI
from datetime import datetime

# Cargar las variables de entorno
load_dotenv()

# Crear una carpeta para los logs si no existe
log_dir = "src/browseruse/logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configuración del archivo de log para el manejo de logs
log_file_path = os.path.join(log_dir, f"agent_output_eval_path_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")

# Define el FileLoggingHandler para los logs
class FileLoggingHandler(logging.Handler):
    """Un controlador de logging que escribe los logs en un archivo."""

    def __init__(self, file_path: Path) -> None:
        """Inicializa el handler de logging."""
        super().__init__()
        self.log_file_path = file_path
        self.log_file = None  # Inicializa log_file como None

    def emit(self, record: logging.LogRecord) -> None:
        """Escribir el log en el archivo de log."""
        if self.log_file is None:
            self.log_file = open(self.log_file_path, mode="a", encoding="utf-8")
        log_entry = self.format(record)
        self.log_file.write(log_entry + "\n")
        self.log_file.flush()

    def close(self) -> None:
        """Cerrar el archivo de log."""
        if self.log_file:
            self.log_file.close()
        super().close()

# Configuración de logging con timestamp
formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", 
    "%Y-%m-%d %H:%M:%S"
)

# Crear el handler de logging que guarda los logs en el archivo
handler = FileLoggingHandler(log_file_path)
handler.setFormatter(formatter)

# Crear un logger y agregar el handler
logger = logging.getLogger('browser_use')
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# Inicializar el modelo
llm = ChatOpenAI(
    model='gpt-4.1-mini',  # Puedes ajustar el modelo si lo necesitas
    temperature=0.0,
)

async def main():
    task = input("Ingrese la tarea para el agente: ")
    
    # Crear el agente
    agent = Agent(
        task=task,  # Usar la tarea proporcionada por input()
        llm=llm,  # Usa el modelo apropiado
        verbose=True,  # Activar el modo verbose para ver las interacciones
    )

    # Registrar el inicio de la tarea
    logger.info("Iniciando la tarea del agente.")
    
    # Ejecutar la tarea del agente
    await agent.run()

    # Registrar el final de la tarea
    logger.info("Tarea completada. Resultado guardado en el archivo de salida.")

    print(f"Salida guardada en {log_file_path}")

# Ejecutar el agente
asyncio.run(main())
